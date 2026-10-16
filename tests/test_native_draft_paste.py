"""Exercise native keyboard intent with fake Win32 APIs, never the desktop."""
from types import SimpleNamespace
import sys
import pytest

from galchat.visual_wechat import Win32ScreenBackend
from galchat.wechat import WeChatError


@pytest.fixture
def native(monkeypatch):
    backend = Win32ScreenBackend.__new__(Win32ScreenBackend)
    events = []
    state = {'focus': 'search', 'clipboard': '', 'modifier': False}
    def mouse(kind, *args):
        if kind == 2:
            state['focus'] = 'input'
    def key(code, scan, flags, extra):
        if flags == 0 and code in (0x41, 0x56):
            events.append((code, state['focus'], state['clipboard']))
    backend.api = SimpleNamespace(GetAsyncKeyState=lambda key: 0x8000 if state['modifier'] else 0,
                                 SetCursorPos=lambda point: None, mouse_event=mouse, keybd_event=key)
    backend.con = SimpleNamespace(MOUSEEVENTF_LEFTDOWN=2, MOUSEEVENTF_LEFTUP=4, KEYEVENTF_KEYUP=2)
    backend._point = lambda window, point: point
    clipboard = SimpleNamespace(OpenClipboard=lambda: None, EmptyClipboard=lambda: None,
                                SetClipboardText=lambda text, kind: state.__setitem__('clipboard', text),
                                CloseClipboard=lambda: None, CF_UNICODETEXT=13)
    monkeypatch.setitem(sys.modules, 'win32clipboard', clipboard)
    monkeypatch.setattr('galchat.visual_wechat.time.sleep', lambda seconds: None)
    return backend, events, state


def test_replacement_restores_input_focus_and_selected_text_after_slow_check(native):
    backend, events, state = native
    checks = []
    def slow_check():
        state.update(focus='search', clipboard='copied while OCR waited')
        return True
    backend.paste({}, (1, 1), 'second', before_write=slow_check,
                  after_focus=lambda: checks.append((state['focus'], state['clipboard'])))
    assert checks == [('input', 'second')]
    assert events == [(0x41, 'input', 'second'), (0x56, 'input', 'second')]


def test_empty_input_never_selects_existing_content(native):
    backend, events, state = native
    backend.paste({}, (1, 1), 'first')
    assert events == [(0x56, 'input', 'first')]


@pytest.mark.parametrize('failure', ['changed', 'modifier'])
def test_changed_pixels_or_new_modifier_during_check_emit_no_write(native, failure):
    backend, events, state = native
    def final_check():
        if failure == 'changed':
            raise WeChatError('draft_changed', 'edited')
        state['modifier'] = True
    with pytest.raises(WeChatError):
        backend.paste({}, (1, 1), 'second', before_write=lambda: True, after_focus=final_check)
    assert events == []
