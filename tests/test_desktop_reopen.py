import json
import sys
from threading import Event
from types import SimpleNamespace as NS

import pytest

from galchat.desktop_instance import DesktopInstance
from galchat.window_group import WindowGroup


class Handle:
    def __init__(self):
        self.closed = False
        self.signal = Event()
    def Close(self):
        self.closed = True


class Events:
    WAIT_OBJECT_0 = 0
    def __init__(self, api):
        self.api = api
        self.mutex, self.event = Handle(), Handle()
    def CreateMutex(self, *args):
        return self.mutex
    def CreateEvent(self, *args):
        self.api.last_error = 0  # Must not erase the mutex's captured status.
        return self.event
    def SetEvent(self, handle):
        handle.signal.set()
    def WaitForSingleObject(self, handle, milliseconds):
        if handle.signal.wait(milliseconds / 1000):
            handle.signal.clear()
            return 0
        return 258


@pytest.mark.parametrize('already_running', [False, True])
def test_mutex_status_is_captured_before_creating_the_notification_event(already_running):
    api = NS(last_error=183 if already_running else 0)
    api.GetLastError = lambda: api.last_error
    events = Events(api)
    instance = DesktopInstance(api=api, event_api=events)
    try:
        assert instance.already_running is already_running
    finally:
        instance.close()
    assert events.mutex.closed and events.event.closed


def test_second_launch_is_delivered_to_the_owner_even_before_ui_listener_starts(monkeypatch):
    monkeypatch.setitem(sys.modules, 'win32gui', NS(FindWindow=lambda *args: 0))
    api = NS(GetLastError=lambda: 183)
    events = Events(api)
    instance = DesktopInstance(api=api, event_api=events)
    received = Event()
    calls = []
    try:
        instance.request_open()
        instance.listen(lambda: (calls.append('managed_restore'), received.set()))
        assert received.wait(1)
        previous = instance.thread
        instance.listen(lambda: pytest.fail('duplicate listener'))
        assert instance.thread is previous
    finally:
        instance.close()
    assert calls == ['managed_restore'] and not instance.thread.is_alive()


def test_close_wakes_listener_without_reopening_a_disposed_window():
    api = NS(GetLastError=lambda: 0)
    events = Events(api)
    instance = DesktopInstance(api=api, event_api=events)
    calls = []
    instance.listen(lambda: calls.append('unexpected_open'))
    instance.close()
    assert calls == [] and not instance.thread.is_alive()


def group(tmp_path, monkeypatch):
    calls = []
    gui = NS(foreground=999, GetWindowLong=lambda *args: 0,
             SetWindowLong=lambda *args: calls.append(('style', args[-1])))
    gui.GetForegroundWindow = lambda: gui.foreground
    gui.GetAncestor = lambda hwnd, level: hwnd
    gui.ShowWindow = lambda hwnd, command: calls.append(('native_restore', command))
    gui.SetForegroundWindow = lambda hwnd: setattr(gui, 'foreground', hwnd)
    class View:
        Visible = False
        def BringToFront(self): calls.append('child_front')
        def Invalidate(self): calls.append('child_invalidate')
    class Form:
        Visible = False
        WindowState = 'Minimized'
        webview = View()
        def Invoke(self, callback): callback()
        def PerformLayout(self): calls.append('layout')
        def Hide(self):
            self.Visible = False
            calls.append('form_hide')
        def Activate(self):
            calls.append('activate')
            gui.foreground = 2
    monkeypatch.setitem(sys.modules, 'System', NS(Action=lambda callback: callback))
    monkeypatch.setitem(sys.modules, 'System.Windows.Forms', NS(FormWindowState=NS(Normal='Normal')))
    form = Form()
    controller = NS(window=NS(native=form), settings=NS(directory=tmp_path, values={'maximized_mode':'hide'}))
    value = WindowGroup(controller, NS(gui=gui, api=NS(), con=NS()))
    value._hwnd = lambda: 2
    return value, form, gui, calls


