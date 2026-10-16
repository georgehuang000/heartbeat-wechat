import io
import json
from pathlib import Path
import sqlite3
import struct
import wave
import ctypes
import sys
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from galchat.attachments import parse_message, readiness
from galchat.engine import ReplyEngine, ReplyEngineError, _check_unread_claims
from galchat.media import CloudMedia, LocalMedia, decode_voice, image_bytes, image_fingerprint
from galchat.memory import MemoryStore
from galchat.settings import Settings
from galchat.media import scan_image_key, read_image_config
from galchat.engine import _check_self_continuation


def record_xml(count=2):
    return f'<msg><appmsg><type>19</type><title>讨论</title><recorditem><![CDATA[<recordinfo><datalist count="{count}"><dataitem datatype="1"><sourcename>原甲</sourcename><sourcetime>2026-09-01 12:00:00</sourcetime><datadesc>火锅周六去</datadesc></dataitem><dataitem datatype="1"><sourcename>原乙</sourcename><datadesc>我周六有空</datadesc></dataitem></datalist></recordinfo>]]></recorditem></appmsg></msg>'


def test_image_media_index_is_not_confused_with_thumbnail_or_cdn_key():
    primary, thumbnail, key = 'a'*32, 'b'*32, 'c'*32
    row = {'_packed_info': b'\x12\x20'+primary.encode(),
           'content': f'<msg><img md5="{primary}" cdnthumbmd5="{thumbnail}" aeskey="{key}"/></msg>'}
    assert image_fingerprint(row) == primary
    row['_packed_info'] = None
    assert image_fingerprint(row) == primary
    row['_packed_info'] = (primary+' '+thumbnail).encode()
    with pytest.raises(Exception, match='多个指纹'):
        image_fingerprint(row)


def test_forward_text_body_and_original_authors_are_quoted():
    parsed = parse_message((19 << 32) | 49, record_xml())
    assert parsed['kind'] == 'forward' and parsed['content_known']
    assert parsed['attachment']['items'][0]['author'] == '原甲'
    payload = ReplyEngine._payload_context({'recent': [{**parsed, 'id':'f', 'role':'other','timestamp':'2026-10-11T12:00:00'}]})
    assert payload['recent'][0]['attachment']['items'][0]['text'] == '火锅周六去'
    assert payload['timeline']['messages'][0]['timestamp'] == '2026-10-11T12:00:00'


def test_partial_record_is_not_claimed_complete():
    data = parse_message(49, record_xml(5))
    assert not data['content_known']
    assert data['attachment']['missing_count'] == 3
    assert data['attachment']['read_count'] == 2


def test_card_does_not_change_identity_or_expose_username_as_counterpart():
    data = parse_message(42, '<msg nickname="陌生甲" username="third-user"/>')
    assert data['text'] == '[个人名片] 陌生甲'
    assert 'third-user' not in json.dumps(data)
    assert readiness([{**data, 'role':'self'}]) is None


@pytest.mark.parametrize('code,kind', [(50,'call'),(43,'video'),(10000,'system'),(3,'image'),(34,'voice')])
def test_event_and_media_types_preserved(code, kind):
    assert parse_message(code, '')['kind'] == kind


def test_voice_wechat_transcription_has_original_kind_and_duration():
    data = parse_message(34, '<msg><voicemsg voicelength="30000"/><voicetrans>周六去吧</voicetrans></msg>')
    assert data['kind'] == 'voice' and data['content_known']
    assert data['duration_seconds'] == 30
    assert readiness([{**data, 'role':'other'}]) is None


@pytest.mark.parametrize('xml', ['<!DOCTYPE a [<!ENTITY b "x">]><msg>&b;</msg>', '<broken', 'x'*256001], ids=['entity','broken','oversized'])
def test_unusable_xml_preserves_unknown_instead_of_content(xml):
    data = parse_message(49, xml)
    assert not data['content_known']


def test_media_followed_by_known_text_can_continue_but_unread_last_voice_waits():
    voice = {'role':'other','kind':'voice','text':'[语音]','content_known':False}
    assert readiness([voice]) is not None
    assert readiness([voice, {'role':'other','text':'周六去吧','kind':'text'}]) is None


