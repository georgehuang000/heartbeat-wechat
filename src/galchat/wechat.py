"""Small, fail-closed Windows UI Automation adapter for WeChat 4.x.

No message-sending API exists here. The caller owns a single COM/UIA worker
thread and must initialize UIAutomation there before calling these methods.
Only explicit ``load_history`` and ``fill_draft`` calls may change the UI.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable
from uuid import uuid4


class WeChatError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _get(node: Any, attr: str, default: Any = "") -> Any:
    try:
        return getattr(node, attr)
    except Exception:
        return default


def _children(node: Any) -> list[Any]:
    try:
        return node.GetChildren()
    except Exception as exc:
        raise WeChatError("ui_changed", "微信界面刚刚变化，请重新读取。") from exc


def _walk(node: Any, depth: int = 30, limit: int = 1800):
    stack = [(node, 0)]
    seen = 0
    while stack:
        item, level = stack.pop()
        yield item
        seen += 1
        if seen >= limit:
            raise WeChatError("unsupported_ui", "微信控件树超出读取范围，请关闭浮层后重试。")
        if level < depth:
            stack.extend((child, level + 1) for child in reversed(_children(item)))


def _aid(node: Any, suffix: str) -> bool:
    return suffix.lower() in str(_get(node, "AutomationId")).lower().split(".")


def _rect(node: Any) -> list[int] | None:
    r = _get(node, "BoundingRectangle", None)
    if r is None:
        return None
    try:
        result = [int(r.left), int(r.top), int(r.right), int(r.bottom)]
        return result if result[2] > result[0] and result[3] > result[1] else None
    except (TypeError, AttributeError, ValueError):
        return None


def _visible(node: Any) -> bool:
    return not _get(node, "IsOffscreen", False) and _rect(node) is not None


def _digest(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).hexdigest()[:24]


def _clean_contact(name: str) -> str:
    value = (name or "").strip()
    for suffix in ("的消息", "的聊天", "聊天输入框", "消息输入框"):
        if value.endswith(suffix) and len(value) > len(suffix):
            value = value[:-len(suffix)].strip()
            break
    if value in {"输入", "输入消息", "发送消息", "消息", "聊天", "搜索", ""}:
        raise WeChatError("no_chat", "请在微信里打开一个具体聊天。")
    return value


def _value(control: Any) -> tuple[Any, str | None]:
    for method in ("GetValuePattern", "GetLegacyIAccessiblePattern"):
        try:
            pattern = getattr(control, method)()
            if pattern is not None:
                value = pattern.Value
                if isinstance(value, str):
                    return pattern, value
        except Exception:
            continue
    return None, None


def _chat_inputs(nodes):
    """Select editable inputs, excluding Qt's identically named viewport."""
    return [n for n in nodes
            if (_get(n, 'ClassName') == 'mmui::ChatInputField'
                 or (_aid(n, 'chat_input_field') and _get(n, 'ControlTypeName') == 'EditControl'))
            and not _get(n, 'IsOffscreen', False)]


def _input_contact(edit):
    name = str(_get(edit, 'Name')).strip()
    # Qt can append its voice-input placeholder to an empty input's name.
    # Never remove a suffix from a populated or unreadable input.
    if _value(edit)[1] == '':
        name = re.sub(r'(?:长按|按住)鼠标\s*语音输入(?:文字)?$', '', name).strip()
    return _clean_contact(name)


def _signature(message: dict) -> tuple[str, str]:
    return message["role"], message["text"]


