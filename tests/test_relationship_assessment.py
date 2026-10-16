from contact_fixtures import apply_enabled_snapshot
import json
from pathlib import Path
import threading
import time

import httpx
import pytest

from galchat.controller import Controller
from galchat.engine import ReplyEngine
from galchat.relationship_assessment import validate_relationship_assessment


CHOICES = [{'label': f'方向{i}', 'text': f'先确认第{i}个问题吧', 'hint': '先澄清', 'tone': 'calm'} for i in range(3)]
CONFIG = {'base_url': 'https://example.test/v1', 'model': 'fixture'}


def message(mid, role, text, **extra):
    return {'id': mid, 'role': role, 'text': text, 'kind': 'text', 'content_known': True,
            'timestamp': f'2026-10-13 10:0{mid[-1] if mid[-1].isdigit() else 0}', **extra}


def payload(rows, adult=True):
    return {'recent': rows, 'reply_preferences': {'adult_context': adult}}


def candidate(stage, rows):
    return {'stage': stage, 'evidence': [{'message_id': m['id'], 'quote': m['text']} for m in rows]}


INVITE = message('m1', 'self', '周六一起去逛书店？')
ACCEPT = message('m2', 'other', '周六下午可以，在哪里碰面？')


def test_clear_acceptance_has_server_resolved_speakers_dates_and_full_quote():
    rows = [INVITE, ACCEPT]
    assessment = candidate('accepted', rows)
    assessment['evidence'][1].update(quote='可以', role='self', timestamp='invented')
    actual = validate_relationship_assessment(assessment, payload(rows))
    assert actual['status'] == 'suggested' and actual['stage'] == 'accepted'
    assert actual['evidence'][1] == {'message_id': 'm2', 'quote': ACCEPT['text'],
        'role': 'other', 'timestamp': ACCEPT['timestamp']}
    assert actual['scope']['read_count'] == actual['scope']['text_count'] == 2
    assert '好感' not in actual['reason']


def test_actual_invitation_is_a_milestone_not_counterpart_acceptance():
    actual = validate_relationship_assessment(candidate('invited', [INVITE]), payload([INVITE]))
    assert actual['status'] == 'suggested' and actual['stage'] == 'invited'


def test_scheduled_requires_both_confirmation_and_concrete_arrangement():
    arrangement = message('m1', 'self', '周六三点一起去书店门口碰面吧？')
    confirmation = message('m2', 'other', '好呀，就这么定。')
    rows = [arrangement, confirmation]
    actual = validate_relationship_assessment(candidate('scheduled', rows), payload(rows))
    assert actual['stage'] == 'scheduled' and actual['status'] == 'suggested'


@pytest.mark.parametrize('rows', [
    [INVITE, ACCEPT],  # No specific time/place.
    [INVITE, ACCEPT, message('m3', 'self', '周六三点在书店门口碰面吧')],  # Details not accepted.
    [message('m1', 'self', '周六三点一起去书店门口碰面吧？')],  # Proposal alone.
])
def test_proposed_or_incomplete_arrangement_does_not_become_scheduled(rows):
    assert validate_relationship_assessment(candidate('scheduled', rows), payload(rows))['status'] == 'insufficient'


@pytest.mark.parametrize('text', [
    '我不去', '不去', '不可以', '不行，别这样', '改天吧', '以后再说', '取消见面吧', '周六不想去',
])
def test_latest_refusal_or_deferral_supersedes_accepted_evidence(text):
    rows = [INVITE, ACCEPT, message('m3', 'other', text)]
    actual = validate_relationship_assessment(candidate('accepted', rows[:2]), payload(rows))
    assert actual['stage'] == 'unknown' and actual['status'] == 'insufficient'
    assert '旧邀约' in actual['reason']


@pytest.mark.parametrize('text', [
    '不可以', '不行，别这样', '可以帮我看简历吗', '可以吗？', '可能可以', '到时候再说',
    '她说好呀', '朋友答应可以', '我说：“好呀”只是举例',
])
def test_negative_ambiguous_question_or_reported_acceptance_is_not_proof(text):
    rows = [INVITE, message('m2', 'other', text)]
    assert validate_relationship_assessment(candidate('accepted', rows), payload(rows))['status'] == 'insufficient'


