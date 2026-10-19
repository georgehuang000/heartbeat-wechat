"""Narrow Qt accessibility lease for the locally reviewed Weixin DLL.

Gate location and instruction signature researched from fanyuantaier/
wechatauto-replica (Apache-2.0), wechatauto/uia_driver.py. No sending stack,
DLL injection, screen-reader setting or on-disk WeChat modification.
"""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import threading
from .activity import ActivityPaused, check_activity

KNOWN_VERSION = '4.1.13.65'
KNOWN_GATE_RVA = 0x0AE2B0C8
PATTERN = re.compile(rb'\x48\x85\xc9\x0f\x84....\x80\x3d(?P<disp>.{4})\x00\x0f\x84', re.DOTALL)


def validate_gate(data, rva=KNOWN_GATE_RVA):
    if len(data) < 64:
        raise ValueError('invalid PE')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('invalid PE')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    if machine != 0x8664 or not 1 <= count <= 96:
        raise ValueError('not reviewed x64 PE')
    opt_size = struct.unpack_from('<H', data, pe + 20)[0]
    sections = []
    for i in range(count):
        off = pe + 24 + opt_size + i * 40
        size, address, raw_size, raw_ptr = struct.unpack_from('<IIII', data, off + 8)
        flags = struct.unpack_from('<I', data, off + 36)[0]
        sections.append((address, max(size, raw_size), raw_ptr, raw_size, flags))
    target = next((s for s in sections if s[0] <= rva < s[0] + s[1]), None)
    if target is None or not target[4] & 0x80000000 or target[4] & 0x20000000:
        raise ValueError('gate is not in non-executable writable data')
    matches = []
    for address, size, raw_ptr, raw_size, flags in sections:
        if not flags & 0x20000000:
            continue
        end = min(len(data), raw_ptr + raw_size)
        for match in PATTERN.finditer(data, raw_ptr, end):
            cmp_rva = address + match.start('disp') - raw_ptr - 2
            destination = cmp_rva + 7 + struct.unpack('<i', match.group('disp'))[0]
            if destination == rva:
                matches.append(address + match.start() - raw_ptr)
    if len(matches) != 1 or b'qt.accessibility.core' not in data:
        raise ValueError('known gate does not have exactly one verified code reference')
    return {'gate_rva': hex(rva), 'code_reference_rva': hex(matches[0])}


KNOWN_DLL_SHA256 = '8f7406a8a465e851ee10eaecccb572d0e5b4e00d480c770938c714c396097b74'

REVIEWED_BUILDS = {
    KNOWN_VERSION: {'sha256': KNOWN_DLL_SHA256, 'gate_rva': KNOWN_GATE_RVA},
    # 2026-10-19: local PE/instruction and reversible UIA verification.
    '4.1.15.55': {
        'sha256': '6fbbba536b04c0a24730710f540887a53f4eaa24398e4f94905d9b4a36148595',
        'gate_rva': 0x0B39C9D8,
    },
}


class UnreviewedWeChatBuild(ValueError):
    def __init__(self, version):
        self.version = version
        super().__init__(f'微信 {version} 尚未通过当前程序的兼容检查')


def reviewed_build(version, dll_sha256):
    build = REVIEWED_BUILDS.get(version)
    if build is None or build['sha256'] != dll_sha256:
        raise UnreviewedWeChatBuild(version)
    return build