def test_memory_roundtrip_does_not_turn_unknown_voice_into_known_text(tmp_path):
    with MemoryStore(tmp_path/'memory.db') as store:
        store.upsert_messages('one','甲',[{'id':'v','role':'other','text':'[语音]',
            'timestamp':'2026-10-01T12:00:00','kind':'voice','content_known':False,'duration_seconds':30},
            {'id':'f','role':'other','timestamp':'2026-10-11T12:00:00',**parse_message(49,record_xml())}])
        rows = store.get_context('one')['recent']
        assert rows[0]['kind']=='voice' and not rows[0]['content_known']
        assert rows[1]['attachment']['items'][0]['author']=='原甲'


def test_silk_decoder_outputs_one_second_wav_for_tencent_and_standard_headers():
    import pysilk
    pcm = b''.join(struct.pack('<h', (i % 100)*10) for i in range(24000))
    silk = pysilk.encode(pcm, sample_rate=24000)
    for data in (silk, silk[1:] if silk.startswith(b'\x02') else silk):
        wav = wave.open(io.BytesIO(decode_voice(data)))
        assert wav.getframerate()==24000 and wav.getnframes()==24000
        assert wav.getnchannels()==1


def test_image_validation_requires_readable_image_not_just_magic_bytes():
    with pytest.raises(OSError):
        image_bytes(b'\x89PNG\r\n\x1a\ntruncated')
    buf=io.BytesIO(); Image.new('RGB',(3000,3000),'pink').save(buf,format='PNG')
    resized=Image.open(io.BytesIO(image_bytes(buf.getvalue())))
    assert max(resized.size)<=2048