def test_unrelated_later_yes_does_not_answer_an_old_invitation():
    rows = [INVITE, message('m2', 'other', '先帮我看一下简历'),
            message('m3', 'self', '发我吧'), message('m4', 'other', '好呀')]
    actual = validate_relationship_assessment(candidate('accepted', [rows[0], rows[3]]), payload(rows))
    assert actual['status'] == 'insufficient'


def test_past_shared_activity_is_not_a_new_invitation():
    rows = [message('m1', 'other', '上次一起去书店挺开心的')]
    assert validate_relationship_assessment(candidate('invited', rows), payload(rows))['status'] == 'insufficient'


def test_same_speaker_acceptance_does_not_prove_mutual_acceptance():
    rows = [INVITE, message('m2', 'self', '她已经说可以了')]
    assert validate_relationship_assessment(candidate('accepted', rows), payload(rows))['status'] == 'insufficient'


@pytest.mark.parametrize('change', [
    {'kind': 'forward'}, {'kind': 'voice'}, {'kind': 'image'}, {'kind': 'emoji'},
    {'content_known': False}, {'truncated': True}, {'role': 'unknown'},
])
def test_non_direct_or_incomplete_text_cannot_prove_acceptance(change):
    rows = [INVITE, {**ACCEPT, **change}]
    assert validate_relationship_assessment(candidate('accepted', rows), payload(rows))['status'] == 'insufficient'


@pytest.mark.parametrize('bad', [
    None, [], {'stage': True}, {'stage': 'in_love'}, {'stage': 'accepted', 'evidence': []},
    {'stage': 'accepted', 'evidence': [{'message_id': 'old', 'quote': '好呀'}]},
    {'stage': 'accepted', 'evidence': [{'message_id': 'm1', 'quote': 'never in this record'}]},
    {'stage': 'accepted', 'evidence': [{'message_id': 'm1', 'quote': 3}]},
])
def test_invalid_assessments_are_local_insufficient_results(bad):
    actual = validate_relationship_assessment(bad, payload([INVITE, ACCEPT]))
    assert actual['status'] == 'insufficient' and actual['stage'] == 'unknown' and actual['evidence'] == []


def test_duplicate_ids_and_relevant_history_are_not_verifiable_evidence():
    context = payload([INVITE, ACCEPT, {**ACCEPT, 'role': 'self'}])
    assert validate_relationship_assessment(candidate('accepted', [INVITE, ACCEPT]), context)['status'] == 'insufficient'
    context = {**payload([ACCEPT]), 'relevant': [INVITE]}
    assert validate_relationship_assessment(candidate('accepted', [INVITE, ACCEPT]), context)['status'] == 'insufficient'


def test_ordinary_hello_and_multiturn_stages_do_not_require_adult_context():
    intro = [message('m1', 'self', '你好，我叫小明'), message('m2', 'other', '你好，我是小夏')]
    assert validate_relationship_assessment(candidate('new', intro), payload(intro, adult=False))['status'] == 'suggested'
    rows = intro + [message('m3', 'self', '最近在看哪类小说？'), message('m4', 'other', '轻松搞笑的，看完不费脑子')]
    assert validate_relationship_assessment(candidate('talking', rows), payload(rows, adult=False))['stage'] == 'talking'
    assert validate_relationship_assessment(candidate('talking', intro), payload(intro))['status'] == 'insufficient'


@pytest.mark.parametrize('stage', ['invited', 'accepted', 'scheduled'])
def test_advanced_stage_does_not_infer_or_enable_adult_context(stage):
    actual = validate_relationship_assessment(candidate(stage, [INVITE, ACCEPT]), payload([INVITE, ACCEPT], adult=False))
    assert actual['status'] == 'insufficient' and actual['stage'] == 'unknown'
    assert '用户确认双方成年' in actual['reason']


