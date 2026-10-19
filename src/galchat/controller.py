from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
import hashlib
import queue
import threading
import time
from pathlib import Path

from .engine import ReplyEngine
from .activity import ActivityPaused
from .memory import MemoryStore
from .settings import Settings
from .wechat import WeChatAdapter
from .visual_wechat import VisualWeChatAdapter
from .adaptive_visual import AdaptiveVisualAdapter
from .contact_database import ContactDatabase
from .contact_preferences import ContactPreferences
from .current_chat import CurrentChatAdapter
from .reply_batches import ReplyBatches, BurstSchedule, incoming_append_only
from .conversation_identity import is_group
from .api_usage import ApiUsageLog
from .media_selection import automatic_visuals, visual_enrichment_only
from .model_availability import ModelAvailability
from .window_group import WindowGroup
from contextlib import nullcontext
from .attachments import readiness
from .media import LocalMedia, CloudMedia, data_url
from .multimodal import MAX_VISUALS, MAX_VISUAL_BYTES, validate_visual_url, first_frame_visual
from .wechat import _value

SCOPED_OPERATIONS = {'generate', 'choose', 'get_sticker', 'choose_fill', 'fill_draft', 'load_history',
    'transcribe_voice', 'refresh_voice_text', 'retry_latest_image', 'attachment_preview',
    'analyze_attachment', 'supply_attachment_text'}


