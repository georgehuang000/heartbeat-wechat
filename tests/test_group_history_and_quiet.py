from contact_fixtures import apply_enabled_snapshot
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS

import pytest

from galchat.contact_database import ContactDatabase
from galchat.controller import Controller
from galchat.conversation_identity import group_participant
from galchat.engine import ReplyEngine, PreparedPrompt
from galchat.memory import MemoryStore
from galchat.reply_batches import BurstSchedule, incoming_append_only
from galchat.reply_review import parse_review
from galchat.settings import Settings
from galchat.wechat import WeChatError

ROOT = Path(__file__).resolve().parents[1]


def row(i, role='other', **extra):
    return {'id': str(i), 'role': role, 'kind': 'text', 'text': f'消息{i}',
            'timestamp': '2026-10-14T12:00:00', 'content_known': True, **extra}


def test_trailing_pause_is_three_seconds_even_after_continuous_four_second_burst():
    timer = BurstSchedule()
    assert [timer.update(t) for t in (0, .8, 1.6, 2.4, 3.2, 4)] == pytest.approx([3, 3.8, 4.6, 5.4, 6.2, 7])
    timer.clear()
    assert timer.update(100) == 103


def test_context_is_prepared_before_quiet_deadline_without_model_call(tmp_path, monkeypatch):
    clock = [100.]
    monkeypatch.setattr('galchat.controller.time.monotonic', lambda: clock[0])
    app = Controller(ROOT, tmp_path, start_worker=False)
    app.state['settings']['api_ready'] = True
    app.state['connection'] = {'can_read': True}
    app.settings.model_config = lambda: {'base_url': 'https://fixture.test', 'model': 'fixture', 'api_key': 'fixture'}
    calls, done = [], Event()
    def generate(context, config, **kwargs):
        calls.append((context, config))
        done.set()
        return {'choices': [], 'conversation_state': 'wait', 'source': 'fixture', 'model': 'fixture', 'elapsed_ms': 1}
    app.engine = NS(generate=generate)
    try:
        snap = lambda rows: {'contact': {'id': 'A', 'name': '虚构'}, 'messages': rows}
        apply_enabled_snapshot(app, snap([row(1)]))
        assert isinstance(app._prepared_context[2], PreparedPrompt)
        assert app.state['history']['model_context']['included_messages'] == 1
        app._maybe_generate()
        assert calls == []
        clock[0] = 102.
        apply_enabled_snapshot(app, snap([row(1), row(2)]))
        assert app.pending_at == 105
        clock[0] = 104.99
        app._maybe_generate()
        assert calls == []
        clock[0] = 105.
        app._maybe_generate()
        assert done.wait(2) and len(calls) == 1
        assert [m['id'] for m in calls[0][0]['recent']] == ['1', '2']
        assert isinstance(calls[0][1]['_prepared_prompt'], PreparedPrompt)
    finally:
        app.close()


def test_settings_and_memory_support_5000_without_changing_default(tmp_path):
    settings = Settings(tmp_path)
    assert settings.values['history_limit'] == 100
    settings.save({'history_limit': 5000})
    assert Settings(tmp_path).values['history_limit'] == 5000
    for count in (10, 11, 137, 1680, 3200, 4999):
        settings.save({'history_limit': count})
        assert Settings(tmp_path).values['history_limit'] == count
    for value in (9, 5001, 10000, True, 5000.0, 137.5, '137'):
        with pytest.raises(ValueError):
            settings.save({'history_limit': value})
    member = group_participant('fixture-self', 'fixture@chatroom', 'fixture-alice')
    with MemoryStore(':memory:') as memory:
        memory.upsert_messages('room', '虚构群', [row(i, participant_id=member,
            sender_display_name='小林', conversation_kind='group') for i in range(5000)])
        context = memory.get_context('room', limit=5000)
        assert len(context['recent']) == 5000
        assert context['recent'][0]['participant_id'] == member
        assert context['recent'][-1]['sender_display_name'] == '小林'


