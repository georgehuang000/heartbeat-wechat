"""Calibrated, fail-closed screen adapter for Weixin's inaccessible Qt UI.

Only explicit history/fill actions focus or manipulate the window. Screen bytes
are kept in memory; the model only receives the user-calibrated chat rectangle.
Display identities are human-confirmed names, never claimed to be WeChat IDs.
The Win32 backend is lazy and injectable so tests never touch a real desktop.
"""
from __future__ import annotations
from .activity import check_activity

import base64
from copy import deepcopy
import ctypes
import hashlib
import io
import math
import os
import re
from pathlib import Path
import time
from typing import Any, Callable

from PIL import Image, ImageChops, ImageDraw

from .vision import VisionReader
from .wechat import WeChatAdapter, WeChatError, _digest


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _hash(image: Image.Image) -> str:
    # Exact pixel comparison deliberately fails closed on a changed title.
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _possible_caret_change(before: Image.Image, after: Image.Image, dpi_scale: float = 1) -> bool:
    """Only permit a second OCR for a narrow vertical difference, never mask it.

    A typed vertical bar can look like a caret. Both distinct pixel states must
    therefore independently be read as the expected text before either state
    is accepted. Other newly typed characters immediately stop the operation.
    """
    if before.size != after.size or not math.isfinite(dpi_scale) or not 1 <= dpi_scale <= 4:
        return False
    box = ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox()
    # Screenshots use physical pixels. The previously fixed 3px width rejects a
    # normal 4px caret on a 200% monitor (observed live: a 4x37px difference).
    return bool(box and 1 <= box[2] - box[0] <= math.ceil(3 * dpi_scale)
                and math.ceil(8 * dpi_scale) <= box[3] - box[1] <= math.ceil(64 * dpi_scale))


