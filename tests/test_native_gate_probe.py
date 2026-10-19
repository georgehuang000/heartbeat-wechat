"""Validate the isolated probe's gate constraints without any process access."""
import importlib.util
from pathlib import Path
import struct
import pytest
from galchat.native_accessibility import reviewed_build, REVIEWED_BUILDS, UnreviewedWeChatBuild

path = Path(__file__).resolve().parents[1] / 'scripts/probe_native_chat_input.py'
spec = importlib.util.spec_from_file_location('native_gate_probe', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def pe_fixture():
    data = bytearray(4096)
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x84, 0x8664, 2)
    struct.pack_into('<H', data, 0x94, 0xf0)
    table = 0x80 + 24 + 0xf0
    struct.pack_into('<IIII', data, table + 8, 0x200, 0x1000, 0x200, 0x400)
    struct.pack_into('<I', data, table + 36, 0x60000020)
    struct.pack_into('<IIII', data, table + 40 + 8, 0x1000, probe.KNOWN_GATE_RVA & ~0xfff, 0x200, 0x800)
    struct.pack_into('<I', data, table + 40 + 36, 0xc0000040)
    pattern = b'\x48\x85\xc9\x0f\x84' + bytes(4) + b'\x80\x3d' + struct.pack('<i', probe.KNOWN_GATE_RVA - (0x1000 + 16)) + b'\x00\x0f\x84'
    data[0x400:0x400 + len(pattern)] = pattern
    data[0x700:0x700 + len(b'qt.accessibility.core')] = b'qt.accessibility.core'
    return data, table


def test_known_gate_must_have_code_and_writable_data_evidence():
    data, _ = pe_fixture()
    assert probe.validate_gate(data)['gate_rva'] == hex(probe.KNOWN_GATE_RVA)


@pytest.mark.parametrize('version', ['4.1.13.65', '4.1.15.55'])
def test_only_reviewed_version_and_exact_dll_hash_select_the_gate(version):
    build = REVIEWED_BUILDS[version]
    assert reviewed_build(version, build['sha256']) == build
    with pytest.raises(UnreviewedWeChatBuild) as error:
        reviewed_build(version, '0' * 64)
    assert error.value.version == version


def test_unknown_version_cannot_reuse_a_reviewed_dll_gate():
    with pytest.raises(UnreviewedWeChatBuild):
        reviewed_build('4.1.99.99', REVIEWED_BUILDS['4.1.15.55']['sha256'])


@pytest.mark.parametrize('change', ['architecture', 'nonwritable', 'executable', 'wrong_reference', 'missing_core', 'bad_pe'])
def test_unreviewed_or_ambiguous_bytes_are_rejected(change):
    data, table = pe_fixture()
    if change == 'architecture':
        struct.pack_into('<H', data, 0x84, 0x14c)
    elif change == 'nonwritable':
        struct.pack_into('<I', data, table + 40 + 36, 0x40000040)
    elif change == 'executable':
        struct.pack_into('<I', data, table + 40 + 36, 0xe0000040)
    elif change == 'wrong_reference':
        data[0x40b] ^= 1
    elif change == 'missing_core':
        data[0x700] = 0
    else:
        data[0x80] = 0
    with pytest.raises(ValueError):
        probe.validate_gate(data)