def test_disabling_quiet_wait_reschedules_pending_but_retains_displayed_batch(tmp_path, monkeypatch):
    clock = [100.]
    monkeypatch.setattr('galchat.controller.time.monotonic', lambda: clock[0])
    app = Controller(ROOT, tmp_path, start_worker=False)
    try:
        snapshot = lambda rows: {'contact': {'id': 'A', 'name': '虚构'}, 'messages': rows}
        apply_enabled_snapshot(app, snapshot([row(1)]))
        assert app.pending_at == 103
        choices = [{'id': 'choice-1', 'text': '原有选项'}]
        app.state['choices'] = app.reply_batches.publish('A', app.revision, app.state['messages'], choices)
        batch, revision = app.reply_batches.current, app.revision
        clock[0] = 101
        app.dispatch('save_settings', {'strict_quiet_wait': False})
        assert app.pending_at == 100
        assert app.reply_batches.current == batch and app.revision == revision
        assert app.state['choices'][0]['text'] == '原有选项'
        apply_enabled_snapshot(app, snapshot([row(1), row(2)]))
        assert app.pending_at == 101
        clock[0] = 102
        app.dispatch('save_settings', {'strict_quiet_wait': True})
        assert app.pending_at == 104  # Anchored to the last arrival, not the settings click.
        app.pending_at = None
        app.dispatch('save_settings', {'strict_quiet_wait': False})
        assert app.pending_at is None  # No new messages, no gratuitous model request.
        assert Settings(tmp_path).values['strict_quiet_wait'] is False
    finally:
        app.close()


def test_immediate_mode_still_merges_arrivals_into_one_pending_generation(tmp_path, monkeypatch):
    app = Controller(ROOT, tmp_path, start_worker=False)
    app.settings.save({'strict_quiet_wait': False})
    entered, release, done = Event(), Event(), Event()
    calls = []
    app.settings.model_config = lambda: {'base_url': 'https://fixture.test', 'model': 'fixture', 'api_key': 'fixture'}
    app.state['settings'].update(api_ready=True, strict_quiet_wait=False)
    def generate(context, config, **kwargs):
        calls.append([m['id'] for m in context['recent']])
        entered.set()
        assert release.wait(3)
        done.set()
        return {'choices': [], 'conversation_state': 'wait', 'source': 'fixture', 'model': 'fixture', 'elapsed_ms': 1}
    app.engine = NS(generate=generate)
    try:
        snapshot = lambda rows: {'contact': {'id': 'A', 'name': '虚构'}, 'messages': rows}
        apply_enabled_snapshot(app, snapshot([row(1)]))
        app._maybe_generate()
        assert entered.wait(2)
        for end in range(2, 6):
            apply_enabled_snapshot(app, snapshot([row(i) for i in range(1, end+1)]))
            app._maybe_generate()
        assert len(calls) == 1 and app.pending_at is not None
        assert app._prepared_context[1]['recent'][-1]['id'] == '5'
        release.set()
        assert done.wait(2)
    finally:
        release.set()
        app.close()


def group_db(tmp_path):
    raw = [dict(local_id=i, sort_seq=i, local_type=1, content=text, create_time=1791950400 + i,
                _galchat_shard='fixture-message.db', sender_username=sender)
           for i, (sender, text) in enumerate([
               ('private-self-id', '我周日回来'), ('private-alice-id', '我家猫睡着了'),
               ('private-bob-id', '我妹妹毕业了'), ('', '身份没提供')], 1)]
    reader = NS(wxid='private-self-id', search_contact=lambda name: [{
        'username': 'private-room@chatroom', 'nick_name': '虚构群', 'remark': ''}],
        get_messages=lambda user, limit, offset: deepcopy(raw[::-1]))
    db = ContactDatabase(ROOT, tmp_path, reader=reader)
    db._speaker_names = lambda senders: {'private-alice-id': '小林', 'private-bob-id': '小林'}
    return db, db.resolve('虚构群')


