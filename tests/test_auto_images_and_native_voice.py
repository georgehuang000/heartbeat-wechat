from contact_fixtures import apply_enabled_snapshot
from copy import deepcopy
import io
from pathlib import Path
from types import SimpleNamespace
from queue import Empty
import time
import pytest
from PIL import Image
from galchat.controller import Controller
from galchat.native_voice import stored_voice_text

ROOT=Path(__file__).resolve().parents[1]


def proto_blob(field,value):
    def uint(x):
        r=bytearray()
        while x>127:r.append((x&127)|128);x >>= 7
        return bytes(r+bytes([x]))
    return uint(field<<3|2)+uint(len(value))+value


def test_existing_voice_transcript_is_read_only_for_reviewed_voice_shape():
    packed=proto_blob(5,b'\x08\x02'+proto_blob(2,'今天下班后聊'.encode()))
    assert stored_voice_text(34,packed)=='今天下班后聊'
    assert stored_voice_text(3,packed) is None
    assert stored_voice_text(34,packed+packed) is None
    assert stored_voice_text(34,proto_blob(5,b'\x08\x01'+proto_blob(2,b'pending'))) is None
    assert stored_voice_text(34,proto_blob(5,b'\x08\x02'+proto_blob(2,'转文字失败'.encode()))) is None
    assert stored_voice_text(34,b'broken') is None


@pytest.fixture
def image_app(tmp_path):
    app=Controller(ROOT,tmp_path,start_worker=False)
    app.state['settings']=app.settings.save({'api_key':'fixture','media_policy':'ask'})
    current={'contact':{'id':'A','name':'甲'},'messages':[
        {'id':'img','kind':'image','role':'self','text':'[图片]','content_known':False,'timestamp':'2026-10-13T15:00:00'}]}
    apply_enabled_snapshot(app, current)
    app.connected=True
    app.current_adapter=SimpleNamespace(current=lambda:{'name':'甲'})
    app.database=SimpleNamespace(resolve=lambda name:{'id':'A'})
    raw=io.BytesIO();Image.new('RGB',(10,10),'pink').save(raw,format='PNG')
    app.local_media=SimpleNamespace(image=lambda *args:{'bytes':raw.getvalue(),'tier':'original'})
    app.adapter=SimpleNamespace(snapshot=lambda:deepcopy(current))
    yield app,current
    app.close()


def finish_job(app):
    operation,job,_=app.commands.get(timeout=2)
    assert operation=='image_ready'
    app._handle_native(operation,job)


def test_own_image_prepares_under_ask_without_audio_api_or_caption_call(image_app):
    app,current=image_app
    app._maybe_prepare_image()
    assert app.state['generation']['status']=='reading_image'
    finish_job(app)
    assert app._visual_inputs[('A','img')]['data_url'].startswith('data:image/png;')
    assert app.state['generation_blocked_reason'] is None
    assert app.state['messages'][0]['role']=='self'
    assert app.state['messages'][0]['timestamp']==current['messages'][0]['timestamp']
    assert not app.state['messages'][0]['content_known']


def test_image_remains_relevant_when_followed_by_a_question(image_app):
    app,current=image_app
    current['messages'].append({'id':'q','kind':'text','role':'other','text':'你看这图怎么样','content_known':True,'timestamp':'2026-10-13T15:00:05'})
    apply_enabled_snapshot(app, current)
    assert app._automatic_image_candidate()['id']=='img'


@pytest.mark.parametrize('setting,value',[('media_policy','off'),('auto_images',False)])
def test_explicit_picture_controls_are_respected(image_app,setting,value):
    app,_=image_app
    app.settings.save({setting:value})
    app._maybe_prepare_image()
    assert app._image_job is None and not app._visual_inputs


def test_image_finishing_after_contact_change_is_discarded(image_app):
    app,current=image_app
    app._maybe_prepare_image()
    current['contact']={'id':'B','name':'乙'}
    apply_enabled_snapshot(app, current)
    finish_job(app)
    assert app._visual_inputs=={}


def test_image_finishing_after_model_change_is_discarded(image_app):
    app,_=image_app
    app._maybe_prepare_image()
    app.dispatch('save_settings',{'model':'another-model'})
    finish_job(app)
    assert app._visual_inputs=={}


def test_missing_file_retries_are_bounded_and_can_be_restarted(image_app):
    app,_=image_app
    def missing(*args):raise ValueError('missing local image')
    app.local_media.image=missing
    app._maybe_prepare_image();finish_job(app)
    assert app.state['attachment_status']['status']=='skipped'
    assert app.state['generation_blocked_reason'] is None
    assert app.state['messages'][0]['media_read_status']=='unavailable'
    assert app.state['messages'][0]['content_known'] is False
    app._maybe_prepare_image()
    assert app._image_job is None
    app._handle_native('retry_latest_image',{'contact_id':'A'})
    finish_job(app)
    assert app._image_attempts[('A','img')]['count']==1


def test_a_bad_picture_does_not_prevent_other_selected_pictures(image_app):
    app,current=image_app
    older=deepcopy(current['messages'][0]);older['id']='older'
    current['messages'].insert(0,older)
    apply_enabled_snapshot(app, current)
    original=app.local_media.image
    def read(owner,mid,**kwargs):
        if mid=='img':raise ValueError('broken image')
        return original(owner,mid)
    app.local_media.image=read
    app._maybe_prepare_image();finish_job(app)
    app._maybe_prepare_image();finish_job(app)
    assert ('A','older') in app._visual_inputs and ('A','img') not in app._visual_inputs
    context=app._build_reply_context()
    assert len(context['visual_inputs'])==1
    assert context['recent'][-1]['media_read_status']=='unavailable'
    assert app.state['generation_blocked_reason'] is None


def test_timed_out_image_does_not_publish_late_pixels(image_app):
    from threading import Event
    app,_=image_app
    gate=Event()
    original=app.local_media.image
    def slow(*args,**kwargs):
        assert gate.wait(2)
        return original(*args,**kwargs)
    app.local_media.image=slow
    app._maybe_prepare_image()
    app._image_job['deadline']=0
    app._maybe_prepare_image()
    assert app._image_job is None and app.state['generation_blocked_reason'] is None
    gate.set();finish_job(app)
    assert app._visual_inputs=={}
