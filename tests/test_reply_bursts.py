from contact_fixtures import apply_enabled_snapshot
from copy import deepcopy
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace
import pytest
from galchat.controller import Controller
from galchat.reply_batches import BurstSchedule, incoming_append_only
from galchat.current_chat import CurrentChatAdapter
from galchat.wechat import WeChatError

ROOT = Path(__file__).resolve().parents[1]


def row(i, role='other', text=None):
    return {'id': str(i), 'role': role, 'kind': 'text', 'text': text or f'第{i}句',
            'timestamp': f'2026-10-13T15:00:{i:02}', 'content_known': True}


def snapshot(rows, person='A'):
    return {'contact': {'id': person, 'name': person}, 'messages': deepcopy(rows)}


def choices(prefix):
    return [{'id': f'choice-{i}', 'text': f'{prefix}{i}', 'label': f'方向{i}', 'hint': '说明', 'tone': 'calm'}
            for i in range(1, 4)]


@pytest.fixture
def app(tmp_path):
    result = Controller(ROOT, tmp_path, start_worker=False)
    result.settings.save({'api_key': 'fixture', 'auto_generate': True})
    result.state['settings'] = result.settings.public()
    result.state['connection'] = {'can_read': True, 'can_fill': True}
    result.connected = True
    apply_enabled_snapshot(result, snapshot([row(1)]))
    writes = []
    def fill(person, text, **kwargs):
        writes.append((person, text, kwargs))
        return {'ok': True, 'message': 'filled'}
    result.adapter = result.current_adapter = SimpleNamespace(fill_draft=fill)
    result.test_writes = writes
    yield result
    result.close()


def publish(app, prefix='已看见'):
    app.state['choices'] = app.reply_batches.publish('A', app.revision, app.state['messages'], choices(prefix))
    app.state['reply_batch'] = app.reply_batches.describe(app.reply_batches.current, app.state['messages'])
    app.choice_revision = app.revision
    return {'id': 'choice-1', 'batch_id': app.reply_batches.current, 'contact_id': 'A', 'revision': app.revision}


def test_incoming_append_retains_visible_choices_and_selection_identity(app):
    click = publish(app)
    old_text = app.state['choices'][0]['text']
    for i in range(2, 9):
        apply_enabled_snapshot(app, snapshot([row(j) for j in range(1, i+1)]))
    assert app.state['choices'][0]['text'] == old_text
    assert app.state['reply_batch']['new_message_count'] == 7
    assert app.revision > click['revision']
    assert app._handle_native('choose_fill', click)['ok']
    assert app.test_writes == [('A', old_text, {'allow_incoming_updates': True})]


def test_clicked_older_batch_never_changes_to_new_sentence_with_same_id(app):
    old_click = publish(app, '原文')
    publish(app, '刷新后的不同句子')
    app._handle_native('choose_fill', old_click)
    assert app.test_writes[0][1] == '原文1'


@pytest.mark.parametrize('change', ['own_reply', 'edit', 'contact', 'preferences', 'missing_message'])
def test_material_change_revokes_every_old_batch(app, change):
    click = publish(app)
    if change == 'own_reply':
        apply_enabled_snapshot(app, snapshot([row(1), row(2, 'self')]))
    elif change == 'edit':
        apply_enabled_snapshot(app, snapshot([row(1, text='被修改的正文')]))
    elif change == 'missing_message':
        apply_enabled_snapshot(app, snapshot([row(2)]))
    elif change == 'contact':
        apply_enabled_snapshot(app, snapshot([row(1)], 'B'))
    else:
        assert app.dispatch('save_settings', {'emoji_mode': 'lively'})['ok']
    with pytest.raises(ValueError):
        app._handle_native('choose_fill', click)
    assert app.test_writes == []


def test_verified_draft_can_be_replaced_during_incoming_burst(app):
    click = publish(app, '原文')
    app._handle_native('choose_fill', click)
    apply_enabled_snapshot(app, snapshot([row(1), row(2)]))
    app._handle_native('choose_fill', {**click, 'id': 'choice-2'})
    assert app.test_writes[1][2] == {'replace_text': '原文1', 'allow_incoming_updates': True}


