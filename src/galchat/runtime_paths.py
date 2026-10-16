"""Keep immutable bundled resources separate from the user's writable data."""
from pathlib import Path
import os
import sys


def resource_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[2]


def data_directory(override=None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        local = os.environ.get('LOCALAPPDATA')
        return (Path(local) if local else Path.home() / 'AppData/Local') / 'HeartWeChat'
    return resource_root() / 'data'


def prepare_windowed_streams() -> None:
    # Windowed PyInstaller has no stdout/stderr. Uvicorn's formatter and
    # third-party diagnostic print calls still need file-like objects.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')


def report_startup_error() -> None:
    import traceback
    folder = data_directory()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'startup-error.log'
    path.write_text(traceback.format_exc(), encoding='utf-8')
    if os.name == 'nt':
        import ctypes
        from .brand import APP_NAME
        ctypes.windll.user32.MessageBoxW(None,
            f'程序启动失败，诊断记录保存在：\n{path}', APP_NAME, 0x10)