def test_reopen_restores_minimized_parent_and_hidden_webview_on_the_ui_thread(tmp_path, monkeypatch):
    value, form, gui, calls = group(tmp_path, monkeypatch)
    value.manual_minimized = True
    value.visible = False
    value.reopen()
    assert form.Visible and form.webview.Visible and form.WindowState == 'Normal'
    assert value.visible and value.user_opened and not value.manual_minimized
    assert gui.foreground == 2 and 'child_invalidate' in calls
    assert ('native_restore', 9) in calls
    receipt = json.loads((tmp_path / 'native-content-check.json').read_text())
    assert receipt['before'] == {'form_visible': False, 'view_visible': False}
    assert receipt['after'] == {'form_visible': True, 'view_visible': True}
    assert receipt['activation_requested'] and receipt['foreground_is_panel']


def test_automatic_restoration_stops_when_other_app_is_foreground(tmp_path, monkeypatch):
    value, form, gui, calls = group(tmp_path, monkeypatch)
    value._restore_content()
    assert not form.Visible and not form.webview.Visible and calls == []
    assert gui.foreground == 999
    gui.foreground = 2
    value._restore_content()
    assert form.Visible and form.webview.Visible and 'activate' not in calls
    assert gui.foreground == 2


def test_focus_can_leave_between_scheduling_restore_and_ui_thread_execution(tmp_path, monkeypatch):
    value, form, gui, calls = group(tmp_path, monkeypatch)
    gui.foreground = 2
    def invoke(callback):
        gui.foreground = 999
        callback()
    form.Invoke = invoke
    value._restore_content()
    assert not form.Visible and not form.webview.Visible and calls == []


def test_launch_without_identifiable_wechat_keeps_a_usable_panel(tmp_path, monkeypatch):
    value, form, gui, calls = group(tmp_path, monkeypatch)
    value.reopen()
    def unavailable(): raise RuntimeError('no current WeChat window')
    value._target_window = unavailable
    value._hide_content = lambda: pytest.fail('explicit launch must not hide its own UI')
    value._frame()
    assert value.visible and form.webview.Visible
    assert value.diagnostic['status'] == 'manual_open'


def test_reading_guard_restores_a_manually_opened_panel_after_capture(tmp_path, monkeypatch):
    value, form, gui, calls = group(tmp_path, monkeypatch)
    value.reopen()
    def unavailable(): raise RuntimeError('no current WeChat window')
    value._target_window = unavailable
    with value.capture_guard():
        assert not form.Visible and not form.webview.Visible and not value.visible
        value._frame()
        assert not form.Visible and not form.webview.Visible
    value._frame()
    assert form.Visible and form.webview.Visible and value.visible


def test_denied_activation_does_not_immediately_hide_the_explicit_launch(tmp_path, monkeypatch):
    value, form, gui, calls = group(tmp_path, monkeypatch)
    value.target = 1
    value.target = 1  # Verified WeChat handle for the active/standby gate.
    gui.foreground = 1
    form.Activate = lambda: None  # Windows keeps WeChat foreground.
    value._target_window = lambda: {'hwnd': 1, 'rect': (1200, 0, 1600, 800)}
    gui.GetWindowRect = lambda hwnd: (100, 100, 500, 700)
    value.backend.api = NS(MonitorFromRect=lambda *args: 1,
                           GetMonitorInfo=lambda *args: {'Work': (0, 0, 1600, 1000)})
    value.backend.con = NS(MONITOR_DEFAULTTONEAREST=2)
    value.reopen()
    value._frame()
    assert value.user_opened and form.Visible and form.webview.Visible
    gui.foreground = 999
    value._frame()
    assert value.user_opened and value.visible
    gui.foreground = 1
    value._frame()  # Actual return resumes the default no-right-space hiding.
    assert not value.user_opened and not value.visible
    assert value.diagnostic['status'] == 'hidden'