def engine_result(rows, assessment, *, extra=None, response_format='json_object'):
    calls = []
    content = {'choices': CHOICES}
    if assessment != 'missing':
        content['relationship_assessment'] = assessment
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'model': 'fixture', 'choices': [{'message': {'content': json.dumps(content)}}]})
    context = {'contact_id': 'A', **payload(rows), **(extra or {})}
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        actual = ReplyEngine(client).generate(context, {**CONFIG, 'response_format': response_format}, intent='我想邀约')
    return actual, calls


@pytest.mark.parametrize('value', ['missing', None, {'stage': 'accepted', 'evidence': [{'message_id': 'fake', 'quote': 'fake'}]}])
def test_missing_or_invalid_assessment_preserves_choices_without_extra_request(value):
    actual, calls = engine_result([INVITE, ACCEPT], value)
    assert len(calls) == 1 and len(actual['choices']) == 3
    assert actual['relationship_assessment']['status'] == 'insufficient'


def test_same_network_call_returns_both_choices_and_scoped_assessment_schema():
    actual, calls = engine_result([INVITE, ACCEPT], candidate('accepted', [INVITE, ACCEPT]), response_format='json_schema')
    assert len(calls) == 1 and actual['relationship_assessment']['stage'] == 'accepted'
    schema = calls[0]['response_format']['json_schema']['schema']
    assert schema['additionalProperties'] is False
    assert 'relationship_assessment' in schema['properties'] and 'relationship_assessment' in schema['required']
    prompt = calls[0]['messages'][0]['content']
    assert '手动confirmed_stage' in prompt and '不返回好感' in prompt


def test_prompt_budget_truncation_and_omitted_messages_remain_unknown():
    rows = [message(str(i), 'self' if i % 2 else 'other', '长' * 1201) for i in range(30)]
    actual, calls = engine_result(rows, candidate('talking', rows[-4:]))
    sent = json.loads(calls[0]['messages'][1]['content'])['context']['recent']
    assert len(sent) < len(rows) and all(row['truncated'] for row in sent)
    scope = actual['relationship_assessment']['scope']
    assert scope['available_count'] == 30 and scope['read_count'] == len(sent) and scope['truncated'] is True
    assert scope['text_count'] == 0 and actual['relationship_assessment']['status'] == 'insufficient'


def make_app(tmp_path):
    app = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    app._require_api_config = lambda config: None
    app.state['settings']['api_ready'] = True
    app.settings.values['auto_generate'] = False
    app.settings.model_config = lambda: {**CONFIG, 'api_key': 'fixture-placeholder'}
    apply_enabled_snapshot(app, {'contact': {'id': 'A', 'name': '虚构角色'}, 'messages': [INVITE, ACCEPT]})
    app.state['contact_preferences']['adult_context'] = True
    return app


def wait_finished(app):
    deadline = time.monotonic() + 3
    while app.generating and time.monotonic() < deadline:
        time.sleep(.01)
    assert app.generating is False


def ready_engine_result():
    return {'choices': [{'id': f'choice-{i}', **c} for i, c in enumerate(CHOICES)],
            'source': 'model', 'model': 'fixture', 'elapsed_ms': 1,
            'relationship_assessment': validate_relationship_assessment(candidate('accepted', [INVITE, ACCEPT]), payload([INVITE, ACCEPT]))}


def test_controller_exposes_fenced_suggestion_without_saving_stage_or_enabling_adult(tmp_path):
    app = make_app(tmp_path)
    saved_preferences = app.contact_preferences.path.read_bytes()
    app.engine.generate = lambda *args, **kwargs: ready_engine_result()
    try:
        before = app.get_state()['contact_preferences'].copy()
        assert app.dispatch('generate')['ok']
        wait_finished(app)
        state = app.get_state()
        assessment = state['relationship_assessment']
        assert assessment['stage'] == 'accepted'
        assert assessment['contact_id'] == 'A' and assessment['revision'] == state['revision']
        assert state['contact_preferences'] == before
        assert app.contact_preferences.path.read_bytes() == saved_preferences
        assert app.dispatch('save_contact_preferences', {'contact_id': 'A', 'revision': state['revision'],
            'pursuit_stage': assessment['stage']})['ok']
        assert app.get_state()['contact_preferences']['pursuit_stage'] == 'accepted'
        assert app.get_state()['relationship_assessment'] is None
        assert app.dispatch('save_contact_preferences', {'contact_id': 'A', 'revision': state['revision'],
            'pursuit_stage': 'scheduled'})['ok'] is False
    finally:
        app.close()