def test_asr_uses_its_own_multipart_key_and_result():
    def handler(request):
        assert request.url.path=='/v1/audio/transcriptions'
        assert request.headers['Authorization']=='Bearer speech-only'
        assert request.headers['Content-Type'].startswith('multipart/form-data')
        assert b'voice.wav' in request.content
        return httpx.Response(200,json={'text':'周六去吧'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert CloudMedia(client).transcribe(b'RIFFfake',{'base_url':'https://api.example.test/v1','model':'asr','api_key':'speech-only'})=='周六去吧'


def test_missing_asr_key_sends_nothing():
    def handler(request):
        raise AssertionError('must not send anything')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError,match='密钥'):
            CloudMedia(client).transcribe(b'RIFFfake',{'base_url':'https://api.example.test','model':'asr'})


def test_image_api_receives_selected_image_and_marks_thumbnail():
    def handler(request):
        data=json.loads(request.content)
        assert '缩略图' in data['messages'][0]['content']
        assert len(data['messages'][1]['content'])==2
        return httpx.Response(200,json={'choices':[{'message':{'content':'图中是粉色方块。'}}]})
    buf=io.BytesIO();Image.new('RGB',(50,50),'pink').save(buf,format='JPEG')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert '粉色' in CloudMedia(client).describe(buf.getvalue(),{'base_url':'https://api.example.test','model':'vision'},'thumb')


def test_settings_keep_speech_key_private_and_separate(tmp_path):
    settings=Settings(tmp_path)
    settings.save({'api_key':'deepseek-key','asr_api_key':'speech-key','media_policy':'auto'})
    assert settings.model_config()['api_key']=='deepseek-key'
    assert settings.asr_config()['api_key']=='speech-key'
    public=settings.public()
    assert public['asr_ready'] and 'protected_asr_key' not in public
    assert 'speech-key' not in settings.path.read_text()
    assert 'protected_asr_key' not in settings.model_config()


def test_quote_prompt_budget_keeps_latest_target_without_exporting_raw_fields():
    parsed=parse_message(49,record_xml())
    parsed['attachment']['secret_key']='must-not-export'
    payload=ReplyEngine._payload_context({'recent':[{'id':str(i),'role':'other',**parsed} for i in range(100)]+[
        {'id':'target','role':'other','kind':'text','text':'你觉得呢？'}]})
    assert payload['recent'][-1]['id']=='target'
    assert len(json.dumps(payload,ensure_ascii=False))<45000
    assert 'must-not-export' not in json.dumps(payload)


def test_software_unread_does_not_become_user_unread_or_invented_gossip():
    context = {'recent': [{'role':'other','kind':'forward','text':'[合并聊天记录]','content_known':False},
                          {'role':'other','kind':'text','text':'你怎么看？','content_known':True}]}
    for text in ('我还没仔细看里面的内容', '我不敢乱看八卦', '这有什么槽点？'):
        with pytest.raises(ReplyEngineError):
            _check_unread_claims([{'text':text}], context)
    _check_unread_claims([{'text':'你最想聊里面哪一点？'}], context)
    context['recent'].insert(1, {'role':'self','kind':'text','text':'我还没看','content_known':True})
    _check_unread_claims([{'text':'我还没看，想先听听你的看法'}], context)


@pytest.mark.parametrize('plain_header,stored_suffix',[(b'\xff\xd8\xff',b'abcdefghijklmnop'),(b'wxgf',b'')])
def test_bounded_image_key_probe_validates_aes_and_closes_process_handle(monkeypatch,plain_header,stored_suffix):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = b'1234567890abcdef'
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    probe = encryptor.update(plain_header+b'X'*(16-len(plain_header))) + encryptor.finalize()
    class Region(ctypes.Structure):
        _fields_ = [('BaseAddress',ctypes.c_size_t),('RegionSize',ctypes.c_size_t),
                    ('State',ctypes.c_uint32),('Protect',ctypes.c_uint32)]
    class Kernel:
        closed = []
        def OpenProcess(self, *args): return 123
        def CloseHandle(self, value): self.closed.append(value)
        def VirtualQueryEx(self, handle, address, output, size):
            if address.value: return 0
            output._obj.BaseAddress = 4096
            output._obj.RegionSize = 100
            output._obj.State = 0x1000
            output._obj.Protect = 4
            return size
        def ReadProcessMemory(self, handle, address, buffer, size, read):
            data = b'!'+key+stored_suffix+b'!'
            ctypes.memmove(buffer, data, len(data)); read._obj.value = len(data)
            return 1
    kernel = Kernel()
    monkeypatch.setitem(sys.modules, 'wechatauto', SimpleNamespace(db=SimpleNamespace(_k32=kernel, _MBI=Region)))
    decoder = SimpleNamespace(_probe_ct=lambda:probe, db=SimpleNamespace(_find_weixin_pids=lambda:[1]))
    assert scan_image_key(decoder) == key.decode()
    assert kernel.closed == [123]


def test_image_key_probe_deadline_stops_memory_reads(monkeypatch):
    kernel = SimpleNamespace(OpenProcess=lambda *args:123, CloseHandle=lambda value:closed.append(value),
        VirtualQueryEx=lambda *args:pytest.fail('deadline must prevent query'))
    closed = []
    monkeypatch.setitem(sys.modules, 'wechatauto', SimpleNamespace(db=SimpleNamespace(_k32=kernel)))
    decoder = SimpleNamespace(_probe_ct=lambda:b'X'*16, db=SimpleNamespace(_find_weixin_pids=lambda:[1]))
    assert scan_image_key(decoder, seconds=0) is None
    assert closed == [123]


def test_image_config_requires_same_account_and_exact_image_key_validation(monkeypatch):
    records={1:('unused','not-config','foreign'),2:('unused',123,'own')}
    monkeypatch.setitem(sys.modules,'wechatauto',SimpleNamespace(db=SimpleNamespace(extract_master_key_from_cfg=lambda pid:records[pid])))
    decoder=SimpleNamespace(db=SimpleNamespace(wxid='own',_find_weixin_pids=lambda:[1,2]),
        derive_image_keys=lambda config,owner:('verified-key',config&255),_validate_key=lambda key:key=='verified-key')
    assert read_image_config(decoder)==(123,'verified-key',123)
    decoder._validate_key=lambda key:False
    assert read_image_config(decoder) is None


def test_short_own_message_cannot_be_replied_to_as_counterpart():
    context = {'recent':[{'role':'other','text':'以前的话'}, {'role':'self','text':'game'}]}
    with pytest.raises(ReplyEngineError, match='自己发'):
        _check_self_continuation([{'text':'game？你是在召唤我还是手滑'}], context)
    _check_self_continuation([{'text':'今晚想打两把，你有空一起吗？'}], context)
