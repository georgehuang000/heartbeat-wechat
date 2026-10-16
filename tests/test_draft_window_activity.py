from types import SimpleNamespace

from galchat.window_group import WindowGroup


def group(gui):
    value = WindowGroup(SimpleNamespace(window=None), SimpleNamespace(gui=gui))
    value._hwnd = lambda: 2
    value.target = 1
    return value


def test_no_foreground_window_does_not_call_get_ancestor():
    def ancestor(*_):
        raise AssertionError('A missing foreground HWND cannot be queried')
    value = group(SimpleNamespace(GetForegroundWindow=lambda: 0, GetAncestor=ancestor))
    assert value.is_active() is False


def test_disappearing_foreground_window_is_inactive_and_next_sample_recovers():
    roots = iter([OSError('Invalid window handle'), 1])
    def ancestor(*_):
        result = next(roots)
        if isinstance(result, Exception):
            raise result
        return result
    value = group(SimpleNamespace(GetForegroundWindow=lambda: 3, GetAncestor=ancestor))
    assert value.is_active() is False
    assert value.is_active() is True


def test_only_wechat_or_its_panel_is_active():
    value = group(SimpleNamespace(GetForegroundWindow=lambda: 3, GetAncestor=lambda *_: 3))
    assert value.is_active() is False
    value.backend.gui.GetAncestor = lambda *_: 2
    assert value.is_active() is True
    value.target = None
    assert value.is_active() is True  # The panel can configure/connect without a target.


def test_owned_wechat_popup_is_active_but_unrelated_owned_window_is_not():
    value = group(SimpleNamespace(GetForegroundWindow=lambda: 3,
        GetAncestor=lambda h, level: 1 if level == 3 else 3))
    assert value.is_active() is True
    value.backend.gui.GetAncestor = lambda *_: 99
    assert value.is_active() is False


def test_foreground_event_uses_its_handle_even_if_current_foreground_returned():
    value = group(SimpleNamespace(GetForegroundWindow=lambda: 1, GetAncestor=lambda h, _: h))
    assert value.is_active() is True
    assert value.is_active(99) is False


def test_recreated_foreground_wechat_can_recover_a_stale_target():
    value = group(SimpleNamespace(GetForegroundWindow=lambda: 3, GetAncestor=lambda h, _: h))
    value.backend._exe = lambda _: (42, r'C:\Weixin\Weixin.exe')
    assert value.is_active() is True
