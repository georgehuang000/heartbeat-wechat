import base64
import hashlib
import io
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image
import pytest

from galchat.stickers import sticker_reference, local_sticker, read_sticker


def picture():
    output=io.BytesIO()
    Image.new('RGB',(8,8),'pink').save(output,format='PNG')
    return output.getvalue()


def test_xml_only_retains_valid_hash_and_in_memory_key():
    md5='a'*32
    ref=sticker_reference(f'<msg><emoji md5="{md5}" aeskey="{"b"*32}" cdnurl="http://private.invalid/" fromusername="private"/></msg>')
    assert ref=={'md5':md5,'aeskey':'b'*32,'urls':[]}
    assert sticker_reference('<msg><emoji md5="../../secret"/></msg>') is None
    assert sticker_reference('<!DOCTYPE msg [<!ENTITY x "injected">]><msg/>') is None
    assert sticker_reference('malformed') is None


@pytest.mark.parametrize('mode',['plain','cbc','ecb'])
def test_exact_local_sticker_and_encrypted_cache_are_displayable(tmp_path,mode):
    data=picture();key=b'a'*16;md5=hashlib.md5(data).hexdigest()
    payload=data
    if mode!='plain':
        padder=padding.PKCS7(128).padder();padded=padder.update(data)+padder.finalize()
        encryptor=Cipher(algorithms.AES(key),modes.CBC(bytes(16)) if mode=='cbc' else modes.ECB()).encryptor()
        payload=encryptor.update(padded)+encryptor.finalize()
    directory=tmp_path/'business/emoticon/Persist'/md5[:2];directory.mkdir(parents=True)
    (directory/md5).write_bytes(payload)
    result=local_sticker(tmp_path,{'md5':md5,'aeskey':key.hex()})
    assert result.startswith('data:image/png;base64,')
    assert base64.b64decode(result.split(',',1)[1])==data


def test_cached_animated_gif_keeps_original_frames(tmp_path):
    output=io.BytesIO()
    Image.new('RGB',(8,8),'red').save(output,format='GIF',save_all=True,
        append_images=[Image.new('RGB',(8,8),'blue')],duration=100,loop=0)
    data=output.getvalue();md5=hashlib.md5(data).hexdigest()
    path=tmp_path/'cache/2026-10/Emoticon'/md5[:2];path.mkdir(parents=True)
    (path/md5).write_bytes(data)
    result=local_sticker(tmp_path,{'md5':md5,'aeskey':''})
    assert base64.b64decode(result.split(',',1)[1])==data


def test_wrong_image_never_substitutes_for_requested_sticker(tmp_path):
    md5='a'*32;path=tmp_path/'business/emoticon/Persist'/md5[:2];path.mkdir(parents=True)
    (path/md5).write_bytes(picture())
    assert local_sticker(tmp_path,{'md5':md5,'aeskey':''}) is None
    assert local_sticker(tmp_path,{'md5':'../secret','aeskey':''}) is None


def test_missing_local_original_uses_official_cdn_and_verified_cache(tmp_path):
    import httpx
    data=picture();ref={'md5':hashlib.md5(data).hexdigest(),'aeskey':'',
        'urls':['http://wxapp.tc.qq.com/resource?id=fixture']}
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url.scheme=='https'
        return httpx.Response(200,content=data)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=read_sticker(tmp_path,ref,tmp_path/'cache-images',client)
        assert result.startswith('data:image/png')
        assert read_sticker(tmp_path,ref,tmp_path/'cache-images',client)==result
    assert len(calls)==1


@pytest.mark.parametrize('url',['http://127.0.0.1/secret','https://wxapp.tc.qq.com.evil.invalid/a','https://wxapp.tc.qq.com:8080/a','https://user@wxapp.tc.qq.com/a'])
def test_untrusted_media_addresses_are_not_requested(tmp_path,url):
    import httpx
    def handler(request):pytest.fail('Untrusted media address was requested')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert read_sticker(tmp_path,{'md5':'a'*32,'aeskey':'','urls':[url]},tmp_path/'cache-images',client) is None


def test_current_contact_and_revision_gate_media(controller=None):
    # Construct without native worker/API, testing only own-app media dispatch.
    from tempfile import TemporaryDirectory
    from galchat.controller import Controller
    with TemporaryDirectory() as directory:
        app=Controller(Path(__file__).resolve().parents[1],Path(directory),start_worker=False)
        app.state['contact']={'id':'A'}
        app.contact_preferences.save('A', {'assistance_enabled': True})
        app.state['messages']=[{'id':'sticker1','has_sticker':True}]
        calls=[]
        class Database:
            def sticker(self,*args):calls.append(args);return 'data:image/png;base64,fixture'
            def close(self):pass
        app.database=Database()
        try:
            assert not app.dispatch('get_sticker',{'contact_id':'B','revision':app.revision,'id':'sticker1'})['ok']
            assert not app.dispatch('get_sticker',{'contact_id':'A','revision':app.revision-1,'id':'sticker1'})['ok']
            assert not app.dispatch('get_sticker',{'contact_id':'A','revision':app.revision,'id':'unlisted'})['ok']
            assert calls==[]
            assert app.dispatch('get_sticker',{'contact_id':'A','revision':app.revision,'id':'sticker1'})['data_url'].startswith('data:')
        finally:
            app.close()
            app.memory.close()