class WeChatAdapter:
    """Read the selected conversation and fill a verified, empty draft.

    ``snapshot`` raises WeChatError for unsupported/ambiguous controls. Missing
    timestamps stay None. ``status`` and ``fill_draft`` return UI-ready results.
    Contact IDs use an observed account-name namespace, stable across window
    recreation. Display names are explicitly not permanent WeChat user IDs.
    """

    def __init__(self, auto: Any = None, executable: str | Path | None = None,
                 history_loader: Callable[[str], list[dict] | dict] | None = None):
        if auto is None:
            import uiautomation as auto
        self.auto = auto
        self.executable = Path(executable) if executable else self._find_executable()
        self.version = self._read_version()
        self._history: dict[str, list[dict]] = {}
        self.history_loader = history_loader

    @staticmethod
    def _find_executable() -> Path | None:
        candidates = [
            Path(r"D:\software\Wechatfile\Weixin\Weixin.exe"),
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tencent/Weixin/Weixin.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Tencent/WeChat/WeChat.exe",
        ]
        if os.name == "nt":
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for key in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
                    try:
                        with winreg.OpenKey(hive, key) as handle:
                            install, _ = winreg.QueryValueEx(handle, "InstallPath")
                        candidates.extend([Path(install) / "Weixin.exe", Path(install) / "WeChat.exe"])
                    except OSError:
                        pass
        return next((p for p in candidates if p.is_file()), None)

    def _read_version(self) -> str | None:
        if not self.executable or os.name != "nt":
            return None
        try:
            import win32api
            data = win32api.GetFileVersionInfo(str(self.executable), "\\")
            a, b = data["FileVersionMS"], data["FileVersionLS"]
            return f"{a >> 16}.{a & 65535}.{b >> 16}.{b & 65535}"
        except Exception:
            return None

    def _window(self) -> Any:
        roots = _children(self.auto.GetRootControl())
        main = [c for c in roots if _get(c, "ClassName") == "mmui::MainWindow" and _visible(c)]
        if len(main) == 1:
            return main[0]
        if len(main) > 1:
            raise WeChatError("ambiguous_window", "检测到多个微信主窗口，请仅保留要辅助的账号窗口。")
        if any(_get(c, "ClassName") == "mmui::LoginWindow" for c in roots):
            raise WeChatError("login_required", "微信尚未登录，请完成微信登录后连接。")
        shell = [c for c in roots if "微信" in str(_get(c, "Name")) or "Weixin" in str(_get(c, "Name"))]
        if shell:
            raise WeChatError("unsupported_ui", "已找到微信，但没有可读取的聊天控件。请打开聊天；若仍无效，需要适配当前版本的无障碍界面。")
        raise WeChatError("not_running", "还没找到微信窗口，请先打开微信并进入聊天。")

    def _context(self) -> tuple[Any, Any, Any, dict]:
        window = self._window()
        nodes = [node for node in _walk(window) if _visible(node)]
        inputs = _chat_inputs(nodes)
        if len(inputs) != 1:
            raise WeChatError("no_chat", "请打开一个聊天；当前没有唯一的聊天输入框。")
        edit = inputs[0]
        name = _input_contact(edit)
        lists = [n for n in nodes if _aid(n, "chat_message_list")]
        if not lists:
            lists = [n for n in nodes if _get(n, "ClassName") == "mmui::RecyclerListView"]
        if len(lists) != 1:
            raise WeChatError("unsupported_ui", "当前消息列表无法唯一定位，暂时不能读取这个聊天。")
        account = self._account_identity(window, nodes)
        sessions = [n for n in nodes if _get(n, "ClassName") == "mmui::ChatSessionCell"]
        same_name = [n for n in sessions if str(_get(n, "Name")).split("\n", 1)[0].strip() == name]
        if len(same_name) > 1:
            raise WeChatError("ambiguous_contact", "当前会话列表存在同名聊天，暂时不能确定历史归属；请先为联系人设置不同备注。")
        contact = {"id": "wx:" + _digest("display-identity-v1", account, name), "name": name}
        return window, edit, lists[0], contact

    @staticmethod
    def _account_identity(window: Any, nodes: list[Any]) -> str:
        """Never key long-term memories by an HWND or by generic '微信'."""
        generic = {"微信", "Weixin", "WeChat", "wechat", "weixin", "头像", "我的头像", "个人信息", ""}
        title = str(_get(window, "Name")).strip()
        if title not in generic and not re.fullmatch(r"(?:微信|Weixin|WeChat)\s*[（(]\d+[）)]", title):
            return title
        accounts = set()
        for nav in nodes:
            if _get(nav, "ClassName") != "mmui::MainTabBar":
                continue
            for node in _walk(nav, depth=4, limit=120):
                cls = str(_get(node, "ClassName")).lower()
                aid = str(_get(node, "AutomationId")).lower()
                if not any(part in cls or part in aid for part in ("avatar", "headimage", "headportrait")):
                    continue
                name = str(_get(node, "Name")).strip()
                if name not in generic:
                    accounts.add(name)
        if len(accounts) == 1:
            return accounts.pop()
        raise WeChatError("account_unavailable", "微信没有提供可确认的当前账号标识，暂时不能把聊天加入长期存档。")

    @staticmethod
    def _message_role(row: Any, message_list: Any) -> str:
        """Use explicit sender metadata, or an identifiable avatar's alignment.

        A full-width message-row rectangle is NOT evidence of a sender. If Qt
        omits avatar/role metadata we fail rather than attribute the text to
        the wrong participant. Such versions need a verified visual adapter.
        """
        evidence: set[str] = set()
        nodes = list(_walk(row, depth=6, limit=120))
        for node in nodes:
            aid = str(_get(node, "AutomationId")).lower()
            help_text = str(_get(node, "HelpText")).lower()
            metadata = aid.split(".") + [help_text]
            if any(s in {"self", "outgoing", "sent_message", "自己发送"} for s in metadata):
                evidence.add("self")
            if any(s in {"incoming", "received_message", "对方发送"} for s in metadata):
                evidence.add("other")
        bounds = _rect(message_list)
        if bounds:
            width = bounds[2] - bounds[0]
            for node in nodes[1:]:
                cls = str(_get(node, "ClassName")).lower()
                aid = str(_get(node, "AutomationId")).lower()
                if "avatar" not in cls and "avatar" not in aid and "headimage" not in cls:
                    continue
                box = _rect(node)
                if box is None or box[2] - box[0] > min(120, width * 0.2):
                    continue
                center = (box[0] + box[2]) / 2
                if center < bounds[0] + width * 0.25:
                    evidence.add("other")
                elif center > bounds[0] + width * 0.75:
                    evidence.add("self")
        if len(evidence) != 1:
            raise WeChatError("ambiguous_sender", "已读到消息文字，但当前微信未暴露可确认的发言方；不能把双方历史混在一起。")
        return evidence.pop()

    def _read_rows(self, message_list: Any) -> list[dict]:
        rows = _children(message_list)
        messages = []
        timestamp = None
        for row in rows:
            if not _visible(row):
                continue
            classname = _get(row, "ClassName")
            text = str(_get(row, "Name")).strip()
            if classname == "mmui::ChatItemView":
                if re.search(r"\d{1,2}:\d{2}|昨天|星期|\d{4}年", text):
                    timestamp = text  # UI's exact display, not a fabricated ISO time.
                continue
            if classname not in {"mmui::ChatTextItemView", "mmui::ChatBubbleItemView", "mmui::ChatVoiceItemView"}:
                continue
            if not text:
                continue
            role = self._message_role(row, message_list)
            messages.append({"role": role, "text": text, "timestamp": timestamp, "source": "wechat_uia"})
        return messages

    def _reconcile(self, contact_id: str, rows: list[dict], older: bool = False) -> list[dict]:
        """Reuse overlapping sequence identities; keep repeated identical rows."""
        if contact_id not in self._history and self.history_loader is not None:
            saved = self.history_loader(contact_id)
            if isinstance(saved, dict):
                saved = saved.get("recent", [])
            self._history[contact_id] = [dict(item) for item in saved
                                        if item.get("id") and item.get("text")
                                        and item.get("role") in {"self", "other"}][-5000:]
        old = self._history.get(contact_id, [])
        matches = SequenceMatcher(a=[_signature(m) for m in old], b=[_signature(m) for m in rows], autojunk=False)
        mapped: dict[int, dict] = {}
        for block in matches.get_matching_blocks():
            for offset in range(block.size):
                mapped[block.b + offset] = old[block.a + offset]
        result = []
        for index, row in enumerate(rows):
            item = dict(row)
            if index in mapped:
                item["id"] = mapped[index]["id"]
                item["timestamp"] = item.get("timestamp") or mapped[index].get("timestamp")
            else:
                # Never reset a counter into a previously persisted ID after
                # restart. Saved overlap reuses IDs; genuinely new rows use UUID.
                item["id"] = "wm:" + uuid4().hex
            result.append(item)
        if not old:
            merged = result
        elif mapped:
            # Splice the visible sequence into its known timeline. This preserves
            # surrounding history when the user scrolls to an overlapping page.
            old_index = {m["id"]: i for i, m in enumerate(old)}
            positions = [old_index[m["id"]] for m in result if m["id"] in old_index]
            merged = old[:min(positions)] + result + old[max(positions) + 1:]
        else:
            merged = result + old if older else old + result
        self._history[contact_id] = merged[-5000:]
        return result

    def snapshot(self) -> dict:
        window, _, message_list, contact = self._context()
        rows = self._read_rows(message_list)
        if self._context()[3]["id"] != contact["id"]:
            raise WeChatError("chat_changed", "聊天对象已切换，请重新读取。")
        messages = self._reconcile(contact["id"], rows)
        return {"contact": contact, "messages": messages, "window_rect": _rect(window), "coverage": "微信当前可见消息；尚未读取全部历史"}

    def status(self) -> dict:
        try:
            _, edit, message_list, contact = self._context()
            self._read_rows(message_list)
            pattern, value = _value(edit)
            can_fill = pattern is not None and value is not None and not _get(pattern, "IsReadOnly", False)
            return {"status": "connected", "message": f"已连接：{contact['name']}", "version": self.version, "can_read": True, "can_fill": can_fill}
        except WeChatError as exc:
            return {"status": exc.code, "message": str(exc), "version": self.version, "can_read": False, "can_fill": False}
        except Exception:
            return {"status": "uia_error", "message": "暂时无法读取微信界面，请确认微信窗口可见。", "version": self.version, "can_read": False, "can_fill": False}

    def get_window_rect(self) -> list[int] | None:
        try:
            return _rect(self._window())
        except WeChatError:
            return None

    def _latest_viewport(self, contact_id: str, max_steps: int) -> tuple[Any, list[dict]]:
        """Explicit history action only: leave the chat at the newest viewport."""
        _, _, message_list, contact = self._context()
        if contact["id"] != contact_id:
            raise WeChatError("chat_changed", "聊天对象已切换，已停止滚动。")
        try:
            scroll = message_list.GetScrollPattern()
            if scroll and scroll.VerticallyScrollable:
                scroll.SetScrollPercent(-1, 100)
                time.sleep(0.15)
        except Exception:
            pass
        previous = None
        stable = 0
        for _ in range(max_steps):
            _, _, message_list, current_contact = self._context()
            if current_contact["id"] != contact_id:
                raise WeChatError("chat_changed", "聊天对象已切换，已停止滚动。")
            message_list.WheelDown(wheelTimes=6, waitTime=0.1)
            time.sleep(0.1)
            if self._context()[3]["id"] != contact_id:
                raise WeChatError("chat_changed", "聊天对象已切换，已停止滚动。")
            rows = self._read_rows(message_list)
            signature = [_signature(m) for m in rows]
            stable = stable + 1 if signature == previous else 0
            if stable >= 2:
                return message_list, rows
            previous = signature
        raise WeChatError("latest_unverified", "已读取部分历史，但还没回到稳定的最新消息位置；请在微信回到底部后重新连接。")

    def load_history(self, pages: int = 5) -> dict:
        pages = max(1, min(int(pages), 30))
        window, _, message_list, contact = self._context()
        message_list, start_rows = self._latest_viewport(contact["id"], max_steps=35)
        self._reconcile(contact["id"], start_rows)
        current = start_rows
        visited = 0
        stop = "达到本次翻页上限"
        completed = False
        try:
            for _ in range(pages):
                if self._context()[3]["id"] != contact["id"]:
                    raise WeChatError("chat_changed", "读取历史期间聊天对象已切换，已停止。")
                message_list.WheelUp(wheelTimes=3, waitTime=0.2)
                time.sleep(0.15)
                visited += 1
                _, _, message_list, latest_contact = self._context()
                if latest_contact["id"] != contact["id"]:
                    raise WeChatError("chat_changed", "读取历史期间聊天对象已切换，已停止。")
                new_rows = self._read_rows(message_list)
                if [_signature(m) for m in new_rows] == [_signature(m) for m in current]:
                    stop = "界面没有加载更多消息（不代表全部历史）"
                    break
                self._reconcile(contact["id"], new_rows, older=True)
                current = new_rows
            completed = True
        finally:
            # Always return to latest, never leave old messages as the next
            # model trigger. A user changing contact wins; never navigate back.
            try:
                if self._context()[3]["id"] == contact["id"]:
                    _, latest_rows = self._latest_viewport(contact["id"], max_steps=max(6, pages * 2 + 4))
                    self._reconcile(contact["id"], latest_rows)
                elif completed:
                    raise WeChatError("chat_changed", "聊天对象已切换，历史读取结果没有应用到新聊天。")
            except Exception:
                if completed:
                    raise
        messages = list(self._history.get(contact["id"], []))
        return {"contact": contact, "messages": messages, "latest_messages": latest_rows,
                "window_rect": _rect(window), "coverage": f"本次向前翻阅 {visited} 次，累计读取 {len(messages)} 条；{stop}；已滚回最新消息位置"}

    def fill_draft(self, expected_contact_id: str, text: str, *, replace_text=None) -> dict:
        text = str(text)
        if not text.strip() or len(text) > 8000:
            return {"ok": False, "message": "请填写 1–8000 字的回复。"}
        try:
            _, edit, _, contact = self._context()
            if contact["id"] != expected_contact_id:
                raise WeChatError("chat_changed", "微信聊天对象已切换，旧选项没有填入。")
            pattern, current = _value(edit)
            if pattern is None or current is None or _get(pattern, "IsReadOnly", False):
                raise WeChatError("draft_unreadable", "无法确认微信输入框内容，请使用复制按钮。")
            if current == text and replace_text == text:
                return {"ok": True, "message": "这句话已在微信输入框里，尚未发送。"}
            if current and current != replace_text:
                raise WeChatError("draft_edited" if replace_text else "draft_exists",
                                 "你已修改微信草稿，已保留原文；清空后可再选。" if replace_text else
                                 "微信输入框已有草稿，已保留原文；请先自行处理草稿。")
            expected = current
            # Reacquire immediately before writing: cached Qt controls can refer
            # to a previous conversation after the user clicks another contact.
            _, edit, _, fresh = self._context()
            if fresh["id"] != expected_contact_id:
                raise WeChatError("chat_changed", "微信聊天对象已切换，旧选项没有填入。")
            pattern, current = _value(edit)
            if pattern is None or current != expected or _get(pattern, "IsReadOnly", False):
                raise WeChatError("draft_changed", "微信草稿刚刚发生变化，已停止填入。")
            if pattern.SetValue(text) is False:
                raise WeChatError("fill_unsupported", "当前微信不支持直接填入，请使用复制按钮。")
            _, value = _value(edit)
            if value != text:
                raise WeChatError("fill_unverified", "已尝试填入，但无法核对完整内容；请在微信检查草稿，尚未发送。")
            if self._context()[3]["id"] != expected_contact_id:
                raise WeChatError("chat_changed", "填入时聊天对象发生变化，请检查微信草稿；尚未发送。")
            return {"ok": True, "message": "已填入微信输入框，确认后由你发送。"}
        except WeChatError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc)}
        except Exception:
            return {"ok": False, "code": "fill_failed", "message": "填入没有得到确认，请检查微信草稿；尚未发送。"}

    def open_wechat(self) -> dict:
        if self.executable is None or not self.executable.is_file():
            return {"ok": False, "message": "未找到微信安装位置，请手动打开微信。"}
        subprocess.Popen([str(self.executable)], close_fds=True)
        return {"ok": True, "message": "已打开微信，请登录并进入要辅助的聊天。"}
