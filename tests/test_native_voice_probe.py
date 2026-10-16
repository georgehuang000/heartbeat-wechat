"""Native transcript research parser checks; no desktop or private DB reads."""
import importlib.util
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[1] / 'scripts/probe_native_voice.py'
spec = importlib.util.spec_from_file_location('native_voice_probe_tests', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def uint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128); value >>= 7
    return bytes(result + bytes([value]))


def blob(field, value):
    return uint(field << 3 | 2) + uint(len(value)) + value


def test_native_voice_field_path_has_text_shape_without_exporting_text():
    text = '今天上班好累，先吃饭。'
    data = b'\x08\x01' + blob(5, b'\x08\x02' + blob(2, text.encode())) + b'\x58\x00'
    result = probe.inspect_packed(data)
    assert result['valid'] and result['candidate_count'] == 1
    assert result['field5_children'][0]['field1_varints'] == [2]
    assert result['field5_children'][0]['texts'][0]['characters'] == len(text)
    assert text not in repr(result)


def test_unrelated_text_field_is_not_a_transcript():
    assert probe.inspect_packed(blob(3, '图片文件名'.encode()))['candidate_count'] == 0


def test_duplicate_fields_are_visible_not_silently_first_wins():
    child = blob(2, b'one') + blob(2, b'two')
    assert probe.inspect_packed(blob(5, child))['candidate_count'] == 2


@pytest.mark.parametrize('data', [None, 'text', b'\x00', b'\x0b', b'\x08\x80',
    b'\x08' + b'\xff'*9 + b'\x02', b'\x2a\x10\x01', b'\x09\x01',
    b'\x0d\x01', b'\x08\x00'*257, b'\x08\x00'*32769],
    ids=['none','string','zero-tag','group','short-varint','overflow','short-bytes',
         'short-fixed64','short-fixed32','too-many','too-large'])
def test_malformed_or_oversized_buffer_does_not_report_a_candidate(data):
    result = probe.inspect_packed(data)
    assert not result['valid'] and result['candidate_count'] == 0


@pytest.mark.parametrize('text', [b'\xff\xfe', b'\0hidden', b' ', b'a'*12001],
    ids=['bad-utf8','control-char','blank','too-long'])
def test_invalid_text_is_not_readable_native_content(text):
    assert probe.inspect_packed(blob(5, blob(2, text)))['candidate_count'] == 0


def test_truncated_nested_proto_is_rejected():
    assert not probe.inspect_packed(blob(5, b'\x12\x10\x01'))['valid']