class Win32ScreenBackend:
    """Physical-pixel capture with pre/post z-order and process checks."""

    def __init__(self):
        if os.name != "nt":
            raise WeChatError("unsupported_platform", "视觉连接仅支持 Windows 微信。")
        import win32api
        import win32con
        import win32gui
        import win32process
        self.api, self.con, self.gui, self.process = win32api, win32con, win32gui, win32process

    @staticmethod
    def _dpi():
        try:
            user = ctypes.windll.user32
            user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            if user.GetAwarenessFromDpiAwarenessContext(user.GetThreadDpiAwarenessContext()) != 2:
                raise OSError("DPI context unavailable")
        except Exception as exc:
            raise WeChatError("dpi_unavailable", "无法统一屏幕像素坐标，暂时不能安全截取微信。") from exc

    def _exe(self, hwnd):
        _, pid = self.process.GetWindowThreadProcessId(hwnd)
        handle = self.api.OpenProcess(0x1000, False, pid)
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buffer))
            fn = ctypes.windll.kernel32.QueryFullProcessImageNameW
            fn.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
            if not fn(int(handle), 0, buffer, ctypes.byref(size)):
                raise OSError("process executable unavailable")
            return pid, buffer.value
        finally:
            handle.Close()

    @staticmethod
    def dpi_scale(window) -> float:
        try:
            query = ctypes.windll.user32.GetDpiForWindow
            query.argtypes = [ctypes.c_void_p]
            query.restype = ctypes.c_uint
            dpi = query(ctypes.c_void_p(window['hwnd']))
            if 96 <= dpi <= 384:
                return dpi / 96
        except (AttributeError, OSError, TypeError, KeyError):
            pass
        # An unavailable DPI must not broaden the accepted pixel difference.
        return 1.0

    @staticmethod
    def _cloaked(hwnd) -> bool:
        value = ctypes.c_int()
        try:
            result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                ctypes.c_void_p(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value))
            return result == 0 and bool(value.value)
        except Exception:
            return False

    def window(self, *, include_minimized=False) -> dict:
        check_activity(getattr(self, 'activity_check', None))
        self._dpi()
        found = []
        def inspect(hwnd, _):
            if not re.fullmatch(r'(?:微信|Weixin|WeChat)(?:\s*[（(]\d+[）)])?', self.gui.GetWindowText(hwnd)) or not self.gui.IsWindowVisible(hwnd) or self._cloaked(hwnd):
                return
            # Qt context menus also have a Weixin caption and process. They
            # are owned popups, not an additional account's main window.
            if self.gui.GetWindow(hwnd, self.con.GW_OWNER):
                return
            try:
                pid, executable = self._exe(hwnd)
            except Exception:
                return
            if Path(executable).name.casefold() == "weixin.exe":
                found.append({"hwnd": hwnd, "pid": pid, "executable": executable,
                              "rect": list(self.gui.GetWindowRect(hwnd)),
                              "fingerprint": _digest(pid, hwnd, executable.casefold())})
        self.gui.EnumWindows(inspect, None)
        if len(found) > 1:
            raise WeChatError("ambiguous_window", "检测到多个微信窗口，请只保留当前账号窗口。")
        if not found:
            raise WeChatError("not_running", "未找到 Weixin.exe 的微信窗口，请打开微信并进入聊天。")
        if self.gui.IsIconic(found[0]["hwnd"]) and not include_minimized:
            raise WeChatError("window_minimized", "微信已最小化，恢复窗口后继续读取。")
        return found[0]

    def _layered_visibility(self, hwnd) -> dict:
        """Only explicit constant alpha zero proves that a window draws nothing.

        WS_EX_TRANSPARENT affects hit testing/painting order, not pixel opacity.
        Per-pixel layered windows may contain visible pixels despite that style;
        unsupported attributes therefore remain potential blockers.
        """
        details = {"ex_style": None, "layered_attributes": None, "fully_transparent": False,
                   "verified_cursor_overlay": False}
        try:
            details["ex_style"] = int(self.gui.GetWindowLong(hwnd, -20))
            if details["ex_style"] & 0x00080000:  # WS_EX_LAYERED
                color_key, alpha, flags = self.gui.GetLayeredWindowAttributes(hwnd)
                details["layered_attributes"] = {"color_key": int(color_key), "alpha": int(alpha), "flags": int(flags)}
                details["fully_transparent"] = bool(int(flags) & 0x2 and int(alpha) == 0)  # LWA_ALPHA
                # Observed 2026-10-08: the Codex computer-use helper uses a full-screen
                # color-key surface solely for its cursor highlight. Require every
                # observed identity/style property; never whitelist an arbitrary
                # transparent, layered, or click-through application window.
                cursor_styles = 0x00080000 | 0x00000020 | 0x08000000  # LAYERED, TRANSPARENT, NOACTIVATE
                if (details["ex_style"] & cursor_styles == cursor_styles
                        and int(color_key) == 0 and int(flags) & 0x1
                        and self.gui.GetClassName(hwnd) == "CodexComputerUseCursorOverlay"
                        and Path(self._exe(hwnd)[1]).name.casefold() == "codex-computer-use.exe"):
                    details["verified_cursor_overlay"] = True
        except Exception:
            pass
        return details

    def _check(self, window: dict, area) -> None:
        self._dpi()
        current = self.window()
        if current["fingerprint"] != window["fingerprint"] or current["rect"] != window["rect"]:
            raise WeChatError("window_changed", "微信窗口刚刚移动或发生变化，请重新读取。")
        target, blockers, reached = window["hwnd"], [], False
        visibility = {}
        def inspect(hwnd, _):
            nonlocal reached
            if hwnd == target:
                reached = True
            if reached or not self.gui.IsWindowVisible(hwnd) or self.gui.IsIconic(hwnd) or self._cloaked(hwnd):
                return
            if _overlap(self.gui.GetWindowRect(hwnd), area):
                visibility[hwnd] = self._layered_visibility(hwnd)
                if visibility[hwnd]["fully_transparent"] or visibility[hwnd]["verified_cursor_overlay"]:
                    return
                blockers.append(hwnd)
        self.gui.EnumWindows(inspect, None)
        if not reached or blockers:
            diagnostic_blockers = []
            for hwnd in blockers:
                detail = {"hwnd": hwnd, "class": None, "rect": None, "exe": None,
                          "ex_style": visibility[hwnd]["ex_style"],
                          "layered_attributes": visibility[hwnd]["layered_attributes"]}
                try:
                    detail["class"] = self.gui.GetClassName(hwnd)
                    detail["rect"] = list(self.gui.GetWindowRect(hwnd))
                except Exception:
                    pass  # A disappearing overlay must not hide the original rejection.
                try:
                    detail["exe"] = Path(self._exe(hwnd)[1]).name
                except Exception:
                    pass
                diagnostic_blockers.append(detail)
            error = WeChatError("window_obscured", "微信聊天区被其他窗口遮挡，请移开遮挡后继续。")
            # Diagnostic metadata intentionally excludes all window titles and image/text content.
            error.diagnostic = {"target_hwnd": target, "target_rect": list(window["rect"]),
                                "capture_area": list(area), "target_reached": reached,
                                "blockers": diagnostic_blockers}
            raise error
        # Refuse partially off-screen rectangles rather than return black or another display's pixels.
        left, top = self.api.GetSystemMetrics(76), self.api.GetSystemMetrics(77)
        right, bottom = left + self.api.GetSystemMetrics(78), top + self.api.GetSystemMetrics(79)
        if not (left <= area[0] < area[2] <= right and top <= area[1] < area[3] <= bottom):
            raise WeChatError("window_offscreen", "微信聊天区没有完整显示在屏幕中。")

    def capture(self, window: dict, region: list[int] | None = None) -> Image.Image:
        check_activity(getattr(self, 'activity_check', None))
        from PIL import ImageGrab
        bounds = window["rect"]
        area = bounds if region is None else [bounds[0] + region[0], bounds[1] + region[1],
                                              bounds[0] + region[2], bounds[1] + region[3]]
        self._check(window, area)
        check_activity(getattr(self, 'activity_check', None))
        image = ImageGrab.grab(bbox=tuple(area), all_screens=True).convert("RGB")
        self._check(window, area)
        if image.size != (area[2] - area[0], area[3] - area[1]):
            raise WeChatError("dpi_mismatch", "截图尺寸与屏幕坐标不一致，请重新校准。")
        return image

    def focus(self, window: dict):
        check_activity(getattr(self, 'activity_check', None))
        self._dpi()
        if self.gui.GetForegroundWindow() == window['hwnd']:
            return
        try:
            self.gui.SetForegroundWindow(window["hwnd"])
            self.gui.SetWindowPos(window['hwnd'],0,0,0,0,0,0x0001|0x0002|0x0010)
        except Exception as exc:
            error = WeChatError("focus_failed", "请先点一下微信窗口，再执行本次操作。")
            error.diagnostic = self._focus_diagnostic(window)
            raise error from exc
        # SetForegroundWindow may return before asynchronous activation completes.
        # Observe that one request; never repeatedly reclaim the user's focus.
        deadline = time.monotonic() + 0.6
        while True:
            if self.gui.GetForegroundWindow() == window["hwnd"]:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        error = WeChatError("focus_failed", "无法确认微信获得焦点，本次操作已停止。")
        error.diagnostic = self._focus_diagnostic(window)
        raise error

    def _focus_diagnostic(self, window: dict) -> dict:
        def identity(hwnd):
            detail = {"hwnd": hwnd, "class": None, "exe": None}
            try:
                detail["class"] = self.gui.GetClassName(hwnd)
            except Exception:
                pass
            try:
                detail["exe"] = Path(self._exe(hwnd)[1]).name
            except Exception:
                pass
            return detail
        try:
            foreground = self.gui.GetForegroundWindow()
        except Exception:
            foreground = None
        return {"target": identity(window["hwnd"]), "foreground": identity(foreground),
                "target_rect": list(window["rect"]), "activation_wait_limit_seconds": 0.6}

    def _point(self, window: dict, point):
        check_activity(getattr(self, 'activity_check', None))
        if self.gui.GetForegroundWindow() != window["hwnd"]:
            raise WeChatError("focus_changed", "焦点已离开微信，本次操作已停止。")
        x, y = window["rect"][0] + point[0], window["rect"][1] + point[1]
        self._check(window, [x, y, x + 1, y + 1])
        if self.gui.GetAncestor(self.gui.WindowFromPoint((x, y)), 2) != window["hwnd"]:
            raise WeChatError("window_obscured", "目标位置已被遮挡，本次操作已停止。")
        return x, y

    def scroll(self, window: dict, point, direction: str, amount: int):
        self.api.SetCursorPos(self._point(window, point))
        self.api.mouse_event(self.con.MOUSEEVENTF_WHEEL, 0, 0, 120 * amount * (1 if direction == "up" else -1), 0)
        time.sleep(0.2)

    def paste(self, window: dict, point, text: str, *, before_write=None, after_focus=None):
        import win32clipboard
        # Select all only after a fresh check of the application's untouched draft.
        # Never press Enter or call a send API.
        if any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise WeChatError("modifier_pressed", "请松开 Shift、Ctrl、Alt 或 Windows 键后重试。")
        self.api.SetCursorPos(self._point(window, point))
        self.api.mouse_event(self.con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        self.api.mouse_event(self.con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        self._point(window, point)
        replace = before_write() if before_write is not None else False
        # Prepare the clipboard after slow verification; a user copying during
        # that wait must not change which line this operation pastes.
        win32clipboard.OpenClipboard()
        try:
            check_activity(getattr(self, 'activity_check', None))
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        if after_focus is not None:
            # OCR can take seconds; the user may have clicked WeChat's search
            # field meanwhile. Restore the input target after that wait.
            self.api.SetCursorPos(self._point(window, point))
            self.api.mouse_event(self.con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            self.api.mouse_event(self.con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            after_focus()
        if any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise WeChatError("modifier_pressed", "请松开 Shift、Ctrl、Alt 或 Windows 键后重试。")
        self._point(window, point)
        if replace:
            try:
                self.api.keybd_event(0x11, 0, 0, 0)
                self.api.keybd_event(0x41, 0, 0, 0)
            finally:
                self.api.keybd_event(0x41, 0, self.con.KEYEVENTF_KEYUP, 0)
                self.api.keybd_event(0x11, 0, self.con.KEYEVENTF_KEYUP, 0)
            self._point(window, point)
        try:
            self.api.keybd_event(0x11, 0, 0, 0)
            self.api.keybd_event(0x56, 0, 0, 0)
        finally:
            self.api.keybd_event(0x56, 0, self.con.KEYEVENTF_KEYUP, 0)
            self.api.keybd_event(0x11, 0, self.con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.2)


class VisualWeChatAdapter:
    def __init__(self, settings_provider: Callable[[], dict], reader=None, backend=None,
                 history_loader: Callable[[str], list[dict] | dict] | None = None):
        self.settings_provider = settings_provider
        self.reader = reader if reader is not None else VisionReader()
        self._backend = backend
        self.history_loader = history_loader
        self._history: dict[str, list[dict]] = {}
        self.calibration: dict | None = None
        self._preview = None
        self._binding = None
        self._cache = None
        self._automatic_retry_after = 0.0
        self.version = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = Win32ScreenBackend()
        return self._backend

    def capture_setup(self) -> dict:
        window = self.backend.window()
        image = self.backend.capture(window)
        return {"image_data_url": "data:image/png;base64," + base64.b64encode(_png(image)).decode(),
                "width": image.width, "height": image.height, "window_rect": window["rect"],
                "window_fingerprint": window["fingerprint"]}

    def configure(self, config: dict) -> dict:
        try:
            width, height = int(config["width"]), int(config["height"])
            name = str(config["account_name"]).strip()
            if not name or len(name) > 160 or not (200 <= width <= 10000 and 200 <= height <= 10000):
                raise ValueError()
            normalized = {"width": width, "height": height, "account_name": name}
            for key in ("region", "title_region", "input_region"):
                value = config[key]
                if not isinstance(value, (list, tuple)) or len(value) != 4:
                    raise ValueError()
                rect = [int(v) for v in value]
                if not (0 <= rect[0] < rect[2] <= width and 0 <= rect[1] < rect[3] <= height):
                    raise ValueError()
                normalized[key] = rect
            region = normalized["region"]
            for key in ("title_region", "input_region"):
                rect = normalized[key]
                if not (region[0] <= rect[0] < rect[2] <= region[2] and region[1] <= rect[1] < rect[3] <= region[3]):
                    raise ValueError()
            if normalized["title_region"][3] >= normalized["input_region"][1]:
                raise ValueError()
        except (TypeError, ValueError, KeyError) as exc:
            raise WeChatError("invalid_calibration", "请完整框选聊天区、标题和输入框，并填写当前账号名称。") from exc
        self.calibration = normalized
        self._preview = self._binding = self._cache = None
        self._automatic_retry_after = 0.0
        return deepcopy(normalized)

    def _capture(self):
        if not self.calibration:
            raise WeChatError("calibration_required", "请先校准微信聊天区。")
        window = self.backend.window()
        rect = window["rect"]
        if (rect[2] - rect[0], rect[3] - rect[1]) != (self.calibration["width"], self.calibration["height"]):
            self._binding = self._cache = None
            raise WeChatError("calibration_changed", "微信窗口尺寸已变化，请重新校准聊天区。")
        return window, self.backend.capture(window, self.calibration["region"])

    def _part(self, image, key):
        rect, region = self.calibration[key], self.calibration["region"]
        return image.crop((rect[0] - region[0], rect[1] - region[1], rect[2] - region[0], rect[3] - region[1]))

    def _content_hash(self, image):
        # Input blinks/draft editing must never trigger background image API calls.
        clean = image.copy()
        region, draft = self.calibration["region"], self.calibration["input_region"]
        ImageDraw.Draw(clean).rectangle((draft[0] - region[0], draft[1] - region[1],
                                        draft[2] - region[0] - 1, draft[3] - region[1] - 1), fill="white")
        return _hash(clean)

    def _assert_binding(self, window, image):
        if not self._binding:
            raise WeChatError("contact_confirmation_required", "请预览并确认当前聊天联系人。")
        if window["fingerprint"] != self._binding["fingerprint"]:
            self._binding = self._cache = None
            raise WeChatError("contact_confirmation_required", "微信窗口已重新创建，请重新确认账号和联系人。")
        if _hash(self._part(image, "title_region")) != self._binding["title_hash"]:
            self._binding = self._cache = None
            raise WeChatError("contact_confirmation_required", "聊天标题已变化，请重新预览并确认联系人。")

    def _read(self, window, image, *, strict_input=False):
        result = self.reader.read(_png(image), self.settings_provider())
        after_window, after = self._capture()
        if (after_window["fingerprint"] != window["fingerprint"] or
                self._content_hash(image) != self._content_hash(after) or
                strict_input and _hash(self._part(image, "input_region")) != _hash(self._part(after, "input_region"))):
            raise WeChatError("chat_changed", "识别期间聊天画面发生变化，请重新读取。")
        return result

    def preview(self) -> dict:
        window, image = self._capture()
        result = self._read(window, image)
        self._preview = {"window": window, "image": image, "result": result}
        return {**deepcopy(result), "window_rect": window["rect"],
                "image_data_url": "data:image/png;base64," + base64.b64encode(_png(image)).decode(),
                "identity_note": "请确认当前账号和联系人备注；此显示身份不是微信号，同名联系人应先设置不同备注。"}

    def bind_contact(self, confirmed_name: str) -> dict:
        name = str(confirmed_name).strip()
        if not name or len(name) > 160 or not self._preview:
            raise WeChatError("contact_confirmation_required", "请先预览，再确认当前联系人名称。")
        preview = self._preview
        window, image = self._capture()
        if window["fingerprint"] != preview["window"]["fingerprint"] or self._content_hash(image) != self._content_hash(preview["image"]):
            raise WeChatError("chat_changed", "预览后聊天画面已变化，请重新预览确认。")
        contact = {"id": "wxv:" + _digest("visual-display-identity-v1", self.calibration["account_name"], name),
                   "name": name, "identity_kind": "confirmed_display_name"}
        self._binding = {"contact": contact, "title_hash": _hash(self._part(image, "title_region")),
                         "fingerprint": window["fingerprint"]}
        self._cache = {"hash": self._content_hash(image), "result": deepcopy(preview["result"])}
        return self._snapshot_result(window, preview["result"])

    def _rows(self, result):
        # Uncertain/truncated text remains in preview only, never silently becomes memory.
        return [{"role": row["role"], "text": row["text"], "timestamp": None,
                 "kind": row.get("kind", "text"), "source": "wechat_visual"}
                for row in result["messages"] if row["role"] in {"self", "other"}
                and not row.get("truncated") and "[看不清]" not in row["text"]]

    def _reconcile(self, contact_id, rows, older=False):
        return WeChatAdapter._reconcile(self, contact_id, rows, older)

    def _snapshot_result(self, window, result, older=False):
        contact = deepcopy(self._binding["contact"])
        rows = self._reconcile(contact["id"], self._rows(result), older)
        visible = [row for row in result["messages"] if row["role"] != "system"]
        latest = visible[-1] if visible else None
        needs_transcript = bool(latest and (latest.get("kind", "text") != "text"
            or latest["role"] == "unknown" or latest.get("truncated") or "[看不清]" in latest["text"]))
        return {"contact": contact, "messages": rows, "window_rect": window["rect"], "mode": "visual",
                "coverage": "DeepSeek 视觉读取当前可见消息；未读到的、被截断或发言方不明的内容未存档",
                "warnings": deepcopy(result.get("warnings", [])), "input_text": result.get("input_text", ""),
                "latest_kind": latest.get("kind", "text") if latest else None,
                "needs_transcript": needs_transcript,
                "generation_blocked_reason": "最新消息是媒体或未完整辨认，请先补充文字或核对原消息。" if needs_transcript else None}

    def snapshot(self) -> dict:
        window, image = self._capture()
        self._assert_binding(window, image)
        content_hash = self._content_hash(image)
        if not self._cache or self._cache["hash"] != content_hash:
            if time.monotonic() < self._automatic_retry_after:
                raise WeChatError("vision_cooldown", "上次画面识别未完成，正在等待画面稳定后重试。")
            # Animated media must not cause a paid image request every polling tick.
            time.sleep(0.18)
            stable_window, stable_image = self._capture()
            self._assert_binding(stable_window, stable_image)
            if self._content_hash(stable_image) != content_hash:
                raise WeChatError("screen_unstable", "聊天画面仍在变化，等动画或滚动停下后继续读取。")
            try:
                result = self._read(stable_window, stable_image)
            except Exception:
                self._automatic_retry_after = time.monotonic() + 15
                raise
            self._cache = {"hash": content_hash, "result": result}
        return self._snapshot_result(window, self._cache["result"])

    def status(self) -> dict:
        try:
            window, image = self._capture()
            self._assert_binding(window, image)
            return {"status": "connected", "message": "已连接视觉读取：" + self._binding["contact"]["name"],
                    "can_read": True, "can_fill": True, "version": self.version, "mode": "visual"}
        except WeChatError as exc:
            return {"status": exc.code, "message": str(exc), "can_read": False, "can_fill": False, "mode": "visual"}

    def get_window_rect(self):
        try:
            return self.backend.window()["rect"]
        except WeChatError:
            return None

    def _message_point(self):
        region = self.calibration["region"]
        return ((region[0] + region[2]) // 2,
                (self.calibration["title_region"][3] + self.calibration["input_region"][1]) // 2)

    def _scroll(self, direction, amount):
        window, image = self._capture()
        self._assert_binding(window, image)
        self.backend.scroll(window, self._message_point(), direction, amount)
        window, image = self._capture()
        self._assert_binding(window, image)
        return self._content_hash(image)

    def _latest(self, max_steps=30):
        previous, stable = None, 0
        for _ in range(max_steps):
            current = self._scroll("down", 6)
            stable = stable + 1 if previous == current else 0
            if stable >= 2:
                return self.snapshot()
            previous = current
        raise WeChatError("latest_unverified", "尚未确认回到稳定的最新消息位置，请在微信回到底部后重试。")

    def load_history(self, pages: int = 5) -> dict:
        pages = max(1, min(int(pages), 5))
        window, image = self._capture()
        self._assert_binding(window, image)
        contact = deepcopy(self._binding["contact"])
        self.backend.focus(window)
        saved_history = deepcopy(self._history)
        visited = 0
        stop = "达到本次翻页上限，不代表全部历史"
        restored = None
        try:
            try:
                self._latest()
                for _ in range(pages):
                    before_window, before = self._capture()
                    self._assert_binding(before_window, before)
                    after_hash = self._scroll("up", 2)
                    visited += 1
                    if after_hash == self._content_hash(before):
                        stop = "界面没有加载更多消息，不代表全部历史"
                        break
                    window, image = self._capture()
                    self._assert_binding(window, image)
                    result = self._read(window, image)
                    self._snapshot_result(window, result, older=True)
            finally:
                # Even a failed extraction must restore; a switched contact is never navigated back.
                restored = self._latest(max_steps=max(10, pages * 3 + 4))
        except Exception:
            self._history = saved_history
            self._cache = None
            raise
        if restored is None or restored["contact"]["id"] != contact["id"]:
            raise WeChatError("latest_unverified", "未确认回到最新消息，历史结果没有应用。")
        messages = deepcopy(self._history.get(contact["id"], []))
        return {"contact": contact, "messages": messages, "latest_messages": restored["messages"],
                "window_rect": restored["window_rect"], "mode": "visual",
                "latest_kind": restored["latest_kind"], "needs_transcript": restored["needs_transcript"],
                "generation_blocked_reason": restored["generation_blocked_reason"],
                "coverage": f"向前翻阅 {visited} 次，累计读取 {len(messages)} 条；{stop}；已滚回最新消息位置"}

    def _read_input(self, window, image):
        self._assert_binding(window, image)
        result = self.reader.read_input(_png(self._part(image, "input_region")), self.settings_provider())
        after_window, after = self._capture()
        self._assert_binding(after_window, after)
        return result

    def _verified_input(self, window, image, expected: str, *, allow_empty=False, before_write=False,
                        validate_before_return=None, collect_caret_phases=False):
        """Read back at most two caret phases, without ignoring arbitrary text."""
        first_input = self._part(image, "input_region")
        known_inputs = {_hash(first_input)}
        result = self._read_input(window, image)
        if result.get("input_text") != expected and not (allow_empty and result.get('input_text') == ''):
            if before_write and expected:
                raise WeChatError('draft_edited', '你已修改微信草稿，已保留原文；清空后可再选。')
            raise WeChatError("draft_exists" if expected == "" else "fill_unverified",
                             "微信已有草稿或无法确认空白，已保留原文。" if expected == "" else
                             "已尝试粘贴，但无法核对完整草稿；请在微信检查，尚未发送。")
        expected = result['input_text']  # An empty cleared draft must remain empty too.
        phase_deadline = time.monotonic() + 1.2
        attempt = 0
        while True:
            if validate_before_return is not None:
                validate_before_return()
            fresh_window, fresh = self._capture()
            self._assert_binding(fresh_window, fresh)
            current_input = self._part(fresh, "input_region")
            signature = _hash(current_input)
            if signature in known_inputs:
                if collect_caret_phases and len(known_inputs) == 1 and time.monotonic() < phase_deadline:
                    # Refocusing resets caret visibility. Observe its other
                    # phase locally, then independently OCR it before caching.
                    time.sleep(max(0, min(0.05, phase_deadline - time.monotonic())))
                    continue
                result = {**result, '_verified_input_signatures': known_inputs}
                return result, fresh_window, fresh
            scale = self.backend.dpi_scale(fresh_window) if hasattr(self.backend, 'dpi_scale') else 1.0
            if attempt or not _possible_caret_change(first_input, current_input, scale):
                error = WeChatError("draft_changed", "草稿画面刚刚发生变化，无法安全核对，请在微信检查。")
                error.diagnostic = {"check": ("owned_before_replace" if expected else "empty_before_paste") if before_write else "readback_after_paste",
                    "input_size": list(first_input.size),
                    "changed_bbox": ImageChops.difference(first_input.convert('RGB'), current_input.convert('RGB')).getbbox(),
                    "attempt": attempt, "dpi_scale": scale}
                raise error
            # Do not merely erase a vertical stroke: independently OCR this phase.
            alternate = self._read_input(fresh_window, fresh)
            if alternate.get("input_text") != expected:
                raise WeChatError("draft_changed", "微信草稿内容已变化，已停止操作，请在微信检查。")
            known_inputs.add(signature)
            attempt += 1

    def fill_draft(self, expected_contact_id: str, text: str, *, replace_text=None, validate_before_paste=None) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            return {"ok": False, "code": "invalid_draft", "message": "请填写 1–6000 字的回复。"}
        try:
            window, image = self._capture()
            self._assert_binding(window, image)
            if self._binding["contact"]["id"] != expected_contact_id:
                raise WeChatError("chat_changed", "微信聊天对象已切换，旧选项没有填入。")
            self.backend.focus(window)
            window, image = self._capture()
            self._assert_binding(window, image)
            _, fresh_window, fresh = self._verified_input(window, image, replace_text or '',
                allow_empty=bool(replace_text), before_write=True)
            if validate_before_paste is not None:
                validate_before_paste()
            rect = self.calibration["input_region"]
            point = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
            if replace_text:
                verified_inputs = set()
                def before_write():
                    # Clicking can move the caret. Verify after that click;
                    # the backend then restores focus and checks pixels locally.
                    if validate_before_paste is not None:
                        validate_before_paste()
                    current_window, current_image = self._capture()
                    self._assert_binding(current_window, current_image)
                    result, _, _ = self._verified_input(current_window, current_image, replace_text,
                        allow_empty=True, before_write=True, validate_before_return=validate_before_paste,
                        collect_caret_phases=True)
                    verified_inputs.update(result['_verified_input_signatures'])
                    return bool(result['input_text'])
                def after_focus():
                    current_window, current_image = self._capture()
                    self._assert_binding(current_window, current_image)
                    # Only already OCR-verified phases may pass this fast check.
                    # A new thin stroke is not automatically accepted as a caret.
                    if _hash(self._part(current_image, 'input_region')) not in verified_inputs:
                        raise WeChatError('draft_changed', '微信草稿刚刚发生变化，已停止替换，请在微信检查。')
                self.backend.paste(fresh_window, point, text, before_write=before_write, after_focus=after_focus)
            else:
                self.backend.paste(fresh_window, point, text)
            after_window, after = self._capture()
            self._assert_binding(after_window, after)
            self._verified_input(after_window, after, text)
            return {"ok": True, "message": "已填入并核对微信草稿，确认后由你发送。"}
        except WeChatError as exc:
            result = {"ok": False, "code": exc.code, "message": str(exc)}
            if hasattr(exc, 'diagnostic'):
                result['diagnostic'] = exc.diagnostic
            return result
        except Exception:
            return {"ok": False, "code": "fill_unverified", "message": "无法确认填入结果，请在微信检查草稿；尚未发送。"}

    def open_wechat(self):
        try:
            existing = self.backend.window(include_minimized=True)
        except WeChatError as error:
            if error.code != 'not_running':
                return {'ok': False, 'message': str(error)}
        else:
            check_activity(getattr(self.backend, 'activity_check', None))
            self.backend.gui.ShowWindow(existing['hwnd'], 9)
            try:
                self.backend.focus(self.backend.window())
            except WeChatError:
                return {'ok': True, 'message': '已恢复微信，请点击微信聊天窗口继续。'}
            return {'ok': True, 'message': '已恢复当前微信窗口。'}
        executable = WeChatAdapter._find_executable()
        if not executable:
            return {"ok": False, "message": "未找到微信安装位置，请手动打开微信。"}
        import subprocess
        check_activity(getattr(self.backend, 'activity_check', None))
        subprocess.Popen([str(executable)], close_fds=True)
        return {"ok": True, "message": "已打开微信，请登录并进入要辅助的聊天。"}