def test_obsolete_generation_is_not_published_during_new_quiet_period(app):
    started, release, finished = Event(), Event(), Event()
    seen = []
    def generate(context, *args, **kwargs):
        seen.append(deepcopy(context))
        started.set()
        assert release.wait(2)
        finished.set()
        return {'choices': choices('模型原文'), 'source': 'fixture', 'model': 'fixture', 'elapsed_ms': 10}
    app.engine = SimpleNamespace(generate=generate)
    app.pending_at = 0
    app._maybe_generate()
    assert started.wait(2)
    apply_enabled_snapshot(app, snapshot([row(1), row(2), row(3)]))
    release.set()
    assert finished.wait(2)
    deadline = time.monotonic()+2
    while app.generating and time.monotonic() < deadline:
        time.sleep(.01)
    assert not app.generating and len(seen) == 1
    assert len(seen[0]['recent']) == 1
    assert app.state['choices'] == []
    assert app.state['generation']['status'] == 'collecting'
    assert app.pending_at is not None  # Latest context remains queued, not discarded.


def test_generation_is_discarded_after_user_has_replied(app):
    started, release = Event(), Event()
    def generate(context, *args, **kwargs):
        started.set()
        assert release.wait(2)
        return {'choices': choices('旧轮'), 'source': 'fixture', 'model': 'fixture', 'elapsed_ms': 10}
    app.engine = SimpleNamespace(generate=generate)
    app.pending_at = 0
    app._maybe_generate()
    assert started.wait(2)
    apply_enabled_snapshot(app, snapshot([row(1), row(2, 'self')]))
    release.set()
    deadline = time.monotonic()+2
    while app.generating and time.monotonic() < deadline:
        time.sleep(.01)
    assert app.state['choices'] == []


def test_continuous_messages_have_bounded_generation_wait():
    scheduler = BurstSchedule(quiet=1.2, max_wait=4)
    deadlines = [scheduler.update(t) for t in (0, .8, 1.6, 2.4, 3.2, 4)]
    assert deadlines == pytest.approx([1.2, 2, 2.8, 3.6, 4, 4])
    scheduler.clear()
    assert scheduler.update(10) == pytest.approx(11.2)


def test_bounded_history_only_accepts_verified_overlap_and_incoming():
    old = [row(i) for i in range(1, 5)]
    assert incoming_append_only(old, [row(i) for i in range(2, 6)])
    assert not incoming_append_only(old, [row(i) for i in range(5, 9)])
    assert not incoming_append_only(old, [row(2, text='edited'), row(3), row(4), row(5)])
    assert not incoming_append_only(old, old+[row(5, 'system')])


def test_native_fill_accepts_append_only_but_preserves_manual_draft():
    class Pattern:
        Value = ''
        IsReadOnly = False
        writes = []
        def SetValue(self, value):
            self.writes.append(value)
            self.Value = value
    pattern = Pattern()
    edit = SimpleNamespace(GetValuePattern=lambda: pattern)
    db = SimpleNamespace(resolve=lambda name: {'id': name}, recent=lambda *args: [row(1), row(2)])
    visual = SimpleNamespace(backend=SimpleNamespace(focus=lambda w: None))
    adapter = CurrentChatAdapter(db, visual, lambda: {'history_limit': 100})
    adapter._last = {'contact': {'id': 'A'}, 'messages': [row(1)]}
    adapter.current = lambda **kwargs: {'name': 'A', 'mode': 'uia', 'window': {}, 'edit': edit}
    assert adapter.fill_draft('A', '选中原文', allow_incoming_updates=True)['ok']
    assert pattern.writes == ['选中原文']
    pattern.Value += '手改'
    assert adapter.fill_draft('A', '另一个选项', replace_text='选中原文', allow_incoming_updates=True)['code'] == 'draft_edited'
    assert pattern.writes == ['选中原文']
    db.recent = lambda *args: [row(1), row(2, 'self')]
    assert adapter.fill_draft('A', '旧回复', allow_incoming_updates=True)['code'] == 'messages_changed'


