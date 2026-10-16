"""Controller lifecycle checks; fixtures never access or operate WeChat."""
from contact_fixtures import apply_enabled_snapshot
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from galchat.controller import Controller
from galchat.engine import ReplyEngine
from galchat.wechat import WeChatError

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    controller=Controller(ROOT,tmp_path,start_worker=False)
    voice={'id':'voice','kind':'voice','role':'other','text':'[语音]',
        'timestamp':'2026-10-14T10:20:00','duration_seconds':4.1,'content_known':False}
    snapshot={'contact':{'id':'account:A','name':'甲'},'messages':[voice]}
    apply_enabled_snapshot(controller, snapshot)
    calls=[]
    def begin(cid,mid,*,expected_message):
        calls.append((cid,mid,deepcopy(expected_message)))
        return {'status':'processing','method':'uia_invoke','message':deepcopy(voice)}
    controller.current_adapter=NS(begin_voice_transcription=begin,snapshot=lambda:deepcopy(snapshot))
    controller.cloud_media=NS(transcribe=lambda *args:pytest.fail('native conversion must not call ASR'))
    controller.local_media=NS(voice=lambda *args:pytest.fail('native conversion must not read audio'))
    try:yield controller,snapshot,calls
    finally:controller.close()


def payload(controller):return {'contact_id':'account:A','revision':controller.revision,'id':'voice'}


@pytest.mark.parametrize('role',['self','other'])
def test_conversion_returns_before_service_result_and_keeps_exact_sender_time(app,role):
    controller,snapshot,calls=app
    snapshot['messages'][0]['role']=role
    apply_enabled_snapshot(controller, snapshot)
    controller.state['draft']='已有草稿'
    assert not controller.settings.public()['asr_ready']
    assert controller._handle_native('transcribe_voice',payload(controller))['message']=='正在读这条语音…'
    assert len(calls)==1 and controller._voice_job is not None
    apply_enabled_snapshot(controller, deepcopy(snapshot))
    assert controller.state['attachment_status']['status']=='processing'
    assert controller.state['draft']=='已有草稿'
    completed=deepcopy(snapshot)
    completed['messages'][0].update(text='周末去散步吧',content_known=True,content_source='wechat_transcription')
    apply_enabled_snapshot(controller, completed)
    assert controller._voice_job is None and controller.state['attachment_status']['status']=='ready'
    prepared=ReplyEngine._payload_context({'contact_id':'account:A','recent':controller.state['messages']})['recent'][0]
    assert all(prepared[k]==completed['messages'][0][k] for k in ('id','role','timestamp','text','content_source'))
    assert controller.state['draft']=='已有草稿'


def test_new_peer_messages_do_not_cancel_exact_voice_wait(app):
    controller,snapshot,_=app
    controller._handle_native('transcribe_voice',payload(controller))
    snapshot['messages'].append({'id':'new','role':'other','kind':'text','text':'再晚点也行','timestamp':'2026-10-14T10:21:00'})
    apply_enabled_snapshot(controller, snapshot)
    assert controller._voice_job is not None
    snapshot['messages'][0].update(text='周末去吧',content_known=True,content_source='wechat_transcription')
    apply_enabled_snapshot(controller, snapshot)
    assert controller.state['attachment_status']['status']=='ready'


def test_contact_switch_discards_wait_and_late_old_result(app):
    controller,snapshot,_=app
    controller._handle_native('transcribe_voice',payload(controller))
    other={'contact':{'id':'account:B','name':'乙'},'messages':[{'id':'b','role':'other','text':'你好'}]}
    apply_enabled_snapshot(controller, other)
    assert controller._voice_job is None and controller.state['attachment_status'] is None
    assert controller.state['messages'][0]['id']=='b'


@pytest.mark.parametrize('change',[{'role':'self'},{'timestamp':'2026-10-14T10:20:01'},{'duration_seconds':5.1},
    {'participant_id': 'group_member_' + 'b' * 24}, {'conversation_kind': 'group'}])
def test_changed_voice_identity_never_completes_original_wait(app,change):
    controller,snapshot,_=app
    controller._handle_native('transcribe_voice',payload(controller))
    snapshot['messages'][0].update(change,text='另一个结果',content_known=True,content_source='wechat_transcription')
    apply_enabled_snapshot(controller, snapshot)
    assert controller._voice_job is None and controller.state['attachment_status']['status']=='error'


def test_timeout_on_unchanged_snapshot_retains_manual_draft_and_no_asr_fallback(app):
    controller,snapshot,_=app
    controller.state['draft']='手写草稿'
    controller._handle_native('transcribe_voice',payload(controller))
    controller._voice_job['deadline']=0
    apply_enabled_snapshot(controller, snapshot)
    assert controller._voice_job is None
    assert controller.state['attachment_status']['status']=='error'
    assert controller.state['draft']=='手写草稿'
    assert ('account:A','voice') in controller._media_attempted


def test_stale_request_and_duplicate_requests_do_not_retrigger_controls(app):
    controller,_,calls=app
    with pytest.raises(ValueError):controller._handle_native('transcribe_voice',{**payload(controller),'revision':-1})
    assert not calls
    controller._handle_native('transcribe_voice',payload(controller))
    with pytest.raises(ValueError):controller._handle_native('transcribe_voice',payload(controller))
    assert len(calls)==1


def test_pausing_and_identity_loss_clear_wait_without_ui_actions(app):
    controller,snapshot,calls=app
    controller._handle_native('transcribe_voice',payload(controller))
    controller.dispatch('set_paused',{'paused':True})
    assert controller._voice_job is None and controller.state['attachment_status'] is None
    with pytest.raises(ValueError):controller._handle_native('transcribe_voice',payload(controller))
    assert len(calls)==1
    controller.dispatch('set_paused',{'paused':False})
    controller._handle_native('transcribe_voice',payload(controller))
    controller._connection_error('当前聊天不可读')
    assert controller._voice_job is None and controller.state['attachment_status'] is None


def test_existing_wechat_text_refreshes_without_wait_or_second_asr(app):
    controller,snapshot,calls=app
    snapshot['messages'][0].update(text='周末见',content_known=True,content_source='wechat_transcription')
    controller.current_adapter.begin_voice_transcription=lambda *args,**kwargs:{'status':'ready','method':'stored_wechat_text'}
    result=controller._handle_native('transcribe_voice',payload(controller))
    assert '已读取微信转文字' in result['message']
    assert controller._voice_job is None and not calls
    assert controller.state['messages'][0]['text']=='周末见'


def test_native_failure_keeps_draft_and_does_not_fall_back_to_asr(app):
    controller,_,_=app
    controller.state['draft']='手写草稿'
    controller.current_adapter.begin_voice_transcription=lambda *args,**kwargs:(_ for _ in ()).throw(WeChatError('voice_not_bound','请显示语音及前后消息'))
    with pytest.raises(WeChatError):controller._handle_native('transcribe_voice',payload(controller))
    assert controller.state['draft']=='手写草稿' and controller._voice_job is None
    assert controller.state['attachment_status']['kind']=='voice'
    assert ('account:A','voice') in controller._media_attempted
