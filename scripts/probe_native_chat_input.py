"""Version-bound, reversible Qt accessibility probe. No text writes or sends.

Reference: wechatauto-replica uia_driver.py (Apache-2.0), already reviewed locally.
Default is read-only. --activate temporarily tests the exact known Qt gate and
restores its original byte in finally. It never imports the sending package.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from galchat.visual_wechat import Win32ScreenBackend
from galchat.wechat import _aid, _get, _rect, _value, _walk

from galchat.native_accessibility import KNOWN_VERSION, KNOWN_GATE_RVA, PATTERN, validate_gate


def inspect(window):
    import uiautomation as auto
    started = time.perf_counter()
    nodes = list(_walk(auto.ControlFromHandle(window['hwnd'])))
    edits = [n for n in nodes if _get(n, 'ClassName') == 'mmui::ChatInputField' or _aid(n, 'chat_input_field')]
    result = {'nodes': len(nodes), 'chat_inputs': len(edits), 'read_ms': round((time.perf_counter() - started) * 1000, 2)}
    result['input_candidates'] = [{'class': _get(e, 'ClassName'), 'type': _get(e, 'ControlTypeName'),
        'aid': _get(e, 'AutomationId'), 'offscreen': bool(_get(e, 'IsOffscreen', False)),
        'rect': _rect(e), 'name_length': len(str(_get(e, 'Name'))),
        'pattern': type(_value(e)[0]).__name__,
        'value_length': len(_value(e)[1]) if _value(e)[1] is not None else None} for e in edits]
    edits = [e for e in edits if _get(e, 'ClassName') == 'mmui::ChatInputField']
    if len(edits) == 1:
        edit = edits[0]
        pattern, value = _value(edit)
        name = str(_get(edit, 'Name'))
        normalized = name.strip()
        for suffix in ('按住鼠标 语音输入文字', '按住鼠标语音输入文字', '语音输入文字'):
            if value == '' and normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)].strip()
                break
        matches = None
        for path in (ROOT / 'data/readonly-db-probe/cache').rglob('*contact*.db'):
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
                matches = conn.execute("SELECT count(*) FROM contact WHERE (CASE WHEN coalesce(remark,'')<>'' THEN remark WHEN coalesce(nick_name,'')<>'' THEN nick_name ELSE username END)=?", (normalized,)).fetchone()[0]
            break
        result.update(name_length=len(name), name_hash=hashlib.sha256(name.encode()).hexdigest(),
                      normalized_name_length=len(normalized), exact_contact_matches=matches,
                      draft_readable=value is not None,
                      draft_length=len(value) if value is not None else None,
                      draft_hash=hashlib.sha256(value.encode()).hexdigest() if value is not None else None,
                      value_pattern=type(pattern).__name__ if pattern else None,
                      read_only=bool(_get(pattern, 'IsReadOnly', False)) if pattern else None,
                      set_value_available=callable(getattr(pattern, 'SetValue', None)),
                      text_pattern_available=bool(edit.GetTextPattern()),
                      focused=bool(_get(edit, 'HasKeyboardFocus', False)))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--hold-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.hold_seconds <= 60:
        raise SystemExit('hold must be 0..60 seconds')
    import uiautomation as auto
    import win32api
    import win32process
    window = Win32ScreenBackend().window(include_minimized=True)
    report = {'version': KNOWN_VERSION, 'activation_requested': args.activate,
              'text_writes': 0, 'sent': False}
    handle = None
    original = None
    address = None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.WriteProcessMemory.argtypes = kernel.ReadProcessMemory.argtypes
    def read_byte():
        buf, size = ctypes.c_ubyte(), ctypes.c_size_t()
        if not kernel.ReadProcessMemory(int(handle), address, ctypes.byref(buf), 1, ctypes.byref(size)) or size.value != 1:
            raise OSError('gate read failed')
        return buf.value
    def write_byte(value):
        buf, size = ctypes.c_ubyte(value), ctypes.c_size_t()
        if not kernel.WriteProcessMemory(int(handle), address, ctypes.byref(buf), 1, ctypes.byref(size)) or size.value != 1:
            raise OSError('gate write failed')
    with auto.UIAutomationInitializerInThread():
        try:
            report['before'] = inspect(window)
            if args.activate:
                handle = win32api.OpenProcess(0x0400 | 0x0010 | 0x0020 | 0x0008, False, window['pid'])
                modules = [(m, Path(win32process.GetModuleFileNameEx(handle, m))) for m in win32process.EnumProcessModules(handle)]
                loaded = [(m, p) for m, p in modules if p.name.lower() == 'weixin.dll']
                if len(loaded) != 1:
                    raise ValueError('no unique loaded Weixin.dll')
                base, path = loaded[0]
                version = win32api.GetFileVersionInfo(str(path), '\\')
                actual = '.'.join(str(n) for n in (version['FileVersionMS'] >> 16, version['FileVersionMS'] & 65535, version['FileVersionLS'] >> 16, version['FileVersionLS'] & 65535))
                if actual != KNOWN_VERSION or sys.maxsize <= 2**32:
                    raise ValueError('version or architecture not reviewed')
                data = path.read_bytes()
                report.update(dll_sha256=hashlib.sha256(data).hexdigest(), **validate_gate(data))
                address = int(base) + KNOWN_GATE_RVA
                original = read_byte()
                if original not in (0, 1):
                    raise ValueError('gate has an unexpected value')
                report['original_gate'] = original
                if original == 0:
                    write_byte(1)
                report['active_gate'] = read_byte()
                # No system settings changed. Query the provider after gate-only activation.
                report['after'] = inspect(window)
                if args.hold_seconds:
                    print(json.dumps({'phase':'active','report':report}, ensure_ascii=False), flush=True)
                    time.sleep(args.hold_seconds)
        except Exception as error:
            report['error_type'] = type(error).__name__
            report['error'] = str(error)
        finally:
            if handle is not None:
                try:
                    if original in (0, 1) and address is not None and read_byte() != original:
                        write_byte(original)
                    report['restored_gate'] = read_byte() if original in (0, 1) and address is not None else None
                finally:
                    handle.Close()
    target = ROOT / 'docs/evaluation/native-chat-input-probe-2026-10-13.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
