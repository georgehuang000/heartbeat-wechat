"""One Windows desktop process; later launches ask its UI thread to reopen.

The named event carries only an open request. It never carries chat or tokens.
Win32 ShowWindow in a second process cannot restore a hidden WinForms child.
"""
import ctypes
import threading
from .brand import APP_NAME, LEGACY_APP_NAME


class DesktopInstance:
    def __init__(self, *, api=None, event_api=None, title=APP_NAME):
        if api is None:
            import win32api as api
        if event_api is None:
            import win32event as event_api
        self.api, self.events = api, event_api
        self.title = title
        self.stop = threading.Event()
        self.thread = None
        self.mutex = self.events.CreateMutex(None, False, 'Local\\HeartWeChatDesktop')
        # Capture before CreateEvent changes the thread's last-error value.
        self.already_running = self.api.GetLastError() == 183
        self.event = self.events.CreateEvent(None, False, False, 'Local\\HeartWeChatDesktop.Open')

    def request_open(self):
        try:
            import win32gui
            import win32process
            hwnd = win32gui.FindWindow(None, self.title)
            if not hwnd and self.title == APP_NAME:
                hwnd = win32gui.FindWindow(None, LEGACY_APP_NAME)
            if hwnd:
                owner = win32process.GetWindowThreadProcessId(hwnd)[1]
                ctypes.windll.user32.AllowSetForegroundWindow(owner)
        except Exception:
            pass  # Showing managed content still works without focus permission.
        self.events.SetEvent(self.event)

    def listen(self, callback):
        if self.thread is not None:
            return
        def run():
            while not self.stop.is_set():
                signal = self.events.WaitForSingleObject(self.event, 200)
                if self.stop.is_set():
                    return
                if signal == self.events.WAIT_OBJECT_0:
                    callback()
        self.thread = threading.Thread(target=run, name='desktop-open-request', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.events.SetEvent(self.event)
            self.thread.join(timeout=1)
        self.event.Close()
        self.mutex.Close()