def test_group_members_do_not_merge_on_shared_names_and_unknown_stays_unknown(tmp_path):
    db, contact = group_db(tmp_path)
    messages = db.recent(contact, 5000)
    assert contact['conversation_kind'] == 'group' and contact['gender'] == 0
    assert [m['role'] for m in messages] == ['self', 'other', 'other', 'unknown']
    assert messages[1]['sender_display_name'] == messages[2]['sender_display_name']
    assert messages[1]['participant_id'] != messages[2]['participant_id']
    context = {'contact_id': contact['id'], 'conversation_kind': 'group', 'recent': messages,
               'reply_preferences': {'adult_context': True}}
    payload = ReplyEngine._payload_context(context)
    assert payload['generation_perspective']['recipient_id'] == 'group_chat'
    assert payload['generation_perspective']['respond_to_speaker_id'] == messages[2]['participant_id']
    assert payload['recent'][1]['speaker_id'] != payload['recent'][2]['speaker_id']
    assert 'chat_peer' not in payload['participants']
    assert payload['pursuit']['enabled'] is False
    encoded = json.dumps(payload)
    assert not any(token in encoded for token in ('private-self-id', 'private-alice-id', 'private-bob-id', 'private-room@chatroom'))
    changed = deepcopy(messages[:3]); changed[0]['participant_id'] = 'changed'
    assert not incoming_append_only(messages[:3], changed + [row(9)])


def test_unknown_group_speaker_cannot_be_silently_named_chat_peer():
    with pytest.raises(Exception, match='群成员身份'):
        ReplyEngine._payload_context({'conversation_kind': 'group', 'recent': [row(1)]})


def test_group_media_ownership_is_part_of_the_image_version():
    first = row(1, kind='image', participant_id=group_participant('self', 'room@chatroom', 'alice'),
        conversation_kind='group')
    second = {**first, 'participant_id': group_participant('self', 'room@chatroom', 'bob')}
    assert Controller._visual_version(first) != Controller._visual_version(second)


def test_group_review_binds_evidence_to_specific_member(tmp_path):
    db, contact = group_db(tmp_path)
    payload = ReplyEngine._payload_context({'contact_id': contact['id'], 'conversation_kind': 'group',
        'recent': db.recent(contact, 100)[:3]})
    source = payload['recent'][1]
    choice = {'id': 'choice-1', 'text': '你的猫睡着了啊'}
    proof = {'message_id': source['id'], 'speaker_id': source['speaker_id'], 'quote': '我家猫睡着了'}
    review = {'conversation_state': 'close', 'checks': [{'choice_id': 'choice-1', 'decision': 'pass',
        'reason': '原文猫属于这一位成员', 'evidence': [proof], 'claims': [{**proof,
            'text': '你的猫睡着了', 'owner_id': source['speaker_id']}]}]}
    assert parse_review(json.dumps(review), [choice], payload)[1] == []
    review['checks'][0]['evidence'][0]['speaker_id'] = payload['recent'][2]['speaker_id']
    with pytest.raises(ValueError, match='发言人证据'):
        parse_review(json.dumps(review), [choice], payload)


def test_large_history_is_explicitly_budgeted_and_never_silently_capped_to_200():
    messages = [row(i, text='收到' * 12) for i in range(5000)]
    payload = ReplyEngine._payload_context({'contact_id': 'A', 'recent': messages})
    assert 200 < len(payload['recent']) < 5000
    assert payload['recent'][-1]['id'] == '4999'
    assert payload['prompt_coverage']['available_messages'] == 5000
    assert payload['prompt_coverage']['omitted_messages'] == 5000 - len(payload['recent'])
    assert len(payload['timeline']['messages']) >= len(payload['recent'])
    assert len(json.dumps(payload, ensure_ascii=False)) < 500000


def test_prepared_prompt_rechecks_context_after_mutation(monkeypatch):
    context = {'contact_id': 'A', 'recent': [row(1, text='今天晴天')], 'contact_name': '虚构'}
    prepared = ReplyEngine.prepare_context(context)
    context['recent'][0]['text'] = '今天下雨'
    bodies = []
    choices = [{'id': f'choice-{i}', 'text': text, 'label': f'方向{i}', 'hint': '只回应天气', 'tone': 'calm'}
        for i, text in enumerate(['雨大不大？', '出门记得带伞', '天气又变啦'], 1)]
    engine = ReplyEngine()
    def request(method, url, headers, timeout, body, *, activity_check=None):
        bodies.append(body)
        return {'choices': [{'message': {'content': json.dumps({'choices': choices})}}]}
    monkeypatch.setattr(engine, '_request', request)
    engine.generate(context, {'base_url': 'https://fixture.test', 'model': 'fixture', 'api_key': 'fixture',
        '_prepared_prompt': prepared})
    sent = json.loads(bodies[0]['messages'][1]['content'])
    assert sent['context']['recent'][0]['text'] == '今天下雨'
    assert '_prepared_prompt' not in json.dumps(bodies)