@pytest.mark.parametrize('change', ['message', 'contact', 'preferences', 'settings', 'disconnect'])
def test_fresh_context_and_relevant_settings_clear_suggestion(tmp_path, change):
    app = make_app(tmp_path)
    app.engine.generate = lambda *args, **kwargs: ready_engine_result()
    try:
        app.dispatch('generate')
        wait_finished(app)
        assert app.get_state()['relationship_assessment']['status'] == 'suggested'
        state = app.get_state()
        if change == 'message':
            apply_enabled_snapshot(app, {'contact': state['contact'], 'messages': [INVITE, ACCEPT, message('m3', 'other', '不去')]})
        elif change == 'contact':
            apply_enabled_snapshot(app, {'contact': {'id': 'B', 'name': '另一虚构角色'}, 'messages': [message('b1', 'other', '你好')]})
        elif change == 'preferences':
            app.dispatch('save_contact_preferences', {'contact_id': 'A', 'revision': state['revision'], 'adult_context': False})
        elif change == 'settings':
            app.dispatch('save_settings', {'model': 'different-fixture'})
        else:
            app._connection_error('fixture disconnected')
        assert app.get_state()['relationship_assessment'] is None
    finally:
        app.close()


def test_background_only_save_keeps_suggestion_and_selected_draft(tmp_path):
    app = make_app(tmp_path)
    app.state['contact_preferences'] = app.contact_preferences.save('A', {'adult_context': True})
    app.engine.generate = lambda *args, **kwargs: ready_engine_result()
    try:
        app.dispatch('generate')
        wait_finished(app)
        app.dispatch('choose', {'id': 'choice-0'})
        before = app.get_state()
        scene = 'park' if before['contact_preferences']['scene_id'] != 'park' else 'cafe'
        changed = app.dispatch('save_contact_preferences', {'contact_id': 'A', 'revision': before['revision'], 'scene_id': scene})
        assert changed['ok'] and changed['state']['revision'] == before['revision']
        assert changed['state']['relationship_assessment'] == before['relationship_assessment']
        assert changed['state']['draft'] == before['draft']
    finally:
        app.close()


@pytest.mark.parametrize('change', ['message', 'contact', 'preferences'])
def test_async_assessment_stays_strict_and_old_replies_are_labelled_during_incoming_append(tmp_path, change):
    app = make_app(tmp_path)
    started, release = threading.Event(), threading.Event()
    def generate(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return ready_engine_result()
    app.engine.generate = generate
    try:
        app.dispatch('generate')
        assert started.wait(2)
        state = app.get_state()
        if change == 'message':
            apply_enabled_snapshot(app, {'contact': state['contact'], 'messages': [INVITE, ACCEPT, message('m3', 'other', '不去')]})
        elif change == 'contact':
            apply_enabled_snapshot(app, {'contact': {'id': 'B', 'name': '另一虚构角色'}, 'messages': [message('b1', 'other', '你好')]})
        else:
            app.dispatch('save_contact_preferences', {'contact_id': 'A', 'revision': state['revision'], 'pursuit_goal': 'invite'})
        release.set()
        wait_finished(app)
        final = app.get_state()
        assert final['relationship_assessment'] is None
        if change == 'message':
            assert final['choices'] == []
            assert final['reply_batch'] is None
            assert final['generation']['status'] == 'collecting'
            assert app.pending_at is not None
        else:
            assert final['choices'] == []
    finally:
        release.set()
        app.close()