def test_state_sequences_advance_even_when_revision_is_unchanged(app):
    one, two = app.get_state(), app.get_state()
    assert one['revision'] == two['revision'] and one['state_sequence'] < two['state_sequence']




def test_native_connect_can_read_without_stealing_focus(app):
    current = SimpleNamespace(current=lambda **kwargs: {'mode':'uia'},
        snapshot=lambda: snapshot([row(1)]), status=lambda: {'status':'connected','can_read':True,'can_fill':True})
    app.current_adapter = current
    app._handle_native('connect', {})
    assert app.connected and app.state['contact']['id']=='A'


@pytest.mark.parametrize('legacy_hold', [15, 30, 60])
def test_three_second_quiet_overrides_old_hold_and_keeps_choices_until_ready(app, monkeypatch, legacy_hold):
    clock=[100.]
    monkeypatch.setattr('galchat.controller.time.monotonic',lambda:clock[0])
    app.settings.values['reply_hold_seconds']=legacy_hold
    publish(app)
    old=deepcopy(app.state['choices'])
    calls=[];started=Event();release=Event()
    def generate(context,config,**kwargs):
        calls.append(context);started.set();release.wait(2)
        return {'choices':choices('最新'), 'source':'fixture','model':'fixture','elapsed_ms':1}
    app.engine=SimpleNamespace(generate=generate)
    try:
        for stamp,i in [(101.,2),(102.,3),(103.,4)]:
            clock[0]=stamp
            apply_enabled_snapshot(app,snapshot([row(j) for j in range(1,i+1)]))
            assert app.pending_at==stamp+3
            app._maybe_generate()
            assert calls==[] and app.state['choices']==old
        # Old clients may still save the retired field. It has no scheduling effect.
        assert app.dispatch('save_settings',{'reply_hold_seconds':60})['ok']
        assert app.pending_at==106.
        clock[0]=105.99;app._maybe_generate();assert calls==[]
        clock[0]=106.;app._maybe_generate()
        assert started.wait(2) and len(calls)==1
        assert len(calls[0]['recent'])==4 and app.state['choices']==old
        assert app.state['generation']['status']=='generating'
        release.set();deadline=time.time()+2
        while app.generating and time.time()<deadline:time.sleep(.01)
        assert app.state['choices'][0]['text']=='最新1'
        assert app.state['generation']['status']=='ready'
        # No incoming message means no timer-driven repeat, even much later.
        clock[0]=200.;app._maybe_generate();assert len(calls)==1
    finally:
        release.set()


def test_manual_update_bypasses_quiet_and_balance_failure_stops_new_message_calls(app):
    from galchat.engine import ReplyEngineError
    publish(app)
    calls=[]
    def unavailable(*args,**kwargs):
        calls.append(1)
        raise ReplyEngineError('余额不足',code='insufficient_balance')
    app.engine=SimpleNamespace(generate=unavailable)
    apply_enabled_snapshot(app, snapshot([row(1),row(2)]))
    assert app.dispatch('generate')['ok']
    deadline=time.monotonic()+2
    while app.generating and time.monotonic()<deadline:time.sleep(.01)
    assert len(calls)==1 and len(app.state['choices'])==3
    assert app.state['generation']['automatic_blocked']
    for i in range(3,8):
        apply_enabled_snapshot(app, snapshot([row(j) for j in range(1,i+1)]))
        app._maybe_generate()
    assert len(calls)==1 and app.pending_at is None
    assert app.state['generation']['error_code']=='insufficient_balance'
    assert app.dispatch('generate')['ok']
    deadline=time.monotonic()+2
    while app.generating and time.monotonic()<deadline:time.sleep(.01)
    assert len(calls)==2  # Recovery retries only after the user's explicit action.


def test_disabling_auto_generation_preserves_visible_choices_and_draft(app):
    publish(app)
    app.state['draft']='用户保留的草稿'
    assert app.dispatch('save_settings',{'auto_generate':False})['ok']
    assert len(app.state['choices'])==3 and app.state['draft']=='用户保留的草稿'
    assert app.pending_at is None