class WindowsGate:
    """Own one process handle; only the reviewed writable data byte is mutable."""
    def __init__(self, window):
        import win32api
        import win32process
        self.handle = win32api.OpenProcess(0x0400 | 0x0010 | 0x0020 | 0x0008, False, window['pid'])
        self.address = None
        try:
            actual_exe = win32process.GetModuleFileNameEx(self.handle, 0)
            if Path(actual_exe).resolve() != Path(window['executable']).resolve():
                raise ValueError('process executable changed')
            loaded = [(m, Path(win32process.GetModuleFileNameEx(self.handle, m)))
                      for m in win32process.EnumProcessModules(self.handle)]
            loaded = [(m, p) for m, p in loaded if p.name.lower() == 'weixin.dll']
            if len(loaded) != 1 or sys.maxsize <= 2**32:
                raise ValueError('no unique reviewed x64 module')
            base, path = loaded[0]
            version = win32api.GetFileVersionInfo(str(path), '\\')
            actual = '.'.join(str(n) for n in (version['FileVersionMS'] >> 16, version['FileVersionMS'] & 65535,
                                             version['FileVersionLS'] >> 16, version['FileVersionLS'] & 65535))
            data = path.read_bytes()
            build = reviewed_build(actual, hashlib.sha256(data).hexdigest())
            gate_rva = build['gate_rva']
            evidence = validate_gate(data, gate_rva)
            # Compare the loaded instruction too; disk identity alone is insufficient.
            ref_rva = int(evidence['code_reference_rva'], 16)
            instruction = self._read(int(base) + ref_rva, 18)
            match = PATTERN.fullmatch(instruction)
            if match is None or ref_rva + 16 + struct.unpack('<i', match.group('disp'))[0] != gate_rva:
                raise ValueError('loaded accessibility instruction differs')
            self.address = int(base) + gate_rva
            self.version = actual
        except Exception:
            self.close()
            raise

    @staticmethod
    def _kernel():
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signature = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        kernel.ReadProcessMemory.argtypes = signature
        kernel.WriteProcessMemory.argtypes = signature
        return kernel

    def _read(self, address, length):
        buffer = ctypes.create_string_buffer(length)
        size = ctypes.c_size_t()
        if not self._kernel().ReadProcessMemory(int(self.handle), address, buffer, length, ctypes.byref(size)) or size.value != length:
            raise OSError('accessibility state read failed')
        return buffer.raw

    def read(self):
        return self._read(self.address, 1)[0]

    def write(self, value):
        buffer, size = ctypes.c_ubyte(value), ctypes.c_size_t()
        if not self._kernel().WriteProcessMemory(int(self.handle), self.address, ctypes.byref(buffer), 1, ctypes.byref(size)) or size.value != 1:
            raise OSError('accessibility state write failed')

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


class NativeAccessibility:
    """One reviewed activation attempt per window/process, restored on close."""
    def __init__(self, factory=None):
        self.factory = factory or WindowsGate
        self._gate = None
        self._original = None
        self._key = None
        self._attempted = None
        self._closed = False
        self._lock = threading.RLock()
        self.diagnostic = {'status': 'not_attempted'}

    def ensure(self, window):
        check_activity(getattr(self, 'activity_check', None))
        if os.name != 'nt' and self.factory is WindowsGate:
            return False
        key = (window.get('pid'), window.get('hwnd'), window.get('fingerprint'))
        with self._lock:
            if self._closed:
                return False
            if key == self._key and self._gate is not None:
                try:
                    return self._gate.read() == 1
                except Exception:
                    self._restore()
                    self.diagnostic = {'status': 'process_unavailable'}
                    return False
            if key == self._attempted:
                return False
            self._restore()
            self._attempted = key
            try:
                self._gate = self.factory(window)
                self._original = self._gate.read()
                if self._original not in (0, 1):
                    raise ValueError('unexpected accessibility state')
                if self._original == 0:
                    check_activity(getattr(self, 'activity_check', None))
                    self._gate.write(1)
                if self._gate.read() != 1:
                    raise OSError('accessibility activation not accepted')
                self._key = key
                self.diagnostic = {'status': 'active', 'version': getattr(self._gate, 'version', KNOWN_VERSION)}
                return True
            except ActivityPaused:
                self._attempted = None
                raise  # Defer any lease cleanup to a foreground retry or explicit exit.
            except Exception as error:
                self._restore()
                self.diagnostic = {'status': 'unsupported', 'reason': str(error)}
                if isinstance(error, UnreviewedWeChatBuild):
                    self.diagnostic.update(code='unsupported_version', version=error.version)
                return False

    def _restore(self):
        gate = self._gate
        if gate is not None:
            try:
                if self._original == 0 and gate.read() == 1:
                    gate.write(0)
            except Exception:
                # A terminated process has nothing left to restore. Its handle is
                # retained until here, so a reused PID cannot redirect the write.
                self.diagnostic = {'status': 'restore_unavailable'}
            finally:
                gate.close()
        self._gate = self._original = self._key = None

    def close(self):
        with self._lock:
            self._restore()
            self._closed = True
