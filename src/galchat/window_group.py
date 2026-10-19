"""WeChat and its tool panel share visibility and z-order, without global topmost."""
from contextlib import contextmanager
import ctypes
import threading
import time
from pathlib import Path
from .activity import ActivityPaused


def placement(rect, work, panel_size, maximized, mode='hide', scale=1):
    left, top, right, bottom = rect
    wl, wt, wr, wb = work
    width, height = panel_size
    if width > wr-wl or height > wb-wt:
        return None
    if maximized:
        if mode == 'hide':
            return None
        return max(wl, min(right, wr)-width-int(12*scale)), max(wt, min(top+int(70*scale), wb-height)), True
    x = right + int(8*scale)
    if x+width > wr:
        return None
    return x, max(wt, min(top, wb-height)), False


class WindowGroup:
    def __init__(self, controller, backend):
        self.controller, self.backend = controller, backend
        self.stop = threading.Event()
        self.thread = None
        self.suspended = 0
        self.lock = threading.RLock()
        self.target = None
        self.visible = False
        self._hooks = []
        self.diagnostic = {'status':'starting'}
        self.manual_minimized = False
        # Explicit app launch should expose a usable panel even before WeChat
        # is available. Following resumes when the user returns to WeChat.
        self.user_opened = True
        self._opened_last_foreground = None
        self._minimized_target_was_active = False
        self._target_cache = None
        self._target_checked = 0
        self._last_foreground = None
        self._last_recorded = 0

    def _target_window(self):
        """Discover slowly; read the known window's live rect while dragging."""
        now = time.monotonic()
        if self._target_cache is None or now-self._target_checked >= .3:
            self._target_cache = self.backend.window()
            self._target_checked = now
        target = dict(self._target_cache)
        gui = self.backend.gui
        if not gui.IsWindow(target['hwnd']) or gui.IsIconic(target['hwnd']) or not gui.IsWindowVisible(target['hwnd']):
            self._target_cache = None
            raise RuntimeError('微信窗口已隐藏或最小化')
        target['rect'] = list(gui.GetWindowRect(target['hwnd']))
        return target

    def _hwnd(self):
        window = self.controller.window
        if window is None or window.native is None:
            return None
        return int(window.native.Handle.ToInt64())

    def start(self):
        if self.thread is None:
            self.visible = bool(self.backend.gui.IsWindowVisible(self._hwnd()))
            self.thread = threading.Thread(target=self._run, name='wechat-window-group', daemon=True)
            self.thread.start()

    def is_active(self, foreground_hwnd=None):
        # Windows can briefly return no foreground window, or destroy it between
        # these two calls. Activity sampling must not kill the command worker.
        try:
            hwnd = self._hwnd()
            current = self.backend.gui.GetForegroundWindow() if foreground_hwnd is None else foreground_hwnd
            if not current:
                return False
            foreground = self.backend.gui.GetAncestor(current, 2)
            if not foreground:
                return False
            if foreground in {hwnd, self.target}:
                return True
            # Owned WeChat menus/dialogs remain inside the same window group.
            owner = self.backend.gui.GetAncestor(current, 3)
            if owner and owner in {hwnd, self.target}:
                return True
            # Bootstrap from the actual foreground only, without enumerating
            # chats, querying UIA or reading another app's window contents.
            if callable(getattr(self.backend, '_exe', None)):
                return Path(self.backend._exe(foreground)[1]).name.casefold() == 'weixin.exe'
            return False
        except Exception:
            return False

    def minimize(self):
        if not self.is_active():
            raise ActivityPaused('已暂停后台窗口操作。')
        with self.lock:
            self.manual_minimized = True
            self.user_opened = False
            self._minimized_target_was_active = True
            self.backend.gui.ShowWindow(self._hwnd(), 6)
            self.visible = False
            self._pause_work()

    def _pause_work(self):
        signal = getattr(self.controller, '_background_event', None)
        if signal is not None and getattr(self.controller, '_activity_active', None) is not False:
            signal.set()

    def work_state(self, foreground_hwnd=None):
        """Both real windows must be exposed; never raise them to pass this check.

        Only window geometry/styles are inspected, never another app's content.
        Foreground membership stays separate so settings and explicit reopen can
        remain available while the source window is missing or minimized.
        """
        inactive = lambda reason: {'active': False, 'reason': reason}
        try:
            gui = self.backend.gui
            panel, target = self._hwnd(), self.target
            if not panel or not gui.IsWindow(panel) or gui.IsIconic(panel) or not gui.IsWindowVisible(panel):
                return inactive('panel_hidden')
            if not self.is_active(foreground_hwnd):
                return inactive('background')
            if not target or not gui.IsWindow(target) or gui.IsIconic(target) or not gui.IsWindowVisible(target):
                return inactive('wechat_hidden')
            if self.backend._cloaked(panel) or self.backend._cloaked(target):
                return inactive('window_hidden')
            areas = {hwnd: gui.GetWindowRect(hwnd) for hwnd in (panel, target)}
            if any(r[2] <= r[0] or r[3] <= r[1] for r in areas.values()):
                return inactive('window_hidden')
            pending, blocked = set(areas), False
            from .visual_wechat import _overlap
            def inspect(hwnd, _):
                nonlocal blocked
                if hwnd in pending:
                    pending.remove(hwnd)
                    return
                if blocked or not pending or not gui.IsWindowVisible(hwnd) or gui.IsIconic(hwnd) or self.backend._cloaked(hwnd):
                    return
                # The pair's own menus/dialogs belong to the same workspace.
                if gui.GetAncestor(hwnd, 3) in areas:
                    return
                if not any(_overlap(gui.GetWindowRect(hwnd), areas[h]) for h in pending):
                    return
                details = self.backend._layered_visibility(hwnd)
                if not details['fully_transparent'] and not details['verified_cursor_overlay']:
                    blocked = True
            gui.EnumWindows(inspect, None)
            if blocked or pending:
                return inactive('window_obscured')
            return {'active': True, 'reason': None}
        except Exception:
            return inactive('visibility_unknown')

    def _record(self, value):
        if value != self.diagnostic:
            self.diagnostic = value
            now = time.monotonic()
            if value.get('status') == 'attached' and now-self._last_recorded < .5:
                return  # Keep drag updates in memory; avoid disk writes per frame.
            self._last_recorded = now
            import json
            (self.controller.settings.directory/'window-group-check.json').write_text(
                json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

    def _restore_content(self, *, activate=False):
        """Restore the managed child as well as the outer HWND, without focus."""
        window = getattr(self.controller, 'window', None)
        form = getattr(window, 'native', None)
        if form is None:
            return
        from System import Action
        def restore():
            if not activate and not self.is_active():
                return  # UI-thread invocation may execute after focus has left.
            gui = self.backend.gui
            hwnd = self._hwnd()
            foreground = gui.GetForegroundWindow()
            style = gui.GetWindowLong(hwnd, -20)
            before = {'form_visible': bool(form.Visible), 'view_visible': bool(form.webview.Visible)}
            try:
                # SWP_SHOWWINDOW alone leaves WinForms' managed visibility false.
                # Show the parent through WinForms so visibility reaches WebView2.
                # The temporary native flag prevents background restoration from
                # activating the window; ordinary click behavior is restored below.
                if activate:
                    from System.Windows.Forms import FormWindowState
                    form.WindowState = FormWindowState.Normal
                    # Native minimization can leave WinForms' cached state out
                    # of sync. Restore the HWND too, then show managed content.
                    gui.ShowWindow(hwnd, 9)
                else:
                    gui.SetWindowLong(hwnd, -20, style | 0x08000000)
                # Force the managed child visibility event too. Restoring only
                # the HWND can leave CoreWebView2's renderer hidden after a
                # picture viewer, minimization or maximization transition.
                form.webview.Visible = False
                form.Visible = True
                form.webview.Visible = True
                form.webview.BringToFront()
                form.PerformLayout()
                form.webview.Invalidate()
                if activate:
                    form.Activate()
            finally:
                gui.SetWindowLong(hwnd, -20, style)
            preserved = gui.GetForegroundWindow() == foreground
            if not preserved and not activate and gui.GetForegroundWindow() == hwnd:
                try:
                    # Undo activation caused by this restoration only. Never
                    # override a different app the user selected in the meantime.
                    gui.SetForegroundWindow(foreground)
                except Exception:
                    pass
                preserved = gui.GetForegroundWindow() == foreground
            import json
            (self.controller.settings.directory/'native-content-check.json').write_text(json.dumps({
                'before':before,'after':{'form_visible':bool(form.Visible),'view_visible':bool(form.webview.Visible)},
                'focus_preserved':preserved, 'activation_requested':activate,
                'foreground_is_panel':gui.GetForegroundWindow()==hwnd,
                'window_state':str(form.WindowState),
                'pid':__import__('os').getpid()},indent=2),encoding='utf-8')
        form.Invoke(Action(restore))

    def reopen(self):
        """An explicit launcher request restores parent and child together."""
        with self.lock:
            self.manual_minimized = False
            self._minimized_target_was_active = False
            self.user_opened = True
            self._restore_content(activate=True)
            self.visible = True
            self._opened_last_foreground = self.backend.gui.GetAncestor(
                self.backend.gui.GetForegroundWindow(), 2)
            self._last_foreground = None
            self._record({'status':'manual_open','source':'launcher'})

    def _hide_content(self):
        """WinForms must own hiding so WebView2 receives visibility events."""
        form = getattr(getattr(self.controller, 'window', None), 'native', None)
        if form is None:
            self.backend.gui.ShowWindow(self._hwnd(), 0)
            return
        from System import Action
        def hide():
            if not self.is_active():
                return
            form.webview.Visible = False
            form.Hide()
        form.Invoke(Action(hide))

    @contextmanager
    def capture_guard(self):
        if not self.is_active():
            raise ActivityPaused('已暂停后台识别，请回到微信或心动微信。')
        with self.lock:
            self.suspended += 1
            # Do not hide the panel to bypass the both-windows-visible policy.
            # A true visual capture still has its own independent area check.
        try:
            yield
        finally:
            with self.lock:
                self.suspended -= 1

    def _frame(self):
        gui, api, con = self.backend.gui, self.backend.api, self.backend.con
        panel = self._hwnd()
        if not panel:
            return
        if gui.IsIconic(panel) or self.visible and not gui.IsWindowVisible(panel):
            self.manual_minimized = True
            self.user_opened = False
            self.visible = False
            self._pause_work()
        if self.manual_minimized:
            if gui.IsIconic(panel) or not gui.IsWindowVisible(panel):
                self._record({'status': 'minimized', 'active': False})
                return
            # Only an actual taskbar/launcher restore clears explicit dismissal.
            self.manual_minimized = False
            self.visible = True
        if not self.is_active():
            # Standby observes foreground handles only. Do not discover/move,
            # hide/show, change z-order, or restore either background window.
            self._minimized_target_was_active = False
            self._last_foreground = gui.GetForegroundWindow()
            self._opened_last_foreground = self._last_foreground
            self.diagnostic = {**self.diagnostic, 'status': 'background_paused', 'active': False}
            return
        if self.user_opened:
            # Do not turn an explicit launch into an invisible panel simply
            # because WeChat is missing, ambiguous or has no right-side space.
            current_foreground = gui.GetAncestor(gui.GetForegroundWindow(), 2)
            try:
                opened_target = self._target_window()
                self.target = opened_target['hwnd']
                returning_to_wechat = (current_foreground == self.target
                    and self._opened_last_foreground not in {None, self.target})
            except Exception:
                returning_to_wechat = False
            self._opened_last_foreground = current_foreground
            if not returning_to_wechat:
                if not self.suspended and not self.visible:
                    self._restore_content()
                    self.visible = True
                self._record({'status':'manual_open','source':'launcher'})
                return
            self.user_opened = False
        try:
            target = self._target_window()
            self.target = target['hwnd']
            fg = gui.GetAncestor(gui.GetForegroundWindow(), 2)
            active = fg in {panel, self.target}
            monitor = api.MonitorFromRect(tuple(target['rect']), con.MONITOR_DEFAULTTONEAREST)
            work = api.GetMonitorInfo(monitor)['Work']
            pr = gui.GetWindowRect(panel)
            size = (pr[2]-pr[0], pr[3]-pr[1])
            dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(panel)) or 96
            zoomed = ctypes.windll.user32.IsZoomed
            zoomed.argtypes = [ctypes.c_void_p]
            zoomed.restype = ctypes.c_int
            plan = placement(target['rect'], work, size, bool(zoomed(ctypes.c_void_p(self.target))),
                             self.controller.settings.values['maximized_mode'], dpi/96)
            # Layered alpha can blank standard WinForms WebView2. A native
            # transparent overlay needs a composition controller instead.
            if plan and plan[2]:
                plan = None
        except Exception as error:
            self._record({'status':'error','error':str(error),'code':type(error).__name__})
            plan, active = None, False
        if plan is None:
            if self.diagnostic.get('status') != 'error':
                self._record({'status':'hidden','reason':'no_right_space_or_maximized'})
            if self.visible and self.is_active():
                self._hide_content()
                self.visible = False
            return
        if self.suspended:
            return  # An adjacent panel need not disappear during title reading.
        if not self.is_active():
            return
        x, y, overlay = plan
        restoring = not self.visible
        was_active = self._last_foreground in {panel, self.target}
        entering = active and (not self.visible or not was_active or fg != self._last_foreground)
        moved = (pr[0],pr[1]) != (x,y)
        if entering:
            gui.SetWindowPos(panel,0,x,y,0,0,0x0001|0x0004|0x0010|0x0040)
        elif moved:
            gui.SetWindowPos(panel,0,x,y,0,0,0x0001|0x0004|0x0010)
        if restoring:
            self._restore_content()
            gui.SetWindowPos(panel,0,x,y,0,0,0x0001|0x0004|0x0010|0x0040)
        self._last_foreground = fg
        self.visible = True
        self._record({'status':'attached','target_rect':target['rect'],
                      'panel_rect':list(gui.GetWindowRect(panel)),'overlay':False,'alpha':255,
                      'active':active})

    def _run(self):
        from ctypes import wintypes
        user = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                           wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        dirty = threading.Event()
        def changed(hook,event,hwnd,object_id,child_id,thread_id,event_time):
            if event != 3 and object_id != 0:
                return  # Ignore caret/content animation, observe window objects.
            if not self.work_state(hwnd if event == 3 else None)['active']:
                self._pause_work()  # Remember even a brief cover/leave/restore.
            dirty.set()
        callback = callback_type(changed)
        user.SetWinEventHook.restype = wintypes.HANDLE
        user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, callback_type,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user.UnhookWinEvent.restype = wintypes.BOOL
        for low, high in ((3, 3), (0x16, 0x17), (0x8002, 0x8004), (0x800b, 0x800b)):
            hook = user.SetWinEventHook(low, high, None, callback, 0, 0, 0)
            if hook:
                self._hooks.append(hook)
        msg = wintypes.MSG()
        last = 0
        try:
            while not self.stop.wait(.016):
                while user.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    user.TranslateMessage(ctypes.byref(msg))
                    user.DispatchMessageW(ctypes.byref(msg))
                if dirty.is_set() or time.monotonic()-last >= .3:
                    dirty.clear()
                    last = time.monotonic()
                    with self.lock:
                        try:
                            if self.controller.state['mode'] != 'demo':
                                self._frame()
                        except Exception as error:
                            self._record({'status':'error','error':str(error),'code':type(error).__name__})
        finally:
            for hook in self._hooks:
                user.UnhookWinEvent(hook)

    def close(self):
        self.stop.set()
