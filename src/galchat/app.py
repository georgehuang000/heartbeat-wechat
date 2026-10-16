from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets

from fastapi import FastAPI, HTTPException, Request

from .controller import Controller
from .runtime_paths import resource_root, data_directory
from .brand import APP_NAME


class Bridge:
    def __init__(self, controller):
        self._controller = controller

    def dispatch(self, method, payload=None):
        return self._controller.dispatch(method, payload)


def create_server(controller: Controller, root: Path, token: str):
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/api/{method}")
    async def dispatch(method: str, request: Request):
        import asyncio
        if not secrets.compare_digest(request.headers.get("X-Galchat-Token", ""), token):
            raise HTTPException(403, "Session token required")
        if int(request.headers.get("Content-Length", "0")) > 3_000_000:
            raise HTTPException(413, "Request too large")
        payload = await request.json()
        return await asyncio.to_thread(controller.dispatch, method, payload)

    frontend = root / 'prototype-heart-wechat' / 'dist' / 'client'
    if not (frontend / 'index.html').exists():
        frontend = root / 'web'

    @app.get("/")
    def index():
        return FileResponse(frontend / "index.html")

    app.mount("/", StaticFiles(directory=frontend), name="web")
    return app


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} · galgame聊天提词器")
    parser.add_argument("--serve", action="store_true", help="开发时在本地浏览器检查界面")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--data-dir")
    parser.add_argument('--check-runtime', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = resource_root()
    directory = data_directory(args.data_dir)
    if args.check_runtime:
        from .packaging_check import check_runtime
        check_runtime(root, directory)
        return
    singleton = None
    if not args.serve and os.name == "nt":
        from .desktop_instance import DesktopInstance
        singleton = DesktopInstance()
        if singleton.already_running:
            singleton.request_open()
            singleton.close()
            return
    controller = Controller(root, directory)
    if args.demo:
        controller.dispatch("demo", {"enabled": True})
    try:
        if args.serve:
            import uvicorn
            token = secrets.token_urlsafe(32)
            print(f"http://127.0.0.1:{args.port}/#token={token}", flush=True)
            uvicorn.run(create_server(controller, root, token), host="127.0.0.1", port=args.port,
                        access_log=False, log_level="warning")
        else:
            import webview
            import socket
            import threading
            import uvicorn
            from .window_group import WindowGroup
            from .visual_wechat import Win32ScreenBackend
            if os.name == 'nt':
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('HeartWeChat.Desktop')
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            sock.listen(128)
            port = sock.getsockname()[1]
            token = secrets.token_urlsafe(32)
            if os.environ.get('GALCHAT_ACCEPTANCE_SESSION') == '1':
                # Explicit local QA only; never shipped in the EXE or emitted
                # to a report. Allows the existing API to be checked without
                # capturing another app or altering any WeChat draft.
                import json
                (directory / '_acceptance-session.json').write_text(json.dumps({
                    'pid': os.getpid(), 'port': port, 'token': token}), encoding='utf-8')
            print(f'http://127.0.0.1:{port}/?desktop=1#token={token}', flush=True)
            server = uvicorn.Server(uvicorn.Config(create_server(controller, root, token),
                                    access_log=False, log_level='warning'))
            threading.Thread(target=lambda:server.run(sockets=[sock]), daemon=True, name='local-ui-server').start()
            import time
            deadline=time.monotonic()+5
            while not server.started and time.monotonic()<deadline:
                time.sleep(.02)
            if not server.started:
                raise RuntimeError('本机界面服务未能启动。')
            window = webview.create_window(APP_NAME, f'http://127.0.0.1:{port}/?desktop=1#token={token}',
                js_api=Bridge(controller), width=460, height=620, min_size=(400, 540),
                background_color="#fff5fa", on_top=False, text_select=True,
                frameless=True, easy_drag=False)
            controller.window = window
            controller.window_group = WindowGroup(controller, Win32ScreenBackend())
            window.events.closed += controller.close
            def inspect_render():
                # Own-window DOM diagnostics contain no contact/message text.
                import json
                result=window.evaluate_js("""(() => ({
                    ready:!!document.querySelector('.game-window'),
                    width:innerWidth,height:innerHeight,
                    images:[...document.images].filter(i=>i.complete&&i.naturalWidth>0).length,
                    missing_images:[...document.images].filter(i=>!i.complete||!i.naturalWidth).length,
                    choices:document.querySelectorAll('.choice').length
                }))()""")
                result['pid'] = os.getpid()
                result['checked_at'] = time.time()
                result['frozen'] = bool(getattr(__import__('sys'), 'frozen', False))
                with controller.lock:
                    result['connection'] = {k: controller.state['connection'].get(k)
                        for k in ('status', 'can_read', 'can_fill')}
                    result['contact_resolved'] = bool(controller.state.get('contact'))
                    result['messages_count'] = len(controller.state.get('messages', []))
                    result['identity_mode'] = getattr(controller.current_adapter, 'identity_mode', None)
                from System import Action
                def native_state():
                    form=window.native
                    view=form.webview
                    result['native']={'form_visible':bool(form.Visible),'view_visible':bool(view.Visible),
                        'view_width':int(view.Width),'view_height':int(view.Height),
                        'form_width':int(form.ClientSize.Width),'form_height':int(form.ClientSize.Height),
                        'window_state':str(form.WindowState)}
                window.native.Invoke(Action(native_state))
                if os.environ.get('GALCHAT_CAPTURE_OPEN') == '1' and window.native.InvokeRequired:
                    # Test-only page pixels from this WebView, even when another
                    # application covers it. Start on the UI thread, wait here
                    # on the background callback so the UI pump stays free.
                    from System.IO import MemoryStream
                    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                    stream, capture = MemoryStream(), {}
                    def begin_capture():
                        capture['task'] = window.native.webview.CoreWebView2.CapturePreviewAsync(
                            CoreWebView2CapturePreviewImageFormat.Png, stream)
                    window.native.Invoke(Action(begin_capture))
                    if capture['task'].Wait(5000):
                        picture = directory / f'desktop-page-{os.getpid()}-{time.time_ns()}.png'
                        picture.write_bytes(bytes(stream.ToArray()))
                        result['page_preview'] = picture.name
                        stream.Close()
                (directory/'ui-render-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            def loaded_render():
                inspect_render()
                # Do not hide the form while WebView2 creates its native child.
                # A hidden-at-initialization controller can keep rendering
                # disabled even after the outer HWND is shown again.
                controller.window_group.start()
                timer=threading.Timer(1,inspect_render)
                timer.daemon=True
                timer.start()
                if singleton is not None:
                    def reopen():
                        try:
                            controller.window_group.reopen()
                            inspect_render()
                        except Exception as error:
                            import json
                            (directory/'desktop-open-error.json').write_text(json.dumps({
                                'type':type(error).__name__, 'error':str(error)},ensure_ascii=False),encoding='utf-8')
                    singleton.listen(reopen)
            window.events.loaded += loaded_render
            icon = root / 'assets' / 'heart-wechat.ico'
            webview.start(gui="edgechromium", debug=bool(os.environ.get("GALCHAT_DEBUG")),
                          private_mode=True, icon=str(icon) if icon.is_file() else None)
            server.should_exit = True
    finally:
        controller.close()
        if os.environ.get('GALCHAT_ACCEPTANCE_SESSION') == '1':
            (directory / '_acceptance-session.json').unlink(missing_ok=True)
        if singleton is not None:
            singleton.close()
