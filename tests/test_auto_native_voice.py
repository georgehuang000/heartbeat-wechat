from contact_fixtures import apply_enabled_snapshot
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from galchat.controller import Controller
from galchat.wechat import WeChatError
from galchat.settings import Settings

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    a=Controller(ROOT,tmp_path,start_worker=False)
    voice={'id':'v','role':'other','kind':'voice','text':'[语音]','content_known':False,
        'timestamp':'2026-10-14T12:00:00','duration_seconds':4}
    snapshot={'contact':{'id':'A','name':'甲'},'messages':[voice]}
    apply_enabled_snapshot(a, snapshot);a.connected=True
    pattern=NS(Value='',IsReadOnly=False);calls=[];foreground=[100]
    backend=NS(gui=NS(GetForegroundWindow=lambda:foreground[0]),focus=lambda w:pytest.fail('must not focus'))
    def begin(cid,mid,**kwargs):
        calls.append((cid,mid,kwargs));return {'status':'processing','method':'uia_menu_click'}
    a.current_adapter=NS(current=lambda **kw:{'name':'甲','window':{'hwnd':100},'edit':NS(GetValuePattern=lambda:pattern)},
        visual=NS(backend=backend),begin_voice_transcription=begin)
    a.database=NS(resolve=lambda name:{'id':'A'})
    try:yield a,snapshot,pattern,calls,foreground
    finally:a.close()


@pytest.mark.parametrize('role',['self','other'])
def test_native_auto_reads_latest_voice_without_asr_key_and_keeps_identity(app,role):
    a,s,p,calls,_=app;s['messages'][0]['role']=role;apply_enabled_snapshot(a, s)
    assert not a.settings.public()['asr_ready']
    a._maybe_prepare_voice()
    assert calls[0][0:2]==('A','v') and calls[0][2]['automatic'] is True
    assert calls[0][2]['expected_message']['role']==role
    a._maybe_prepare_voice();assert len(calls)==1 and a._voice_job is not None
    s['messages'][0].update(text='这是原生语音正文',content_known=True,content_source='wechat_transcription')
    apply_enabled_snapshot(a, s)
    assert a._voice_job is None and a.state['attachment_status']['status']=='ready'


@pytest.mark.parametrize('condition',['draft','foreground','paused','off','disabled'])
def test_automatic_voice_defers_without_touching_draft_or_consuming_attempt(app,condition):
    a,_,pattern,calls,foreground=app
    if condition=='draft':pattern.Value='手写内容'
    if condition=='foreground':foreground[0]=7
    if condition=='paused':a.state['paused']=True
    if condition=='off':a.settings.save({'media_policy':'off'})
    if condition=='disabled':a.settings.save({'auto_voice':False})
    a._maybe_prepare_voice()
    assert not calls and not a._voice_attempts and a._voice_job is None
    assert pattern.Value==('手写内容' if condition=='draft' else '')


def test_latest_known_voice_does_not_start_converting_older_unknown_history(app):
    a,s,_,calls,_=app
    s['messages'].append({**s['messages'][0],'id':'new','content_known':True,'content_source':'wechat_transcription','text':'已有转写'})
    apply_enabled_snapshot(a, s);a._maybe_prepare_voice()
    assert not calls


def test_failed_native_menu_is_not_reopened_in_a_background_loop(app):
    a,_,_,calls,_=app
    def fail(*args,**kw):calls.append('failed');raise WeChatError('voice_not_bound','未能绑定')
    a.current_adapter.begin_voice_transcription=fail
    a._maybe_prepare_voice();a._voice_defer_until=0;a._maybe_prepare_voice()
    assert calls==['failed'] and a._voice_job is None
    assert a.state['attachment_status']['status']=='error'


def test_disabling_auto_voice_cancels_wait_and_persists_switch(app,tmp_path):
    a,_,_,calls,_=app;a._maybe_prepare_voice()
    assert a.dispatch('save_settings',{'auto_voice':False})['ok']
    assert a._voice_job is None and Settings(tmp_path).public()['auto_voice'] is False
