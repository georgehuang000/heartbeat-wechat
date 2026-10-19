"""Current GUI identity + verified database history, never a bot sending stack."""
from copy import deepcopy
import json
import time

from .visual_wechat import VisualWeChatAdapter, _hash, _png
from .wechat import WeChatError, _chat_inputs, _input_contact, _get, _value, _walk
from .native_accessibility import NativeAccessibility
from .reply_batches import incoming_append_only
from .attachments import readiness
from .native_voice_control import begin_conversion
from .activity import ActivityPaused, check_activity


class CurrentChatAdapter:
    def __init__(self, database, visual, settings_provider, auto=None, accessibility=None):
        self.database, self.visual = database, visual
        self.settings_provider, self.auto = settings_provider, auto
        self._identity = None
        self._last = None
        self._title = None
        self._retry_after = 0
        self._retry_signature = None
        self._failed_title_signature = None
        self.display_name = ''
        self.accessibility = accessibility if accessibility is not None else NativeAccessibility()
        self.identity_mode = None
        self.contact_enabled = None
        self._poll_key = None
        self.monitor_stats = {'checks': 0, 'full_reads': 0, 'idle': False}

    def _uia_current(self, window):
        if self.auto is None:
            import uiautomation as auto
            self.auto = auto
        root = self.auto.ControlFromHandle(window['hwnd'])
        edits = _chat_inputs(_walk(root))
        if len(edits) != 1:
            return None
        name = _input_contact(edits[0])
        return {'name': name, 'window': window, 'edit': edits[0], 'mode': 'uia'}

    def current(self, force=False, require_native=False):
        check_activity(getattr(self, 'activity_check', None))
        self.display_name = ''
        window = self.visual.backend.window()
        native = self._uia_current(window)
        check_activity(getattr(self, 'activity_check', None))
        self.accessibility.activity_check = getattr(self, 'activity_check', None)
        if native is None and 'pid' in window and self.accessibility.ensure(window):
            check_activity(getattr(self, 'activity_check', None))
            native = self._uia_current(window)
        if native:
            self.display_name = native['name']
            self.identity_mode = 'uia'
            return native
        if require_native or self.contact_enabled is not None:
            diagnostic = getattr(self.accessibility, 'diagnostic', {})
            if diagnostic.get('code') == 'unsupported_version':
                raise WeChatError('unsupported_version',
                    f"当前微信 {diagnostic['version']} 尚未适配，请更新心动微信后重试。")
            raise WeChatError('native_input_lost', '尚未确认微信原生聊天控件，已暂停操作；请重新选择聊天。')
        self.identity_mode = 'visual'
        window, image = self.visual._capture()
        signature = (window['fingerprint'], _hash(self.visual._part(image, 'title_region')))
        if signature == self._retry_signature and time.monotonic() < self._retry_after and not force:
            raise WeChatError('vision_cooldown', '暂未确认当前聊天标题，稍后重试。')
        if force or signature != self._title:
            try:
                if hasattr(self.visual.reader,'read_title'):
                    title=self.visual._part(image,'title_region')
                    title=title.resize((title.width*3,title.height*3))
                    result=self.visual.reader.read_title(_png(title),self.visual.settings_provider())
                    after_window,after_image=self.visual._capture()
                    after_signature=(after_window['fingerprint'],_hash(self.visual._part(after_image,'title_region')))
                    if after_signature!=signature:
                        raise WeChatError('chat_changed','识别期间聊天标题发生变化，正在读取新对象。')
                else:
                    result = self.visual._read(window, image)
            except ActivityPaused:
                raise
            except Exception:
                self._retry_after = time.monotonic() + 15
                self._retry_signature = signature
                raise
            self._retry_signature = None
            self._retry_after = 0
            self._title, self._identity = signature, result
        self.display_name = self._identity['contact_name']
        return {'name': self._identity['contact_name'], 'window': window, 'image': image,
                'result': deepcopy(self._identity), 'mode': 'visual', 'signature': signature}

    def poll(self, *, force=False):
        """Check identity and source metadata before reading/decoding history."""
        self.monitor_stats['checks'] += 1
        gui = self.current(require_native=True)
        token_reader = getattr(self.database, 'change_token', None)
        token = token_reader() if callable(token_reader) else None
        key = (gui['name'], gui['window']['fingerprint'], tuple(gui['window']['rect']),
            self.settings_provider()['history_limit'], token)
        check_activity(getattr(self, 'activity_check', None))
        if not force and token is not None and key == self._poll_key:
            self.monitor_stats['idle'] = True
            return None
        self.monitor_stats['idle'] = False
        self._poll_key = None  # A failed/inconsistent read must not seed a cache.
        snapshot = self.snapshot(_gui=gui)
        after = token_reader() if callable(token_reader) else None
        if token is not None and token == after:
            self._poll_key = key
        return snapshot

    def snapshot(self, *, _gui=None):
        # Local reading is independent of model permission. Native identity
        # remains mandatory in the desktop runtime; no paid title fallback.
        gui = _gui or (self.current(require_native=True) if self.contact_enabled else self.current())
        expected_account = (self.settings_provider().get('visual_config') or {}).get('account_name')
        if expected_account and self.database.reader.get_self_info().get('nick_name') != expected_account:
            raise WeChatError('account_changed', '数据库账号与已确认的账号名称不同，请在设置里重新核对账号。')
        try:
            contact = self.database.resolve(gui['name'])
        except WeChatError as error:
            signature = gui.get('signature')
            if (error.code != 'contact_not_found' or gui.get('mode') != 'visual'
                    or signature is None or signature == self._failed_title_signature):
                raise
            # A cached OCR typo must not permanently strand an unchanged title.
            # Re-read once per title image, still requiring an exact unique DB match.
            self._failed_title_signature = signature
            gui = self.current(force=True)
            contact = self.database.resolve(gui['name'])
        check_activity(getattr(self, 'activity_check', None))
        self.monitor_stats['full_reads'] += 1
        rows = self.database.recent(contact, self.settings_provider()['history_limit'])
        # A database refresh must not attach its result to a chat opened midway.
        fresh = self.current(require_native=True) if self.contact_enabled else self.current()
        if fresh['name'] != gui['name'] or fresh['window']['fingerprint'] != gui['window']['fingerprint']:
            raise WeChatError('chat_changed', '聊天刚刚切换，正在读取新对象。')
        if gui['mode'] == 'visual':
            # Existing visual write checks retain their window and title fence.
            self.visual._binding = {'contact': contact, 'fingerprint': gui['window']['fingerprint'],
                                    'title_hash': gui['signature'][1]}
        last = rows[-1] if rows else None
        blocked = readiness(rows) if rows else None
        if last and last['role'] not in {'self', 'other', 'system'}:
            blocked = '最新消息的发送方未确定，正在等待可确认的消息。'
        self._last = {'contact': contact, 'messages': rows, 'window_rect': gui['window']['rect'],
                      'coverage': f"当前联系人最近{len(rows)}条（设置上限{self.settings_provider()['history_limit']}条）",
                      'mode': 'database', 'generation_blocked_reason':
                      blocked}
        if self.contact_enabled and not self.contact_enabled(contact):
            self._last['assistance_disabled'] = True
        return deepcopy(self._last)

    def status(self):
        disabled = bool(self._last and self._last.get('assistance_disabled'))
        return {'status': 'connected', 'message': '已识别当前聊天 · 辅助未开启' if disabled else '已跟随当前聊天 · 数据库读取',
                'can_read': self._last is not None, 'can_fill': self._last is not None and not disabled, 'mode': 'database',
                'identity_mode': self.identity_mode, 'native_accessibility': dict(self.accessibility.diagnostic)}

    def load_history(self, **kwargs):
        return self.snapshot()

    def begin_voice_transcription(self, contact_id, message_id, *, expected_message=None, automatic=False):
        return begin_conversion(self, contact_id, message_id, expected_message=expected_message, automatic=automatic)

    @staticmethod
    def _message_version(rows):
        return json.dumps([{k: m.get(k) for k in ('id', 'role', 'kind', 'text', 'timestamp',
            'content_known', 'content_source', 'attachment', 'participant_id', 'sender_display_name',
            'conversation_kind')} for m in rows], sort_keys=True, ensure_ascii=False)

    def _check_message_version(self, contact, *, allow_incoming_updates=False):
        if self._last is None or self._last['contact']['id'] != contact['id']:
            raise WeChatError('messages_changed', '请先读取当前聊天的新选项。')
        latest = self.database.recent(contact, self.settings_provider()['history_limit'])
        if (self._message_version(latest) != self._message_version(self._last['messages'])
                and not (allow_incoming_updates and incoming_append_only(self._last['messages'], latest))):
            raise WeChatError('messages_changed', '聊天已有新消息，旧选项没有填入；正在等待新选项。')

    def fill_draft(self, expected_contact_id, text, *, replace_text=None, allow_incoming_updates=False):
        started = time.perf_counter()
        self._fill_method = None
        result = self._fill_draft(expected_contact_id, text, replace_text=replace_text,
                                 allow_incoming_updates=allow_incoming_updates)
        result['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
        result['input_method'] = self._fill_method
        return result

    def _fill_draft(self, expected_contact_id, text, *, replace_text=None, allow_incoming_updates=False):
        gui = self.current(force=True)
        self._fill_method = 'uia_value' if gui['mode'] == 'uia' else 'visual_clipboard'
        contact = self.database.resolve(gui['name'])
        if contact['id'] != expected_contact_id:
            return {'ok': False, 'code': 'chat_changed', 'message': '聊天已切换，旧选项没有填入。'}
        try:
            self._check_message_version(contact, allow_incoming_updates=allow_incoming_updates)
        except WeChatError as error:
            return {'ok': False, 'code': error.code, 'message': str(error)}
        if gui['mode'] == 'visual':
            self.visual._binding = {'contact': contact, 'fingerprint': gui['window']['fingerprint'],
                                    'title_hash': gui['signature'][1]}
            return self.visual.fill_draft(expected_contact_id, text,
                replace_text=replace_text,
                validate_before_paste=lambda: self._check_message_version(contact, allow_incoming_updates=allow_incoming_updates))
        pattern, value = _value(gui['edit'])
        if pattern is None or value is None or _get(pattern, 'IsReadOnly', False) or not callable(_get(pattern, 'SetValue', None)):
            return {'ok': False, 'message': '输入框无法读写，请检查微信。'}
        if value == text and replace_text == text:
            return {'ok': True, 'message': '这句话已在微信输入框，尚未发送。'}
        if value and value != replace_text:
            return {'ok': False, 'code': 'draft_edited' if replace_text else 'draft_exists',
                    'message': '你已修改微信草稿，已保留原文；清空后可再选。' if replace_text else '微信已有草稿，已保留原文。'}
        expected = value
        check_activity(getattr(self, 'activity_check', None))
        self.visual.backend.focus(gui['window'])
        fresh = self.current(force=True, require_native=True)
        if (self.database.resolve(fresh['name'])['id'] != expected_contact_id
                or fresh['window'].get('fingerprint') != gui['window'].get('fingerprint')):
            return {'ok': False, 'message': '聊天已切换，旧选项没有填入。'}
        pattern, value = _value(fresh['edit'])
        if pattern is None or value != expected or _get(pattern, 'IsReadOnly', False):
            return {'ok': False, 'message': '输入框状态变化，已停止填入。'}
        try:
            self._check_message_version(contact, allow_incoming_updates=allow_incoming_updates)
        except WeChatError as error:
            return {'ok': False, 'code': error.code, 'message': str(error)}
        if _value(fresh['edit'])[1] != expected:
            return {'ok': False, 'code': 'draft_changed', 'message': '微信草稿刚刚发生变化，已保留原文。'}
        check_activity(getattr(self, 'activity_check', None))
        if pattern.SetValue(text) is False:
            return {'ok': False, 'message': '微信输入框没有接受这句话。'}
        after = self.current(force=True, require_native=True)
        if self.database.resolve(after['name'])['id'] != expected_contact_id or _value(after['edit'])[1] != text:
            return {'ok': False, 'message': '无法核对填入结果，请检查微信草稿；尚未发送。'}
        try:
            check_activity(getattr(self, 'activity_check', None))
            after['edit'].SetFocus()
        except Exception:
            pass
        return {'ok': True, 'message': '已填入微信，发送由你完成。'}

    def close(self):
        self.accessibility.close()

    def open_wechat(self):
        return self.visual.open_wechat()

    def get_window_rect(self):
        return self.visual.get_window_rect()