class Controller:
    def __init__(self, root: Path, data_directory: Path, *, start_worker: bool = True):
        self.root = root
        self.settings = Settings(data_directory)
        self.api_usage = ApiUsageLog(data_directory)
        self.memory = MemoryStore(data_directory / "memory.sqlite3")
        self.engine = ReplyEngine(usage_observer=self.api_usage.record)
        self.contact_preferences = ContactPreferences(data_directory)
        self.database = ContactDatabase(root, data_directory)
        self.local_media = LocalMedia(self.database)
        self.cloud_media = CloudMedia()
        self._media_attempted = set()
        self._image_job = None
        self._image_slots = threading.BoundedSemaphore(2)
        self._media_batch = None
        self.model_availability = ModelAvailability()
        self._voice_job = None
        self._voice_attempts = {}
        self._voice_defer_until = 0
        self._image_attempts = {}
        self._visual_inputs = {}  # Approved pixels stay in RAM, never in chat text.
        self.current_adapter = None
        self.window_group = None
        self._desktop_runtime = start_worker
        self._activity_active = None
        self._activity_epoch = 0
        self._background_event = threading.Event()
        self._needs_fresh_snapshot = start_worker
        self._resume_ready = None
        self._interrupted_signature = None
        self._running_signature = None
        self._operation_activity = threading.local()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.commands: queue.Queue = queue.Queue()
        self.adapter = None
        self.uia_adapter = None
        self.visual_adapter = None
        self.connected = False
        self.window = None
        self.revision = 0
        self.choice_revision = -1
        self.selected_revision = -1
        self._filled_draft = None  # Only a verified native write grants replacement ownership.
        self.generating = False
        self.pending_at = None
        self.intent = ""
        self.last_signature = None
        self.reply_batches = ReplyBatches()
        self.burst_schedule = BurstSchedule()
        self._prepared_context = None
        self._state_sequence = 0
        self.state = {
            "mode": "wechat", "paused": False,
            "activity": {"active": not start_worker, "needs_refresh": start_worker},
            "connection": {"status": "disconnected", "message": "打开微信，选中聊天后连接", "can_read": False, "can_fill": False},
            "contact": None, "messages": [], "choices": [], "draft": "",
            "generation": {"status": "idle" if self.settings.public()["api_ready"] else "unconfigured",
                           "error": None, "source": None, "model": self.settings.values["model"]},
            "history": {"count": 0, "coverage": "还没有读取聊天"},
            "generation_blocked_reason": None,
            "settings": self.settings.public(), "memories": [],
            "contact_preferences": None,
            "relationship_assessment": None,
            "chat_display_name": "",
            "attachment_status": None,
            "last_fill_result": None, "reply_batch": None,
        }
        self.worker = threading.Thread(target=self._worker, name="wechat-reader", daemon=True)
        self.database.activity_check = self._check_operation_activity
        self.local_media.activity_check = self._check_operation_activity
        if start_worker:
            self.worker.start()

    def _sync_activity(self):
        # Test fixtures have no native windows. A production reader fails closed
        # until the desktop window group can positively identify the foreground.
        work_state = getattr(self.window_group, 'work_state', None)
        visibility = work_state() if callable(work_state) else None
        active = (visibility['active'] if visibility is not None else
            bool(self.window_group.is_active()) if self.window_group is not None else not self._desktop_runtime)
        with self.lock:
            lost = self._background_event.is_set()
            self._background_event.clear()
            if (not active and self._activity_active is not False) or lost:
                if self._activity_active is not False:
                    self._resume_ready = (self.last_signature, deepcopy(self.state['choices']),
                        deepcopy(self.state['generation']), self._filled_draft) if (
                        self.state['generation'].get('status') == 'ready'
                        and self.pending_at is None and not self.generating) else None
                self._activity_epoch += 1
                if self.generating and self._running_signature is not None:
                    self._interrupted_signature = self._running_signature
                self._needs_fresh_snapshot = True
                self.pending_at = None
                self._invalidate_reply_batches(preserve_resume=True)
                self.choice_revision = self.selected_revision = -1
                self._cancel_voice_job()
                self._image_job = self._media_batch = None
                self.state['attachment_status'] = None
            self._activity_active = active
            self.state['activity'] = {'active': active, 'needs_refresh': self._needs_fresh_snapshot}
            if visibility is not None:
                self.state['activity']['reason'] = visibility['reason']
            return active

    def _require_active(self, epoch=None, *, fresh=True):
        if not self._sync_activity() or self.stop.is_set():
            raise ActivityPaused('已暂停后台操作，请回到微信或心动微信。')
        with self.lock:
            if epoch is not None and epoch != self._activity_epoch:
                raise ActivityPaused('已取消离开前的操作，请等待重新确认当前聊天。')
            if fresh and self._needs_fresh_snapshot:
                raise ActivityPaused('正在重新确认当前聊天，请稍候。')

    def _check_operation_activity(self):
        self._require_active(getattr(self._operation_activity, 'epoch', None), fresh=False)
        contact_id = getattr(self._operation_activity, 'contact_id', None)
        if contact_id is not None:
            self._require_assist(contact_id)

    def _assist_allowed(self, contact=None):
        if self.state['mode'] == 'demo':
            return True
        contact = contact if contact is not None else self.state.get('contact')
        return bool(contact and (not self.settings.values['ask_before_assist']
            or self.contact_preferences.get(contact['id'], contact)['assistance_enabled']))

    def _require_assist(self, contact_id=None):
        contact = self.state.get('contact')
        if not self._assist_allowed() or contact_id is not None and (not contact or contact['id'] != contact_id):
            raise ActivityPaused('此聊天尚未开启辅助，请先为当前联系人开启。')

    def _apply_disabled_contact(self, contact, snapshot=None):
        rows = deepcopy((snapshot or {}).get('messages', self.state['messages']
            if (self.state['contact'] or {}).get('id') == contact['id'] else []))
        for row in rows:
            row.pop('visual_input', None)
        signature = ('assistance_disabled', contact['id'], contact.get('name'), contact.get('gender'),
            CurrentChatAdapter._message_version(rows))
        if self.last_signature != signature:
            self.revision += 1
            self._invalidate_reply_batches()
        self.last_signature = signature
        self.pending_at = None
        self._cancel_voice_job()
        self._image_job = self._media_batch = None
        self._visual_inputs.clear()
        self.state.update(contact=contact, contact_preferences=self.contact_preferences.get(contact['id'], contact),
            chat_display_name=contact['name'], messages=rows, choices=[], memories=[], relationship_assessment=None,
            attachment_status=None, generation_blocked_reason=None, draft='', last_fill_result=None,
            generation={'status': 'contact_disabled', 'error': None},
            history={'count': len(rows), 'coverage': '已在本地识别；开启此聊天后才向模型提交内容。'})
        self.choice_revision = self.selected_revision = -1
        self.intent = ''

    def _poll_current(self, *, force=False):
        self._require_active(fresh=False)
        epoch = self._activity_epoch
        self._operation_activity.epoch = epoch
        self._operation_activity.contact_id = None
        poll = getattr(self.adapter, 'poll', None)
        snapshot = poll(force=force or self._needs_fresh_snapshot or self.last_signature is None) if callable(poll) else self.adapter.snapshot()
        self._require_active(epoch, fresh=False)
        with self.lock:
            if hasattr(self.adapter, 'monitor_stats'):
                self.state['observation'] = dict(self.adapter.monitor_stats)
            if snapshot is None:
                self.state['connection'] = self.adapter.status()
                return None
            resumed = self._needs_fresh_snapshot
            saved, self._resume_ready = self._resume_ready, None
            if resumed:
                self.last_signature = None
            self.state['connection'] = self.adapter.status()
            self._apply_snapshot(snapshot)
            self.connected = True
            self._needs_fresh_snapshot = False
            self.state['activity']['needs_refresh'] = False
            if resumed and saved and saved[0] == self.last_signature and self._assist_allowed():
                # An unchanged, already completed batch costs no second API call.
                self.state['choices'] = self.reply_batches.publish(self.state['contact']['id'],
                    self.revision, self.state['messages'], saved[1])
                self.state['reply_batch'] = self.reply_batches.describe(self.reply_batches.current, self.state['messages'])
                self.state['generation'] = saved[2]
                self.choice_revision = self.revision
                self.pending_at = None
                self.burst_schedule.clear()
                if saved[3] and saved[3][0] == self.state['contact']['id']:
                    self._filled_draft = (saved[3][0], self.revision, saved[3][2])
            if self._interrupted_signature == self._generation_signature():
                self.pending_at = None
                self.burst_schedule.clear()
                self.state['generation'] = {'status': 'interrupted', 'error': None}
        return snapshot

    def get_state(self) -> dict:
        self._sync_activity()
        with self.lock:
            result = deepcopy(self.state)
            self._state_sequence += 1
            result["state_sequence"] = self._state_sequence
            result["reply_epoch"] = self.reply_batches.epoch
            result["revision"] = self.revision
            result['waiting_for_change'] = self.pending_at is None and not self.generating
            contact = self.state.get('contact')
            person = self.contact_preferences.get(contact['id'], contact) if contact else {}
            asking = self.state['mode'] != 'demo' and self.settings.values['ask_before_assist']
            result['assistance'] = {'ask_each_contact': asking, 'enabled': self._assist_allowed(),
                'needs_choice': bool(contact and asking and not person.get('assistance_decided')),
                'decision': ('yes' if person.get('assistance_enabled') else 'no') if person.get('assistance_decided') else None}
            if self.window_group:
                result['window_group'] = {**self.window_group.diagnostic,
                    'thread_alive':bool(self.window_group.thread and self.window_group.thread.is_alive())}
            result["draft_stale"] = bool(self.state["draft"]) and self.selected_revision != self.revision
            result["can_fill_draft"] = (self.state["mode"] == "wechat" and bool(self.state["draft"])
                                        and self._assist_allowed()
                                        and self._activity_active and not self._needs_fresh_snapshot
                                        and self.selected_revision == self.revision
                                        and bool(self.state["connection"].get("can_fill")))
            ready = self.state["settings"]["api_ready"]
            result["model_status"] = {"status": "configured" if ready else "unconfigured",
                                      "message": "API 已配置" if ready else "到设置里填入 API Key，即可生成下一句"}
            return result

    def _on_worker(self, operation: str, payload: dict) -> dict:
        self._require_active(fresh=False)
        payload = {**payload, '_activity_epoch': self._activity_epoch}
        if operation in SCOPED_OPERATIONS:
            self._require_assist()
            payload['_assist_contact_id'] = self.state['contact']['id']
        future = Future()
        self.commands.put((operation, payload, future))
        # Each backend call has bounded API timeouts. Do not return a timeout
        # while a queued history or paste action continues behind the UI.
        return future.result()

    def dispatch(self, method: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "请求格式无效"}
        try:
            if method in {'save_settings', 'save_contact_preferences', 'set_paused', 'demo'}:
                # Local UI stays usable when one of the source windows is covered.
                if self.window_group is not None and not self.window_group.is_active():
                    self._require_active(fresh=False)
            elif method not in {'get_state', 'get_api_usage', 'close', 'minimize'}:
                self._require_active(fresh=False)
            if method in SCOPED_OPERATIONS:
                self._require_assist()
            if method == "get_state":
                return self.get_state()
            if method == 'get_api_usage':
                from urllib.parse import urlsplit
                return {'ok': True, 'usage': self.api_usage.summary(urlsplit(self.settings.values['base_url']).hostname)}
            if method == 'get_sticker':
                self._require_active()
                epoch = self._activity_epoch
                with self.lock:
                    contact_id = (self.state['contact'] or {}).get('id')
                    revision = self.revision
                    if payload.get('contact_id') != contact_id or payload.get('revision') != revision:
                        raise ValueError('聊天已切换，旧表情包没有显示。')
                    message_id = payload.get('id')
                    if not any(m['id'] == message_id and m.get('has_sticker') for m in self.state['messages']):
                        raise ValueError('这条消息没有可读取的表情包。')
                image = self.database.sticker(contact_id, message_id)
                self._require_active(epoch)
                with self.lock:
                    if revision != self.revision:
                        raise ValueError('聊天已更新，旧表情包没有显示。')
                return {'ok': True, 'data_url': image, 'message': None if image else '表情包原图未缓存或暂时无法读取'}
            if method in {"connect", "open_wechat", "load_history", "fill_draft",
                          "visual_setup_capture", "visual_setup_save", "visual_preview", "visual_bind", "choose_fill",
                          "attachment_preview", "analyze_attachment", "supply_attachment_text", "retry_latest_image", "refresh_voice_text", "transcribe_voice"}:
                result = self._on_worker(method, payload)
                return {"ok": True, "state": self.get_state(), **(result or {})}
            if method == "copy_draft":
                self._require_active(fresh=False)
                epoch = self._activity_epoch
                text = str(payload.get("text", ""))
                if not text.strip() or len(text) > 8000:
                    raise ValueError("请先选一条或写一句回复")
                import win32clipboard
                win32clipboard.OpenClipboard()
                try:
                    self._require_active(epoch, fresh=False)
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                finally:
                    win32clipboard.CloseClipboard()
                return {"ok": True, "state": self.get_state(), "message": "已复制"}
            if method == "generate":
                self._require_active()
                self._require_api_config(self.settings.model_config())
                with self.lock:
                    if self.generating:
                        raise ValueError("正在生成选项，等这组出来后再换一组")
                    if not self.state["contact"] or not self.state["messages"]:
                        raise ValueError("先读取一段聊天，或者试试演示场景")
                    self.model_availability.clear()
                    self._interrupted_signature = None
                    if self.state.get('generation_blocked_reason') and self._automatic_image_candidate() is None:
                        self._require_readable_target()
                    self.intent = str(payload.get("intent", ""))[:300]
                    self.pending_at = time.monotonic()
                self._maybe_generate()
            elif method == "choose":
                with self.lock:
                    if self.choice_revision != self.revision:
                        raise ValueError("聊天有更新，等新选项出来再选")
                    choice = next((c for c in self.state["choices"] if str(c["id"]) == str(payload.get("id"))), None)
                    if not choice:
                        raise ValueError("这条选项已过期，请重新生成")
                    self.state["draft"] = choice["text"]
                    self.selected_revision = self.revision
                    self.memory.record_choice(self.state["contact"]["id"], choice["text"], choice["label"])
            elif method == "set_paused":
                with self.lock:
                    self.state["paused"] = bool(payload.get("paused"))
                    if self.state["paused"]:
                        self.pending_at = None
                        self._cancel_voice_job()
                    elif self.state["messages"] and self.state["settings"]["auto_generate"]:
                        self.pending_at = time.monotonic() + self._auto_delay()
            elif method == "save_settings":
                with self.lock:
                    if "visual_config" in payload:
                        raise ValueError("请在截图读取窗口中重新框选聊天区")
                    was_allowed = self._assist_allowed()
                    self.state["settings"] = self.settings.save(payload)
                    if set(payload) == {'ask_before_assist'}:
                        if self.state['contact'] and was_allowed != self._assist_allowed():
                            self._invalidate_reply_batches()
                            self.revision += 1
                            self._cancel_voice_job()
                            self._image_job = self._media_batch = None
                            self.pending_at = None
                            self.last_signature = None
                            self.state['choices'] = []
                            self.state['relationship_assessment'] = None
                            self.choice_revision = self.selected_revision = -1
                            if not self._assist_allowed():
                                self._apply_disabled_contact(self.state['contact'])
                            else:
                                self.state['generation'] = {'status': 'collecting', 'error': None}
                        return {'ok': True, 'state': self.get_state()}
                    if set(payload) <= {'reply_hold_seconds', 'auto_generate', 'strict_quiet_wait'}:
                        # The retired hold preference is accepted for old clients,
                        # but cannot delay or restart the quiet-period countdown.
                        if 'auto_generate' in payload:
                            if not self.settings.values['auto_generate']:
                                self.pending_at = None
                            elif (self.state.get('reply_batch') or {}).get('has_new_messages'):
                                self.pending_at = time.monotonic() + self._auto_delay()
                        if 'strict_quiet_wait' in payload and self.pending_at is not None:
                            last = self.burst_schedule.last_update
                            self.pending_at = (time.monotonic() if last is None else last) + self._auto_delay()
                        return {'ok': True, 'state': self.get_state()}
                    if payload.get('auto_voice') is False or payload.get('media_policy')=='off':self._cancel_voice_job()
                    response_fields = {'base_url','model','api_type','max_tokens','api_key','clear_api_key','history_limit',
                                       'emoji_mode','reply_style','tone_balance','auto_generate','ask_before_assist',
                                       'media_policy','auto_images','image_limit','sticker_limit','reply_hold_seconds','auto_voice','asr_base_url','asr_model','asr_api_key','clear_asr_api_key'}
                    if not response_fields.intersection(payload):
                        # Saving display preferences must not cause a pending
                        # paid request as a side effect of the settings action.
                        self.pending_at = None
                        return {'ok': True, 'state': self.get_state()}
                    self._invalidate_reply_batches()
                    self.revision += 1
                    self.state['relationship_assessment'] = None
                    self.state["choices"] = []
                    self.state["draft"] = ""
                    self.selected_revision = -1
                    self.state["generation"] = {"status": "idle" if self.state["settings"]["api_ready"] else "unconfigured",
                                                "error": None, "model": self.settings.values["model"]}
                    self.pending_at = None
                    self.last_signature = None
                    self._media_attempted.clear()
                    self._image_attempts.clear()
                    self._image_job = None
                    self._media_batch = None
                    if {'base_url', 'model', 'api_type', 'max_tokens', 'api_key', 'clear_api_key'} & payload.keys():
                        self.model_availability.clear()
                    self._voice_attempts.clear()
                    self._visual_inputs.clear()
                    for message in self.state['messages']:
                        message.pop('visual_input', None)
                    if self.state['messages']:
                        self.state['generation_blocked_reason'] = readiness(self.state['messages'])
                    if self.state['mode'] == 'demo':
                        self._set_demo_choices()
                    elif self.state['messages'] and not self.state['paused'] and self.settings.values['auto_generate']:
                        self.pending_at = time.monotonic() + self._auto_delay()
            elif method == 'save_contact_preferences':
                with self.lock:
                    if not self.state['contact']:
                        raise ValueError('先打开一个聊天，再调整这个联系人的设置。')
                    if payload.get('contact_id') != self.state['contact']['id']:
                        raise ValueError('联系人已切换，请重新打开当前聊天的设置。')
                    if 'revision' in payload and payload['revision'] != self.revision:
                        raise ValueError('聊天已更新，请重新打开当前聊天的设置。')
                    before = self.state.get('contact_preferences') or {}
                    self.state['contact_preferences'] = self.contact_preferences.save(self.state['contact']['id'], payload,self.state['contact'])
                    changed = any(before.get(key) != self.state['contact_preferences'].get(key)
                                  for key in ('adult_context', 'pursuit_goal', 'pursuit_stage', 'assistance_enabled', 'assistance_decided'))
                    if changed:
                        self._invalidate_reply_batches()
                        self.revision += 1
                        self.state['relationship_assessment'] = None
                        self.state['choices'] = []
                        self.state['draft'] = ''
                        self.selected_revision = -1
                        self.choice_revision = -1
                        if 'assistance_enabled' in payload:
                            self._cancel_voice_job()
                            self._image_job = self._media_batch = None
                            self.last_signature = None
                            self.pending_at = None
                            if not self._assist_allowed():
                                self._apply_disabled_contact(self.state['contact'])
                            else:
                                self.state['generation'] = {'status': 'collecting', 'error': None}
                            return {'ok': True, 'state': self.get_state()}
                        self.state['generation'] = {'status': 'idle' if self.state['settings']['api_ready'] else 'unconfigured',
                                                    'error': None, 'source': None, 'model': self.settings.values['model']}
                        if self.state['mode'] == 'demo':
                            self._set_demo_choices()
                        elif self.state['messages'] and not self.state['paused'] and self.settings.values['auto_generate']:
                            self.pending_at = time.monotonic() + self._auto_delay()
                        else:
                            self.pending_at = None
            elif method == "test_model":
                self._require_active(fresh=False)
                config = self.settings.model_config()
                epoch = self._activity_epoch
                config['activity_check'] = lambda: self._require_active(epoch, fresh=False)
                self._require_api_config(config)
                result = self.engine.probe(config)
                if not result.get("model_listed"):
                    raise ValueError("已连接服务，但没有找到填写的模型名称")
                sample = {"contact_id": "connection-test", "contact_name": "连接测试",
                          "recent": [{"role": "other", "text": "周末想找点好玩的", "source": "synthetic"}]}
                proof = self.engine.generate(sample, config)
                return {"ok": True, "state": self.get_state(), "message": "模型已成功生成测试选项", "probe": result,
                        "sample_choices": proof["choices"]}
            elif method == "demo":
                self._demo(bool(payload.get("enabled", True)))
            elif method == "import_history":
                text = str(payload.get("text", ""))
                if not text.strip() or len(text) > 2_000_000:
                    raise ValueError("请粘贴聊天内容，每次最多 200 万字")
                with self.lock:
                    contact = self.state["contact"]
                    supplied_name = str(payload.get("contact_name", "")).strip()
                    if contact and supplied_name and supplied_name != contact["name"]:
                        raise ValueError("这份记录的联系人不同，请先切换到对应聊天")
                    if not contact:
                        name = str(payload.get("contact_name") or "手动聊天")[:80]
                        contact = {"id": "manual:" + hashlib.sha256(name.encode()).hexdigest()[:20], "name": name}
                    added = self.memory.import_text(contact["id"], contact["name"], text)
                    context = self.memory.get_context(contact["id"], limit=30)
                    latest = self.state["messages"] if self.connected and self.state["mode"] == "wechat" else context["recent"]
                    self.last_signature = None
                    self._apply_snapshot({"contact": contact, "messages": context["recent"], "latest_messages": latest, "coverage": "手动导入与本地存档"})
                    if self.state["settings"]["auto_generate"]:
                        self.pending_at = time.monotonic()
                self._maybe_generate()
                return {"ok": True, "state": self.get_state(), "message": f"历史已导入（{added}）"}
            elif method == "close":
                if self.window:
                    self.window.destroy()
            elif method == 'minimize':
                if self.window_group:
                    self.window_group.minimize()
                elif self.window:
                    self.window.minimize()
                return {'ok':True, 'state':self.get_state(), 'message':'窗口已最小化'}
            else:
                raise ValueError("不支持的操作")
            return {"ok": True, "state": self.get_state()}
        except Exception as error:
            if method in {'attachment_preview', 'analyze_attachment', 'supply_attachment_text'}:
                with self.lock:
                    message = next((m for m in self.state['messages'] if m.get('id') == payload.get('id')), {})
                    self.state['attachment_status'] = {'status':'error','kind':message.get('kind'),'id':payload.get('id'),'message':str(error)}
            if method in {"load_history", "fill_draft"}:
                import json
                failure = {"method": method, "code": getattr(error, "code", None),
                           "error": str(error), "time": time.time()}
                (self.settings.directory / "last-operation-error.json").write_text(
                    json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
                with self.lock:
                    if method == "load_history":
                        self.state["history"]["error"] = str(error)
            if isinstance(getattr(error, "diagnostic", None), dict):
                import json
                (self.settings.directory / "capture-diagnostics.json").write_text(
                    json.dumps(error.diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
            if method in {"connect", "visual_bind"} and not isinstance(error, ActivityPaused):
                with self.lock:
                    self.connected = False
                    self._connection_error(str(error) or "微信连接没有完成")
            return {"ok": False, "error": str(error) or "操作暂时没有完成，请稍后再试", "state": self.get_state()}

    @staticmethod
    def _require_api_config(config: dict) -> None:
        if not str(config.get("api_key") or "").strip():
            raise ValueError("请先在设置里填入 API Key，再生成下一句")

    def _vision_config(self) -> dict:
        config = self.settings.model_config()
        config['_usage_observer'] = self.api_usage.record
        config['activity_check'] = self._check_operation_activity
        self._require_api_config(config)
        config["timeout"] = min(float(config.get("timeout", 30)), 30)
        return config

    def _require_readable_target(self) -> None:
        if self.state.get("generation_blocked_reason"):
            raise ValueError(self.state["generation_blocked_reason"])
        if self.connected and self.state["mode"] == "wechat" and not self.state["connection"].get("can_read"):
            raise ValueError("先恢复微信读取并确认当前聊天，再生成选项")

    def _connection_error(self, message: str, status: str = "error") -> None:
        self._cancel_voice_job()
        self._invalidate_reply_batches()
        self._visual_inputs.clear()
        if self.state["connection"].get("can_read") or self.state["choices"] or self.pending_at is not None:
            self.revision += 1
        self.last_signature = None
        self.pending_at = None
        self.selected_revision = -1
        self.state["choices"] = []
        self.state['relationship_assessment'] = None
        self.state['chat_display_name'] = getattr(self.current_adapter,'display_name','')
        self.state['contact'],self.state['contact_preferences'],self.state['messages'] = None,None,[]
        self.state['generation_blocked_reason'] = None
        self.state['attachment_status'] = None
        self.state['history'] = {'count':0,'coverage':'当前聊天暂未读取，等待恢复。'}
        self.state["connection"] = {"status": status, "message": message, "can_read": False, "can_fill": False}
        self.state["generation"] = {"status": "idle", "error": None, "source": None}

    def _demo(self, enabled: bool) -> None:
        self._cancel_voice_job()
        self._invalidate_reply_batches()
        self._visual_inputs.clear()
        with self.lock:
            self.revision += 1
            self.state['relationship_assessment'] = None
            self.last_signature = None
            self.state["choices"], self.state["draft"] = [], ""
            self.state["mode"] = "demo" if enabled else "wechat"
            self.state["generation"] = {"status": "idle", "error": None, "source": None}
            self.state["generation_blocked_reason"] = None
            if not enabled:
                self.state["contact"], self.state["messages"] = None, []
                self.state["history"] = {"count": 0, "coverage": "等待微信聊天"}
                self.pending_at = None
                return
            messages = [
                {"id": "demo-01", "role": "other", "text": "公司附近开了一家火锅店，看起来不错", "timestamp": "2026-10-05 18:20", "source": "demo"},
                {"id": "demo-02", "role": "self", "text": "我也刷到过，感觉可以找时间去试试", "timestamp": "2026-10-05 18:21", "source": "demo"},
                {"id": "demo-03", "role": "other", "text": "今天终于下班啦", "timestamp": "2026-10-07 18:10", "source": "demo"},
                {"id": "demo-04", "role": "self", "text": "恭喜逃离工位哈哈", "timestamp": "2026-10-07 18:11", "source": "demo"},
                {"id": "demo-05", "role": "other", "text": "今天上班好累。", "timestamp": "2026-10-07 18:12", "source": "demo"},
            ]
            self._apply_snapshot({"contact": {"id": "demo:weekend", "name": "小林"}, "messages": messages, "coverage": "虚构演示聊天"})
            self.pending_at = None
            self._set_demo_choices()

    def _set_demo_choices(self):
        lines = ['辛苦了，今天被什么事折腾了？','今日电量耗尽，先吃饭再做人','今晚允许躺平，明天再努力']
        if self.settings.values['reply_style'] == 'serious' or self.settings.values['tone_balance'] >= 75:
            lines = ['今天辛苦了，先好好休息一下。','要是想吐槽，我在这里听。','吃点东西再休息，今晚别硬撑。']
        elif self.settings.values['reply_style'] == 'absurd' or self.settings.values['tone_balance'] <= 15:
            lines = ['申请把今天的班退货，理由：不适合人类。','累了就先休息，今晚不用努力。','今晚批准你变成沙发上的土豆。']
        if self.settings.values['emoji_mode'] != 'off':
            lines[1] += ' 🫠'
            lines[2] += ' (￣▽￣)'
        if self.settings.values['emoji_mode'] == 'lively':
            lines[0] += ' (´･ω･`)'
        self.state['choices'] = [{'id':f'demo-{i}', 'text': text, 'label': label,
                                  'hint': hint, 'tone':'warm'} for i,(text,label,hint) in enumerate(zip(lines,
             ['接住情绪','轻松接梗','留点余地'], ['回应具体经历，愿意说再继续。','熟悉时用玩笑缓和疲惫，难过时先认真关心。','允许休息，不要求对方马上解释。']))]
        self.choice_revision = self.revision
        self.state['generation'] = {'status':'ready','source':'demo','error':None}

    def _apply_snapshot(self, snapshot: dict) -> None:
        if not self._assist_allowed(snapshot['contact']):
            self._apply_disabled_contact(snapshot['contact'], snapshot)
            return
        self._observe_voice_job(snapshot)
        contact = snapshot["contact"]
        all_messages = snapshot.get("messages", [])
        messages = deepcopy(snapshot.get("latest_messages", all_messages))
        original_readiness = readiness(messages) if messages else None
        versions = {m.get('id'): self._visual_version(m) for m in messages}
        selected_visuals = {m['id'] for m in automatic_visuals(messages, self.settings.values)}
        self._image_attempts = {key: value for key, value in self._image_attempts.items()
            if key[0] == contact['id'] and (value.get('message_version') is None
                or value['message_version'] == versions.get(key[1]))}
        self._visual_inputs = {key: value for key, value in self._visual_inputs.items()
            if key[0] == contact['id'] and versions.get(key[1]) == value['message_version']
            and (not value.get('automatic') or key[1] in selected_visuals)}
        for message in messages:
            visual = self._visual_inputs.get((contact['id'], message.get('id')))
            if visual:
                message['visual_input'] = {'status': 'ready', 'cache_tier': visual['tier'],
                    'content_scope': visual.get('content_scope', 'image')}
                message.pop('media_read_status', None)
            elif (message.get('kind') == 'image' or message.get('kind') == 'emoji' and message.get('has_sticker')) and not message.get('content_known'):
                failed = self._image_attempts.get((contact['id'], message.get('id')), {})
                if failed.get('count', 0) >= 1:
                    message['media_read_status'] = 'unavailable'
                elif message.get('id') not in selected_visuals:
                    message['media_read_status'] = 'skipped'
                else:
                    message.pop('media_read_status', None)
            elif message.get('kind') == 'voice' and not message.get('content_known'):
                if self._voice_attempts.get((contact['id'], message.get('id')), {}).get('count', 0) >= 3:
                    message['media_read_status'] = 'unavailable'
                elif not self.settings.values['auto_voice'] or self.settings.values['media_policy'] == 'off':
                    message['media_read_status'] = 'skipped'
        blocked = snapshot.get("generation_blocked_reason")
        # The DB reader only knows whether text is available. Prepared pixels
        # can satisfy its image gap, never an identity or truncated-text error.
        if blocked == original_readiness and blocked and readiness(messages) is None:
            blocked = None
        if snapshot.get("needs_transcript"):
            blocked = blocked or "最新一条消息还没有可读正文。请先在微信转成文字，再读取并生成回复。"
        if messages:
            blocked = blocked or readiness(messages)
        if messages and messages[-1].get("role") not in {"self", "other", "system"}:
            blocked = blocked or "最新消息的发送方未确定，正在等待可确认的消息。"
        signature = (contact["id"], contact.get('conversation_kind'), contact.get('gender'), tuple((m.get("id"), m.get("role"), m.get("text"), m.get("kind"),
                     m.get('timestamp'), m.get('content_known'), repr(m.get('attachment')),
                     repr(m.get('visual_input')), m.get('participant_id'), m.get('sender_display_name')) for m in messages), blocked)
        if signature == self.last_signature:
            return
        previous_contact = self.state.get("contact")
        contact_changed = not previous_contact or previous_contact["id"] != contact["id"]
        incoming_only = (not contact_changed and (incoming_append_only(self.state['messages'], messages)
            or visual_enrichment_only(self.state['messages'], messages)))
        if not incoming_only:
            self._invalidate_reply_batches()
        owned = self._filled_draft
        previous_revision = self.revision
        self.last_signature = signature
        self.revision += 1
        if incoming_only and owned and owned[:2] == (contact['id'], previous_revision):
            self._filled_draft = (contact['id'], self.revision, owned[2])
            self.selected_revision = self.revision
        self.state['relationship_assessment'] = None
        self.state["contact"] = contact
        self.state['contact_preferences'] = self.contact_preferences.get(contact['id'],contact)
        self.state['chat_display_name'] = contact['name']
        self.state["messages"] = messages[-self.settings.values['history_limit']:]
        self.state["generation_blocked_reason"] = blocked
        if not incoming_only:
            self.state["choices"] = []
        self.state['reply_batch'] = self.reply_batches.describe(self.reply_batches.current, messages)
        if contact_changed:
            self.state["draft"] = ""
            self.intent = ""
            self.state['attachment_status'] = None
            self.state['last_fill_result'] = None
        self.memory.upsert_messages(contact["id"], contact["name"], [m for m in all_messages
                                    if m.get("role") in {"self", "other"} and not m.get("truncated")])
        context = self.memory.get_context(contact["id"], limit=30)
        count = next((c["message_count"] for c in self.memory.list_contacts() if c["id"] == contact["id"]), len(messages))
        self.state["history"] = {"count": count, "coverage": snapshot.get("coverage", "已读取当前窗口；更早记录可继续读取或导入")}
        if snapshot.get("warnings"):
            self.state["history"]["warnings"] = snapshot["warnings"]
        self.state["memories"] = context.get("relevant", [])
        if not incoming_only or not self.generating:
            self.state["generation"] = {"status": "error" if blocked else "collecting", "error": blocked, "source": None}
        target = next((m for m in reversed(messages) if m.get('role') in {'self','other'} and m.get('kind') != 'system'), None)
        if (target
                and not blocked
                and self.state["settings"]["auto_generate"] and not self.state["paused"]):
            self.burst_schedule.quiet = self._auto_delay()
            due = self.burst_schedule.update(time.monotonic(), reset=not incoming_only)
            self.pending_at = due
        else:
            self.pending_at = None
            self.burst_schedule.clear()
        failure = self.model_availability.blocked(self._model_signature())
        if failure:
            self.pending_at = None
            self.state['generation'] = failure
        # This runs on the existing background reader, while the quiet timer
        # counts down. No model request is made by context assembly.
        self._prepared_context = None
        if messages and not blocked:
            context = self._build_reply_context()
            self._prepared_context = (self.revision, context, ReplyEngine.prepare_context(context))
            self.state['history']['model_context'] = dict(self._prepared_context[2].payload['prompt_coverage'])

    def _auto_delay(self):
        return 3.0 if self.settings.values.get('strict_quiet_wait', True) else 0.0

    def _build_reply_context(self):
        contact = self.state['contact']
        limit = self.settings.values['history_limit']
        query = self.state['messages'][-1]['text'] if self.state['messages'] else ''
        # Retrieval still searches older facts when the selected reading window
        # itself is large; recent is replaced by the complete selected window.
        context = self.memory.get_context(contact['id'], query=query, limit=min(limit, 200))
        context['recent'] = deepcopy(self.state['messages'][-limit:])
        context['contact_id'] = contact['id']
        context['conversation_kind'] = contact.get('conversation_kind', 'private')
        ids = {m['id'] for m in context['recent']}
        visuals = [deepcopy(value) for (owner, mid), value in self._visual_inputs.items()
            if owner == contact['id'] and mid in ids]
        if visuals:
            context['visual_inputs'] = visuals
        context['reply_preferences'] = self._reply_preferences()
        return context

    def _reply_preferences(self):
        contact = self.state['contact']
        preferences = {k:self.settings.values[k] for k in ('reply_style','tone_balance','emoji_mode')}
        person = self.state['contact_preferences'] or {}
        preferences.update(adult_context=person.get('adult_context') is True and not is_group(contact),
            pursuit_goal=person.get('pursuit_goal', 'get_to_know'), pursuit_stage=person.get('pursuit_stage', 'unknown'))
        return preferences

    def _invalidate_reply_batches(self, *, preserve_resume=False):
        if not preserve_resume:
            self._resume_ready = None
        self.reply_batches.clear()
        self.burst_schedule.clear()
        self.state['reply_batch'] = None
        self._filled_draft = None
        self._prepared_context = None

    def _model_signature(self):
        return tuple(self.settings.values.get(k) for k in ('base_url', 'model', 'api_type', 'max_tokens', 'protected_key'))

    def _generation_signature(self):
        payload = (self.last_signature, self._reply_preferences(), self._model_signature(),
            self.settings.values['history_limit'])
        return hashlib.sha256(repr(payload).encode('utf-8')).hexdigest()

    def _maybe_generate(self) -> None:
        if not self._sync_activity() or self._needs_fresh_snapshot or self.state['paused']:
            return
        with self.lock:
            if not self._assist_allowed():
                self.pending_at = None
                return
            if self.generating or self.pending_at is None or time.monotonic() < self.pending_at:
                return
            if self._interrupted_signature == self._generation_signature():
                self.pending_at = None
                self.state['generation'] = {'status': 'interrupted', 'error': None}
                return
            if not self.state["contact"]:
                self.pending_at = None
                return
            failure = self.model_availability.blocked(self._model_signature())
            if failure:
                self.pending_at = None
                self.state['generation'] = failure
                return
            candidate = self._automatic_image_candidate()
            if candidate is not None:
                deadline = self._media_batch[1] if self._media_batch else time.monotonic() + 8
                if time.monotonic() < deadline:
                    self.state['generation'] = {'status': 'reading_image', 'error': None}
                    return
                self._image_job = None  # Late bytes cannot revive this expired batch.
                for item in automatic_visuals(self.state['messages'], self.settings.values):
                    if not item.get('visual_input'):
                        self._skip_visual(item, '读取超时，本轮先参考文字。')
                self._refresh_media_snapshot()
            self.pending_at = None
            self.burst_schedule.clear()
            try:
                self._require_readable_target()
            except ValueError as error:
                self.state["generation"] = {"status": "error", "error": str(error)}
                return
            if not self.state["settings"]["api_ready"]:
                self.state["generation"] = {"status": "unconfigured", "error": None, "model": self.settings.values["model"]}
                return
            self.generating = True
            self._running_signature = self._generation_signature()
            revision = self.revision
            reply_epoch = self.reply_batches.epoch
            contact_id = self.state["contact"]["id"]
            cache_valid = (self._prepared_context and self._prepared_context[0] == revision
                and self._prepared_context[1]['reply_preferences'] == self._reply_preferences()
                and self._prepared_context[1]['conversation_kind'] == self.state['contact'].get('conversation_kind', 'private'))
            context = deepcopy(self._prepared_context[1]) if cache_valid else self._build_reply_context()
            prepared = self._prepared_context[2] if cache_valid else ReplyEngine.prepare_context(context)
            self.state['history']['model_context'] = dict(prepared.payload['prompt_coverage'])
            previous = [c["text"] for c in self.state["choices"]]
            intent = self.intent
            self.intent = ''  # A one-time direction must not override subsequent peer messages.
            config = self.settings.model_config()
            model_signature = self._model_signature()
            config['_prepared_prompt'] = prepared
            config['verify_replies'] = True
            activity_epoch = self._activity_epoch
            def check_reply_access():
                self._require_active(activity_epoch)
                self._require_assist(contact_id)
                if revision != self.revision or reply_epoch != self.reply_batches.epoch:
                    raise ActivityPaused('此轮辅助已取消。')
            config['activity_check'] = check_reply_access
            def progress(status):
                check_reply_access()
                with self.lock:
                    if revision == self.revision and reply_epoch == self.reply_batches.epoch:
                        self.state['generation']['status'] = status
            config['progress_callback'] = progress
            self.state["generation"] = {"status": "generating", "error": None, "model": config["model"]}
            self.state['relationship_assessment'] = None

        def run():
            try:
                check_reply_access()
                self._require_api_config(config)
                result = self.engine.generate(context, config, intent=intent, previous=previous)
                check_reply_access()
                with self.lock:
                    if (revision == self.revision and reply_epoch == self.reply_batches.epoch and contact_id == (self.state['contact'] or {}).get('id')
                            and not self.stop.is_set()):
                        self.state["choices"] = self.reply_batches.publish(contact_id, revision, context['recent'], result['choices'])
                        self.state['reply_batch'] = self.reply_batches.describe(self.reply_batches.current, self.state['messages'])
                        self.choice_revision = revision
                        assessment = result.get('relationship_assessment')
                        if revision == self.revision and not is_group(self.state['contact']) and isinstance(assessment, dict):
                            self.state['relationship_assessment'] = {
                                **deepcopy(assessment), 'contact_id': contact_id, 'revision': revision,
                            }
                        self.state["generation"] = {"status": "ready", "error": None,
                            'conversation_state': result.get('conversation_state', 'reply'),
                            'retained_closing': result.get('retained_closing'), 'review': result.get('review'),
                            "source": result["source"], "model": result["model"], "elapsed_ms": result["elapsed_ms"]}
            except ActivityPaused:
                pass  # Cancellation is not a model/configuration failure.
            except Exception as error:
                with self.lock:
                    if not self._sync_activity() or activity_epoch != self._activity_epoch:
                        return
                    self.model_availability.record(error, model_signature)
                    service_failure = self.model_availability.blocked(self._model_signature())
                    if service_failure:
                        self.pending_at = None
                        self.state['generation'] = service_failure
                    if revision == self.revision and reply_epoch == self.reply_batches.epoch:
                        self.state["generation"] = {"status": "error", "error": str(error),
                            "error_code": getattr(error, 'code', None), "model": config["model"],
                            'automatic_blocked': bool(service_failure)}
                        # Minimal local diagnosis: no API key, quoted chat or
                        # candidate text. Logging failure must not mask the error.
                        try:
                            import json
                            (self.settings.directory / 'last-generation-error.json').write_text(json.dumps({
                                'checked_at': time.time(), 'error_type': type(error).__name__,
                                'error_code': getattr(error, 'code', None), 'model': config['model'],
                                'messages_count': len(context.get('recent', [])),
                                'conversation_kind': context.get('conversation_kind', 'private')
                            }, ensure_ascii=False, indent=2), encoding='utf-8')
                        except OSError:
                            pass
            finally:
                with self.lock:
                    self.generating = False
                    self._running_signature = None
                    if self._activity_active and not self._needs_fresh_snapshot and revision != self.revision and self.pending_at is not None:
                        self.state['generation'] = {'status': 'collecting', 'error': None, 'source': None}

        threading.Thread(target=run, name="reply-generator", daemon=True).start()

    def _worker(self) -> None:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            loader = lambda contact_id: self.memory.get_context(contact_id, limit=200)["recent"]
            self.uia_adapter = WeChatAdapter(history_loader=loader)
            self.visual_adapter = AdaptiveVisualAdapter(self._vision_config, history_loader=loader)
            if self.settings.values.get("visual_config"):
                try:
                    self.visual_adapter.configure(self.settings.values["visual_config"])
                except (ValueError, RuntimeError):
                    self.state["connection"]["message"] = "保存的聊天框选已失效，请重新框选"
            self.current_adapter = CurrentChatAdapter(self.database, self.visual_adapter, lambda:self.settings.values)
            self.current_adapter.contact_enabled = self._assist_allowed
            self.current_adapter.activity_check = self._check_operation_activity
            self.visual_adapter.backend.activity_check = self._check_operation_activity
            self.adapter = self.current_adapter
            next_poll = 0
            try:
                while not self.stop.is_set():
                    try:
                        operation, payload, future = self.commands.get(timeout=0.25)
                        try:
                            result = self._handle_native(operation, payload)
                            future.set_result(result)
                        except Exception as error:
                            future.set_exception(error)
                    except queue.Empty:
                        pass
                    now = time.monotonic()
                    active = self._sync_activity()
                    if not active:
                        continue  # Only foreground observation / local API state remains.
                    with self.lock:
                        if self._voice_job is not None and now >= self._voice_job['deadline']:
                            self._observe_voice_job({'contact': self.state['contact'] or {'id': None}, 'messages': self.state['messages']})
                    if now >= next_poll or self._needs_fresh_snapshot:
                        next_poll = now + 2.0
                        if self.state["mode"] == "wechat" and not self.state["paused"] and (self.window_group is None or self.window_group.is_active()):
                            try:
                                self._poll_current()
                            except ActivityPaused:
                                continue
                            except Exception as error:
                                with self.lock:
                                    self._connection_error(str(error), getattr(error, "code", "error"))
                        with self.lock:
                            latest = next((m for m in reversed(self.state['messages']) if m.get('role') in {'self','other'}), None)
                            automatic = bool(self._assist_allowed() and self.connected and not self.state['paused'] and self.settings.values['media_policy'] == 'auto' and latest
                                and not self.settings.values.get('auto_voice',True)
                                and self._voice_job is None and self.settings.public().get('asr_ready')
                                and latest.get('kind') == 'voice'
                                and not latest.get('content_known', False) and not latest.get('visual_input'))
                            media_key = ((self.state['contact'] or {}).get('id'), latest.get('id') if latest else None)
                            media_payload = {'contact_id': media_key[0], 'id': media_key[1], 'revision': self.revision, 'consent': True, '_automatic': True}
                        if automatic and media_key not in self._media_attempted:
                            self._media_attempted.add(media_key)
                            try:
                                self._handle_native('analyze_attachment', media_payload)
                            except Exception as error:
                                with self.lock:
                                    self.state['attachment_status'] = {'status':'error','message':str(error)}
                    self._maybe_prepare_voice()
                    self._maybe_prepare_image()
                    # A message/chat can change between periodic probes and the
                    # quiet deadline. Recheck on the UIA worker before submitting.
                    if not self.generating and self.pending_at is not None and time.monotonic() >= self.pending_at:
                        try:
                            self._poll_current()
                        except ActivityPaused:
                            continue
                        except Exception as error:
                            with self.lock:
                                self._connection_error(str(error), getattr(error, 'code', 'error'))
                            continue
                    self._maybe_generate()
            finally:
                self.current_adapter.close()

    @staticmethod
    def _visual_version(message):
        return tuple(message.get(key) for key in ('id', 'role', 'kind', 'text', 'timestamp', 'content_known',
            'participant_id', 'conversation_kind'))

    def _read_visual(self, contact, message, source=None):
        self._check_operation_activity()
        if message['kind'] == 'image':
            picture = (self.local_media.image(contact['id'], message['id'], source=source) if source is not None
                       else self.local_media.image(contact['id'], message['id']))
            url, tier = data_url(picture['bytes']), picture['tier']
        else:
            url, tier = self.database.sticker(contact['id'], message['id']), 'sticker'
            if not url:
                raise ValueError('表情包原图未缓存或暂时无法读取，请在微信打开后重试。')
        size = validate_visual_url(url)
        _, first_frame_only = first_frame_visual(url)
        return {'contact_id': contact['id'], 'message_id': message['id'], 'data_url': url,
                'content_scope': 'first_frame_only' if first_frame_only else 'image',
                'tier': tier, 'bytes': size, 'message_version': self._visual_version(message)}

    def _automatic_image_candidate(self, *, include_failed=False):
        if not self._assist_allowed():
            return None
        if (not self.state['contact'] or self.state['paused'] or self.settings.values['media_policy'] == 'off'
                or self.model_availability.blocked(self._model_signature())):
            return None
        for candidate in reversed(automatic_visuals(self.state['messages'], self.settings.values)):
            attempt = self._image_attempts.get((self.state['contact']['id'], candidate['id']), {})
            if not candidate.get('visual_input') and (include_failed or not attempt.get('count')):
                return candidate
        return None

    def _skip_visual(self, message, reason):
        self._image_attempts[(self.state['contact']['id'], message['id'])] = {
            'count': 1, 'error': reason, 'message_version': self._visual_version(message)}
        self.state['attachment_status'] = {'status': 'skipped', 'kind': message['kind'],
            'id': message['id'], 'message': reason, 'nonblocking': True}

    def _refresh_media_snapshot(self):
        messages = deepcopy(self.state['messages'])
        for message in messages:
            message.pop('media_read_status', None)
            message.pop('visual_input', None)
        self._apply_snapshot({'contact': deepcopy(self.state['contact']), 'messages': messages})

    def _image_config_signature(self):
        return tuple(self.settings.values.get(k) for k in ('base_url', 'model', 'api_type', 'protected_key',
            'media_policy', 'auto_images', 'image_limit', 'sticker_limit'))

    def _maybe_prepare_image(self):
        if not self._sync_activity() or self._needs_fresh_snapshot:
            return
        with self.lock:
            if self._image_job is not None and time.monotonic() >= self._image_job['deadline']:
                expired = self._image_job
                self._image_job = None
                if ((self.state['contact'] or {}).get('id') == expired['contact']['id']
                        and expired['config'] == self._image_config_signature()):
                    self._skip_visual(expired['message'], '读取超时，本轮先参考文字。')
                    self._refresh_media_snapshot()
            if self._image_job is not None or self._voice_job is not None or not self.connected or self.stop.is_set():
                return
            message = self._automatic_image_candidate()
            if message is None:
                return
            contact = deepcopy(self.state['contact'])
            message = deepcopy(message)
            key = (contact['id'], message['id'])
            plan = (contact['id'], tuple(self._visual_version(m)
                for m in automatic_visuals(self.state['messages'], self.settings.values)))
            if self._media_batch is None or self._media_batch[0] != plan:
                self._media_batch = (plan, time.monotonic() + 8)
            if time.monotonic() >= self._media_batch[1]:
                for item in automatic_visuals(self.state['messages'], self.settings.values):
                    if not item.get('visual_input'):
                        self._skip_visual(item, '本轮先参考文字，可从历史重新读取图片。')
                self._refresh_media_snapshot()
                return
            job = {'contact': contact, 'message': message, 'config': self._image_config_signature(),
                   'deadline': self._media_batch[1], '_activity_epoch': self._activity_epoch}
            # Capture exact-message references before a DB poll can replace them.
            source = None
            if message.get('kind') == 'image' and callable(getattr(self.database, 'media_source', None)):
                try:
                    source = deepcopy(self.database.media_source(contact['id'], message['id']))
                except ActivityPaused:
                    return
                except Exception as error:
                    self._skip_visual(message, str(error))
                    self._refresh_media_snapshot()
                    return
            if not self._image_slots.acquire(blocking=False):
                self._skip_visual(message, '图片读取暂时繁忙，本轮先参考文字。')
                self._refresh_media_snapshot()
                return
            self._image_job = job
            self.state['attachment_status'] = {'status': 'processing', 'kind': message['kind'], 'id': message['id'], 'message': '正在看图片…'}
            if not self.state['choices']:
                self.state['generation'] = {'status': 'reading_image', 'error': None}

        def read():
            self._operation_activity.epoch = job['_activity_epoch']
            self._operation_activity.contact_id = contact['id']
            try:
                job['visual'] = self._read_visual(contact, message, source)
                self._require_active(job['_activity_epoch'])
            except Exception as error:
                job['error'] = str(error)
                job['error_code'] = getattr(error, 'code', 'image_unavailable')
            finally:
                self._image_slots.release()
            # Publication and GUI identity checks stay on the single UIA worker.
            if not self.stop.is_set():
                self.commands.put(('image_ready', job, Future()))
        threading.Thread(target=read, name='image-reader', daemon=True).start()

    def _finish_image(self, job):
        self._require_active(job.get('_activity_epoch'))
        with self.lock:
            if self._image_job is not job:
                return {}
            self._image_job = None
            contact, message = job['contact'], job['message']
            current = next((m for m in self.state['messages'] if m.get('id') == message['id']), None)
            if (self.stop.is_set() or (self.state['contact'] or {}).get('id') != contact['id']
                    or job['config'] != self._image_config_signature()
                    or current is None or self._visual_version(current) != self._visual_version(message)):
                return {}
            key = (contact['id'], message['id'])
            if job.get('error'):
                self._skip_visual(current, job['error'])
                self._refresh_media_snapshot()
                return {}
        try:
            gui = self.current_adapter.current()
            matches = self.database.resolve(gui['name'])['id'] == contact['id']
        except Exception as error:
            with self.lock:
                self._connection_error(str(error), getattr(error, 'code', 'error'))
            return {}
        if not matches:
            with self.lock:
                self._connection_error('聊天已切换，正在读取当前对象。', 'chat_changed')
            return {}
        with self.lock:
            if ((self.state['contact'] or {}).get('id') != contact['id'] or job['config'] != self._image_config_signature()):
                return {}
            visual = job['visual']
            rest = [v for k, v in self._visual_inputs.items() if k != key]
            if len(rest) >= MAX_VISUALS or sum(v['bytes'] for v in rest) + visual['bytes'] > MAX_VISUAL_BYTES:
                self._skip_visual(current, '本轮图片数量或大小超出限制，先参考已读内容。')
                self._refresh_media_snapshot()
                return {}
            visual['automatic'] = True
            self._visual_inputs[key] = visual
            self._image_attempts.pop(key, None)
            if self._voice_job is None:
                self.state['attachment_status'] = {'status': 'ready', 'id': message['id'], 'message': '图片已准备好'}
        snapshot = self.adapter.snapshot()
        with self.lock:
            self._apply_snapshot(snapshot)
            if self.state['messages'] and not self.state['paused'] and self.settings.values['auto_generate']:
                self.pending_at = max(self.pending_at or 0, time.monotonic() + self._auto_delay())
        return {}

    def _cancel_voice_job(self):
        job = self._voice_job
        self._voice_job = None
        if job and (self.state.get('attachment_status') or {}).get('id') == job['message']['id']:
            self.state['attachment_status'] = None

    def _automatic_voice_candidate(self):
        if not self._assist_allowed():
            return None
        if (not self.connected or self.state['mode']!='wechat' or not self.state['contact'] or self.state['paused']
                or self.settings.values['media_policy']=='off' or not self.settings.values.get('auto_voice',True)):
            return None
        human=[m for m in self.state['messages'] if m.get('role') in {'self','other'}][-6:]
        latest=next((m for m in reversed(human) if m.get('kind')=='voice'),None)
        return latest if latest and not latest.get('content_known') else None

    def _maybe_prepare_voice(self):
        if not self._sync_activity() or self._needs_fresh_snapshot:
            return
        with self.lock:
            if self._voice_job is not None or self.stop.is_set() or time.monotonic()<self._voice_defer_until:
                return
            message=self._automatic_voice_candidate()
            if message is None:return
            contact_id=self.state['contact']['id'];key=(contact_id,message['id'])
            attempt=self._voice_attempts.get(key,{})
            if attempt.get('count',0)>=3 or time.monotonic()<attempt.get('retry_at',0):return
            self._voice_defer_until=time.monotonic()+1
        try:
            gui=self.current_adapter.current(require_native=True)
            backend=self.current_adapter.visual.backend
            if (backend.gui.GetForegroundWindow()!=gui['window']['hwnd'] or _value(gui['edit'])[1]!=''
                    or self.database.resolve(gui['name'])['id']!=contact_id):return
        except Exception:
            return  # The ordinary reader reports identity problems; no focus request.
        with self.lock:
            current=self._automatic_voice_candidate()
            if current is None or (self.state['contact']['id'],current['id'])!=key:return
            self._voice_attempts[key]={'count':attempt.get('count',0)+1,'retry_at':time.monotonic()+5}
            while len(self._voice_attempts)>32:del self._voice_attempts[next(iter(self._voice_attempts))]
            payload={'contact_id':contact_id,'id':message['id'],'revision':self.revision,'_automatic':True}
        try:self._start_voice_transcription(payload)
        except Exception as error:
            with self.lock:
                if getattr(error,'code',None) in {'voice_deferred', 'background_paused'}:
                    self._voice_attempts.pop(key,None)
                else:
                    self._voice_attempts[key]={'count':3}  # Recovery is explicit, never reopen a failed menu in a loop.
                    if self.state.get('attachment_status'):
                        self.state['attachment_status']['nonblocking'] = True
                    self._refresh_media_snapshot()

    def _observe_voice_job(self, snapshot):
        # Called by the normal UIA-worker poll, including unchanged snapshots.
        # Conversion never waits for WeChat's service on that worker.
        job = self._voice_job
        if job is None:
            return
        if snapshot['contact']['id'] != job['contact_id']:
            self._cancel_voice_job()
            return
        original = job['message']
        current = next((m for m in snapshot.get('messages', []) if m.get('id') == original['id']), None)
        changed = current is None or any(current.get(k) != original.get(k)
            for k in ('id', 'role', 'kind', 'timestamp', 'duration_seconds', 'participant_id', 'conversation_kind'))
        completed = (not changed and current.get('content_known')
            and current.get('content_source') == 'wechat_transcription'
            and bool(str(current.get('text', '')).strip()))
        supplied = (not changed and current.get('content_known') and current.get('content_source') == 'user_supplied')
        if completed or supplied or changed or time.monotonic() >= job['deadline']:
            self._voice_job = None
            key=(job['contact_id'],original['id'])
            if not (completed or supplied):self._voice_attempts[key]={'count':3}
            self.state['attachment_status'] = {'id': original['id'], 'kind': 'voice',
                'status': 'ready' if completed or supplied else 'error',
                'nonblocking': not (completed or supplied),
                'message': '已读取语音内容' if completed or supplied else
                    '语音记录已变化，请重新选择。' if changed else '微信还未返回转文字结果，可稍后重新读取。'}

    def _start_voice_transcription(self, payload):
        self._require_assist(payload.get('contact_id'))
        self._operation_activity.contact_id = self.state['contact']['id']
        self._require_active(payload.get('_activity_epoch'))
        with self.lock:
            contact_id = (self.state['contact'] or {}).get('id')
            if (self.state['mode'] != 'wechat' or self.state['paused'] or contact_id is None
                    or payload.get('contact_id') != contact_id or payload.get('revision') != self.revision):
                raise ValueError('请先打开当前聊天，再选择这条语音。')
            if self._voice_job is not None:
                raise ValueError('正在读一条语音，请等它完成后再试。')
            message = next((m for m in self.state['messages']
                if m.get('id') == payload.get('id') and m.get('kind') == 'voice'), None)
            if message is None:
                raise ValueError('这条语音不在当前读取范围。')
            message = deepcopy(message)
            automatic=payload.get('_automatic') is True
            if automatic and (not self.settings.values.get('auto_voice',True) or self.settings.values['media_policy']=='off'):
                raise ValueError('自动语音读取已关闭。')
            if not automatic:self._voice_attempts[(contact_id,message['id'])]={'count':3}
            # Choosing WeChat conversion never opts into a later silent ASR
            # fallback, even if the general automatic media setting is on.
            self._media_attempted.add((contact_id, message['id']))
        try:
            kwargs={'expected_message':message}
            if automatic:kwargs['automatic']=True
            result = self.current_adapter.begin_voice_transcription(contact_id, message['id'], **kwargs)
            self._check_operation_activity()
        except Exception as error:
            with self.lock:
                if (self.state['contact'] or {}).get('id') == contact_id and getattr(error,'code',None)!='voice_deferred':
                    self.state['attachment_status'] = {'id': message['id'], 'kind': 'voice', 'status': 'error', 'message': str(error)}
            raise
        with self.lock:
            if ((self.state['contact'] or {}).get('id') != contact_id or self.state['paused'] or self.state['mode'] != 'wechat'):
                return {'message': '已停止等待这条语音，当前聊天保持不变。'}
            if result['status'] == 'processing':
                self._voice_job = {'contact_id': contact_id, 'message': message, 'deadline': time.monotonic() + 30}
                self.state['attachment_status'] = {'id': message['id'], 'kind': 'voice', 'status': 'processing', 'message': '正在读这条语音…'}
                return {'message': '正在读这条语音…'}
        # Existing WeChat results take the same refresh path; no second ASR.
        return self._handle_native('refresh_voice_text', payload)

    def _handle_native(self, operation: str, payload: dict) -> dict:
        self._require_active(payload.get('_activity_epoch'), fresh=operation not in {
            'connect', 'open_wechat', 'visual_setup_capture', 'visual_setup_save', 'visual_preview', 'visual_bind'})
        self._operation_activity.epoch = self._activity_epoch
        self._operation_activity.contact_id = None
        if operation in SCOPED_OPERATIONS:
            self._require_assist(payload.get('_assist_contact_id'))
            self._operation_activity.contact_id = self.state['contact']['id']
        if operation == 'image_ready':
            return self._finish_image(payload)
        if operation == 'transcribe_voice':
            return self._start_voice_transcription(payload)
        if operation == 'refresh_voice_text':
            with self.lock:
                revision = self.revision
                contact_id = (self.state['contact'] or {}).get('id')
                if payload.get('contact_id') != contact_id or payload.get('revision') != self.revision:
                    raise ValueError('聊天已更新，请重新打开这条语音。')
                message = next((m for m in self.state['messages'] if m['id'] == payload.get('id') and m.get('kind') == 'voice'), None)
                if message is None: raise ValueError('这条语音不在当前读取范围。')
            snapshot = self.current_adapter.snapshot()
            if snapshot['contact']['id'] != contact_id:
                raise ValueError('微信对象已切换，语音结果未应用。')
            fresh = next((m for m in snapshot['messages'] if m['id'] == payload['id']), None)
            if not fresh or fresh.get('kind') != 'voice':
                raise ValueError('这条语音已不在当前读取范围。')
            with self.lock:
                if revision != self.revision or (self.state['contact'] or {}).get('id') != contact_id:
                    raise ValueError('读取期间聊天或设置变化，语音结果未应用。')
                self._apply_snapshot(snapshot)
            return {'message': '已读取微信转文字结果，保留原发言人与发送时间。' if fresh.get('content_source') == 'wechat_transcription' else
                    '微信尚未保存这条语音的转文字结果；可在微信右键这条语音→转文字，再重新读取。'}
        if operation == 'retry_latest_image':
            with self.lock:
                if payload.get('contact_id') != (self.state['contact'] or {}).get('id'):
                    raise ValueError('聊天已切换，请重试当前图片。')
                message = self._automatic_image_candidate(include_failed=True)
                if message is None:
                    raise ValueError('当前图片没有待重试内容。')
                self._image_attempts.pop((self.state['contact']['id'], message['id']), None)
                self._media_batch = None
                self._refresh_media_snapshot()
            self._maybe_prepare_image()
            return {'message': '正在再看一次…'}
        if operation in {'attachment_preview', 'analyze_attachment', 'supply_attachment_text'}:
            with self.lock:
                revision = self.revision
                contact = deepcopy(self.state['contact'])
                if (not contact or payload.get('contact_id') != contact['id'] or payload.get('revision') != revision):
                    raise ValueError('聊天已更新，请重新打开附件。')
                message = next((m for m in self.state['messages'] if m['id'] == payload.get('id')), None)
                if message is None or message.get('kind', 'text') == 'text':
                    raise ValueError('这条附件已不在当前读取范围。')
                message = deepcopy(message)
            # Current GUI is independently checked before and after cloud work.
            gui = self.current_adapter.current()
            if self.database.resolve(gui['name'])['id'] != contact['id']:
                raise ValueError('微信对象已切换，附件未应用。')
            if operation == 'attachment_preview':
                if message['kind'] in {'image', 'emoji'}:
                    visual = self._read_visual(contact, message)
                    return {'data_url': visual['data_url'], 'tier': visual['tier'],
                        'message': {'original':'本地原图', 'mid':'普通缓存，不保证为原图',
                            'thumb':'仅有缩略图', 'sticker':'表情包原图'}[visual['tier']]}
                return {'message': '语音转写将仅处理选中的音频。' if message['kind'] == 'voice' else '可补充这条附件内容。'}
            if operation == 'supply_attachment_text':
                text, source = payload.get('text'), 'user_supplied'
            else:
                if self.settings.values['media_policy'] == 'off':
                    raise ValueError('云端附件处理已关闭，可补充文字。')
                if payload.get('consent') is not True:
                    raise ValueError('请先确认本次附件将发送到已配置API。')
                with self.lock:
                    self.state['attachment_status'] = {'status':'processing','kind':message['kind'],'id':message['id'],
                        'message': '正在转写这条语音…' if message['kind'] == 'voice' else '正在读取图片，准备直接交给模型…'}
                if message['kind'] in {'image', 'emoji'}:
                    visual = self._read_visual(contact, message)
                elif message['kind'] == 'voice':
                    config = self.settings.asr_config()
                    config['activity_check'] = self._check_operation_activity
                    if not config.get('api_key'):
                        raise ValueError('语音API尚未配置，可在设置填写独立密钥或补充转文字结果。')
                    text = self.cloud_media.transcribe(self.local_media.voice(contact['id'], message['id']), config)
                    source = 'speech_api'
                else:
                    raise ValueError('这类附件可读取本地结构或补充正文，暂不上传二进制内容。')
            fresh = self.current_adapter.current()
            self._check_operation_activity()
            if self.database.resolve(fresh['name'])['id'] != contact['id']:
                raise ValueError('分析期间微信对象已切换，结果未应用。')
            with self.lock:
                if revision != self.revision:
                    raise ValueError('分析期间消息或设置已更新，结果未应用。')
                if operation == 'analyze_attachment' and message['kind'] in {'image', 'emoji'}:
                    key = (contact['id'], message['id'])
                    rest = [v for k, v in self._visual_inputs.items() if k != key]
                    if len(rest) >= MAX_VISUALS or sum(v['bytes'] for v in rest) + visual['bytes'] > MAX_VISUAL_BYTES:
                        raise ValueError('本次最多6张图片，总量12MB；可切换聊天清除已选图片后重新选择。')
                    self._visual_inputs[key] = visual
                    ready_message = '图片已加入本次回复上下文，将与双方聊天一起交给多模态模型。'
                else:
                    self.database.enrich(contact['id'], message['id'], text, source)
                    ready_message = '已取得附件正文，沿用原消息时间。'
                self.state['attachment_status'] = {'status':'ready','id':message['id'],
                    'message': ready_message}
            snapshot = self.adapter.snapshot()
            with self.lock:
                self._apply_snapshot(snapshot)
                if (operation == 'analyze_attachment' and message['kind'] in {'image', 'emoji'}
                        and payload.get('_automatic') is not True
                        and (self.state['contact'] or {}).get('id') == contact['id']):
                    # The button explicitly requests replies, even if background
                    # generation is disabled. Worker execution retains fences.
                    self.pending_at = time.monotonic()
            return {'message': ready_message}
        if operation == 'choose_fill':
            with self.lock:
                if payload.get('contact_id') != (self.state['contact'] or {}).get('id'):
                    raise ValueError('联系人或消息已更新，旧选项没有填入')
                batch_id = payload.get('batch_id')
                if batch_id:
                    choice = self.reply_batches.choice(batch_id, payload['contact_id'], payload.get('id'))
                else:
                    # Older clients have no immutable batch identity.
                    if payload.get('revision') != self.revision:
                        raise ValueError('联系人或消息已更新，旧选项没有填入')
                    if self.choice_revision != self.revision:
                        raise ValueError('请等待当前聊天的新选项')
                    choice = next((c for c in self.state['choices'] if c['id'] == payload.get('id')), None)
                if not choice:
                    raise ValueError('这条选项已过期')
                if self.state['mode'] == 'demo':
                    self.state['draft'] = choice['text']
                    self.selected_revision = self.revision
                    return {'message':'已填入演示输入框，未发送'}
                identity, text = self.state['contact']['id'], choice['text']
                # Settings and generated choices share this fence until the write
                # completes. GUI identity is independently checked by the adapter.
                with self.window_group.capture_guard() if self.window_group else nullcontext():
                    result = self._fill_owned_draft(identity, text, allow_incoming_updates=bool(batch_id))
                if not result.get('ok'):
                    self.state['last_fill_result'] = {'ok':False,'code':result.get('code'), 'message':result.get('message'), 'time':time.time()}
                    if result.get('diagnostic'):
                        self.state['last_fill_result']['diagnostic'] = result['diagnostic']
                    if result.get('code') in {'messages_changed', 'chat_changed'}:
                        try:
                            self._apply_snapshot(self.adapter.snapshot())
                        except Exception as error:
                            self._connection_error(str(error), getattr(error, 'code', 'error'))
                    raise ValueError(result.get('message') or '未能填入微信')
                self.state['draft'] = text
                self.selected_revision = self.revision
                self.memory.record_choice(identity, text, choice['label'])
                self.state['last_fill_result'] = {'ok':True,'time':time.time(), 'verified_draft':True,'sent':False,
                    'input_method':result.get('input_method'), 'elapsed_ms':result.get('elapsed_ms')}
            return result
        if operation in {"visual_setup_capture", "visual_setup_save", "visual_preview"}:
            with self.lock:
                self.connected = False
                self.revision += 1
                self._connection_error("正在确认截图读取的聊天对象", "confirming")
            if operation == "visual_setup_capture":
                self._follow(self.visual_adapter.get_window_rect())
                # This explicit calibration action may restore focus; automatic
                # background snapshots never bring a different app to the front.
                self.visual_adapter.backend.focus(self.visual_adapter.backend.window())
                with self.window_group.capture_guard() if self.window_group else nullcontext():
                    result = self.visual_adapter.capture_setup()
                config = self.settings.values.get("visual_config") or {}
                return {**result, "account_name": config.get("account_name", "")}
            if operation == "visual_setup_save":
                config = self.visual_adapter.configure(payload)
                with self.lock:
                    self.state["settings"] = self.settings.save({"visual_config": config})
                return {"message": "聊天区域已保存，请预览并确认联系人"}
            self._require_api_config(self.settings.model_config())
            self._follow(self.visual_adapter.get_window_rect())
            return {"preview": self.visual_adapter.preview()}
        if operation == "visual_bind":
            snapshot = self.visual_adapter.bind_contact(payload.get("contact_name", ""))
            with self.lock:
                self.adapter = self.visual_adapter
                self.state["mode"] = "wechat"
                self.connected = True
                self.last_signature = None
                self.state["connection"] = self.adapter.status()
                self._apply_snapshot(snapshot)
            self._follow(snapshot.get("window_rect"))
            return {"message": "已确认并连接当前聊天"}
        if operation == "open_wechat":
            result = self.adapter.open_wechat()
            if not result.get("ok"):
                raise ValueError(result.get("message") or "微信未能打开")
            return result
        if operation == "connect":
            self.adapter = self.current_adapter or self.uia_adapter or self.adapter
            if self.current_adapter is not None:
                # Native metadata is readable behind another app. A read-only
                # connection must not reclaim focus just to identify the chat.
                from .wechat import WeChatError
                try:
                    self.current_adapter.current(require_native=True)
                except WeChatError as error:
                    if error.code != 'native_input_lost':
                        raise
                    backend = self.current_adapter.visual.backend
                    backend.focus(backend.window())
            with self.lock:
                self.state["mode"] = "wechat"
            snapshot = self._poll_current(force=True)
            self._follow(snapshot.get("window_rect"))
            return {"message": "已连接当前微信聊天"}
        if operation == "load_history":
            if self.state["mode"] != "wechat" or not self.connected:
                raise ValueError("连接微信聊天后，才能往前读取历史")
            snapshot = self.adapter.load_history(pages=min(20, max(1, int(payload.get("pages", 5)))))
            with self.lock:
                # Older context changed even though the restored latest viewport
                # is identical. Refresh memory/counts and regenerate from it.
                self.last_signature = None
                self._apply_snapshot(snapshot)
            return {"message": "历史已加入本地存档"}
        if operation == "fill_draft":
            with self.lock:
                if self.state["mode"] == "demo":
                    raise ValueError("演示台词不能直接填入真实微信；请先连接目标聊天")
                if self.selected_revision != self.revision or not self.state["contact"]:
                    raise ValueError("聊天内容有变化，请重新选择一条回复")
                contact_id = self.state["contact"]["id"]
                text = str(payload.get("text", self.state["draft"])).strip()
            if not text or len(text) > 2000:
                raise ValueError("回复需要在 1 到 2000 字之间")
            with self.lock:
                if self.selected_revision != self.revision or contact_id != (self.state['contact'] or {}).get('id'):
                    raise ValueError("聊天内容有变化，请重新选择一条回复")
                with self.window_group.capture_guard() if self.window_group else nullcontext():
                    result = self._fill_owned_draft(contact_id, text)
                if not result.get("ok"):
                    raise ValueError(result.get("message") or "未能填入微信")
                self.state['draft'] = text
                return result
        raise ValueError("不支持的本地操作")

    def _fill_owned_draft(self, contact_id, text, *, allow_incoming_updates=False):
        self._check_operation_activity()
        owned = self._filled_draft
        options = {}
        if allow_incoming_updates and self.adapter is self.current_adapter:
            options['allow_incoming_updates'] = True
        if owned and owned[:2] == (contact_id, self.revision):
            result = self.adapter.fill_draft(contact_id, text, replace_text=owned[2], **options)
        else:
            result = self.adapter.fill_draft(contact_id, text, **options)
        # A failed candidate never gains ownership. Retain the earlier verified
        # text so a harmless focus/caret retry can still compare against it.
        if result.get('ok'):
            self._filled_draft = (contact_id, self.revision, text)
        return result

    def _follow(self, rect) -> bool:
        """Place beside WeChat in physical pixels; pywebview.move takes logical pixels."""
        if self.window_group is not None:
            return False  # One native loop owns window placement.
        if not self.window or not rect or not self.settings.values["follow_window"]:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            import win32api
            import win32con
            import win32gui

            native = self.window.native
            if native is None or native.IsDisposed:
                return False
            hwnd = int(native.Handle.ToInt64())
            if not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return False

            left, top, right, bottom = map(int, rect)
            if right <= left or bottom <= top:
                return False

            user32 = ctypes.windll.user32
            set_context = user32.SetThreadDpiAwarenessContext
            set_context.argtypes = [ctypes.c_void_p]
            set_context.restype = ctypes.c_void_p
            get_dpi = user32.GetDpiForWindow
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            previous_context = set_context(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
            if not previous_context:
                return False
            try:
                monitor = win32api.MonitorFromRect((left, top, right, bottom), win32con.MONITOR_DEFAULTTONEAREST)
                work_left, work_top, work_right, work_bottom = win32api.GetMonitorInfo(monitor)["Work"]
                if work_right <= work_left or work_bottom <= work_top:
                    return False

                # Re-read after a move: crossing displays can change the form's DPI/size.
                for _ in range(2):
                    current_left, current_top, current_right, current_bottom = win32gui.GetWindowRect(hwnd)
                    panel_width = current_right - current_left
                    panel_height = current_bottom - current_top
                    dpi = int(get_dpi(hwnd))
                    if not 48 <= dpi <= 768 or panel_width <= 0 or panel_height <= 0:
                        return False
                    if panel_width > work_right - work_left or panel_height > work_bottom - work_top:
                        return False
                    scale = dpi / 96.0
                    gap = max(8, round(8 * scale))
                    candidates = (right + gap, left - panel_width - gap)
                    x = next((candidate for candidate in candidates
                              if work_left <= candidate and candidate + panel_width <= work_right), None)
                    if x is None:
                        # Never clamp an impossible side placement across the chat itself.
                        return False
                    y = min(max(top, work_top), work_bottom - panel_height)
                    if abs(current_left - x) <= 2 and abs(current_top - y) <= 2:
                        return True
                    # WinForms multiplies these by GetDpiForWindow(panel)/96 internally.
                    self.window.move(x / scale, y / scale)

                placed_left, placed_top, placed_right, placed_bottom = win32gui.GetWindowRect(hwnd)
                return (work_left <= placed_left < placed_right <= work_right
                        and work_top <= placed_top < placed_bottom <= work_bottom
                        and (placed_right <= left or placed_left >= right))
            finally:
                set_context(previous_context)
        except Exception:
            return False

    def close(self) -> None:
        self.stop.set()
        # Let the worker's finally restore its accessibility lease before exit.
        if self.worker.is_alive() and threading.current_thread() is not self.worker:
            self.worker.join(timeout=1.0)
        close_adapter = getattr(self.current_adapter, 'close', None)
        if callable(close_adapter):
            close_adapter()
        if self.window_group:
            self.window_group.close()
