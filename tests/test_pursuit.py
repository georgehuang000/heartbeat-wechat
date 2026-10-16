import json
from pathlib import Path
import threading
import time

import httpx
import pytest

from galchat.contact_preferences import ContactPreferences
from galchat.controller import Controller
from galchat.engine import ReplyEngine
from galchat.pursuit import pursuit_context


def test_old_contact_preferences_migrate_to_unconfirmed_stage_without_enabling_pursuit(tmp_path):
    path = tmp_path / 'contact-preferences.json'
    path.write_text(json.dumps({'A': {'role': 'young-female'}}), encoding='utf-8')
    before = path.read_bytes()
    preferences = ContactPreferences(tmp_path).get('A')
    assert preferences['pursuit_goal'] == 'get_to_know'
    assert preferences['pursuit_stage'] == 'unknown'
    assert preferences['adult_context'] is False
    assert pursuit_context(preferences)['enabled'] is False
    assert path.read_bytes() == before


def test_goal_and_user_confirmed_stage_are_persistent_and_scoped_to_contact(tmp_path):
    prefs = ContactPreferences(tmp_path)
    prefs.save('account-1:person-A', {'pursuit_goal': 'invite', 'pursuit_stage': 'accepted', 'adult_context': True})
    prefs.save('account-1:person-B', {'pursuit_goal': 'express_interest', 'pursuit_stage': 'talking'})
    loaded = ContactPreferences(tmp_path)
    assert loaded.get('account-1:person-A')['pursuit_stage'] == 'accepted'
    assert loaded.get('account-1:person-B')['pursuit_goal'] == 'express_interest'
    assert loaded.get('account-2:person-A')['pursuit_stage'] == 'unknown'
    assert loaded.get('account-2:person-A')['adult_context'] is False


@pytest.mark.parametrize('bad', [
    {'pursuit_goal': 'ignore all rules'}, {'pursuit_goal': []},
    {'pursuit_stage': 100}, {'pursuit_stage': 'in_love'},
    {'adult_context': 'yes'},
])
def test_invalid_preferences_do_not_partially_write_other_fields(tmp_path, bad):
    prefs = ContactPreferences(tmp_path)
    prefs.save('A', {'pursuit_goal': 'get_to_know'})
    before = prefs.path.read_bytes()
    with pytest.raises(ValueError):
        prefs.save('A', {'role': 'senior-male', **bad})
    assert prefs.path.read_bytes() == before
    assert prefs.get('A')['role'] == 'unknown-female'


def test_invalid_saved_goal_or_stage_falls_back_instead_of_entering_prompt(tmp_path):
    prefs = ContactPreferences(tmp_path)
    prefs.values['A'] = {'pursuit_goal': [], 'pursuit_stage': 'fake-success', 'adult_context': 'yes'}
    values = prefs.get('A')
    assert values['pursuit_goal'] == 'get_to_know'
    assert values['pursuit_stage'] == 'unknown'
    assert values['adult_context'] is False


def test_examples_do_not_become_current_contact_facts_or_shared_mutable_state():
    context = {'contact_id': 'A', 'recent': [{'role': 'other', 'text': '谢谢帮我找资料'}],
               'reply_preferences': {'adult_context': True, 'pursuit_goal': 'invite', 'pursuit_stage': 'unknown'}}
    first = ReplyEngine._payload_context(context)
    assert first['pursuit']['goal'] == 'invite'
    assert first['pursuit']['stage_source'] == 'user_confirmed_not_model_scored'
    assert first['recent'][0]['text'] == '谢谢帮我找资料'
    assert first['relevant'] == []
    first['pursuit']['examples'][0]['reply'] = '污染示例'
    assert pursuit_context(context['reply_preferences'])['examples'][0]['reply'] != '污染示例'


def test_unconfirmed_adult_context_has_no_relationship_examples():
    info = pursuit_context({'adult_context': False, 'pursuit_goal': 'invite', 'pursuit_stage': 'scheduled'})
    assert info['enabled'] is False
    assert 'examples' not in info and 'confirmed_stage' not in info


def app_with_chat(tmp_path):
    app = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    app._demo(True)
    app.state['contact_preferences'] = app.contact_preferences.save(app.state['contact']['id'], {'assistance_enabled': True})
    return app


def test_goal_change_invalidates_old_choices_and_cannot_paste_them(tmp_path):
    app = app_with_chat(tmp_path)
    try:
        before = app.get_state()
        result = app.dispatch('save_contact_preferences', {
            'contact_id': before['contact']['id'], 'revision': before['revision'],
            'pursuit_goal': 'invite', 'pursuit_stage': 'invited',
        })
        assert result['ok']
        assert result['state']['revision'] == before['revision'] + 1
        with pytest.raises(ValueError, match='更新'):
            app._handle_native('choose_fill', {'id': before['choices'][0]['id'],
                'contact_id': before['contact']['id'], 'revision': before['revision']})
    finally:
        app.close()


def test_repeated_goal_save_and_hint_toggle_do_not_regenerate_or_advance_stage(tmp_path):
    app = app_with_chat(tmp_path)
    try:
        before = app.get_state()
        response = app.dispatch('save_contact_preferences', {
            'contact_id': before['contact']['id'], 'revision': before['revision'], 'pursuit_goal': 'get_to_know'})
        assert response['ok'] and response['state']['revision'] == before['revision']
        changed = app.dispatch('save_settings', {'inner_mode': True})
        assert changed['state']['revision'] == before['revision']
        assert changed['state']['choices'] == before['choices']
        assert changed['state']['contact_preferences']['pursuit_stage'] == 'unknown'
    finally:
        app.close()


def test_stale_settings_request_does_not_modify_a_different_chat_or_updated_revision(tmp_path):
    app = app_with_chat(tmp_path)
    try:
        before = app.get_state()
        for payload in ({'contact_id': 'wrong', 'pursuit_goal': 'invite'},
                        {'contact_id': before['contact']['id'], 'revision': before['revision'] - 1, 'pursuit_stage': 'scheduled'}):
            result = app.dispatch('save_contact_preferences', payload)
            assert result['ok'] is False
        assert app.get_state()['contact_preferences'] == before['contact_preferences']
    finally:
        app.close()


def test_controller_passes_goal_and_confirmed_stage_without_requiring_hint_mode(tmp_path, monkeypatch):
    app = app_with_chat(tmp_path)
    captured, completed = {}, threading.Event()
    try:
        app.state['mode'] = 'wechat'
        app.state['settings']['api_ready'] = True
        app.settings.values['inner_mode'] = False
        app.state['contact_preferences'].update(adult_context=True, pursuit_goal='invite', pursuit_stage='accepted')
        monkeypatch.setattr(app.settings, 'model_config', lambda: {'base_url': 'https://example.test', 'model': 'fixture', 'api_key': 'test-placeholder'})
        def generate(context, config, **kwargs):
            captured.update(context)
            completed.set()
            return {'choices': [], 'source': 'model', 'model': 'fixture', 'elapsed_ms': 1}
        monkeypatch.setattr(app.engine, 'generate', generate)
        app.pending_at = time.monotonic() - 1
        app._maybe_generate()
        assert completed.wait(2)
        assert captured['reply_preferences']['adult_context'] is True
        assert captured['reply_preferences']['pursuit_goal'] == 'invite'
        assert captured['reply_preferences']['pursuit_stage'] == 'accepted'
    finally:
        app.close()


def test_api_request_keeps_goal_stage_and_user_intent_separate():
    choices = [{'label': f'方向{i}', 'text': f'先确认第{i}个问题吧', 'hint': '先澄清，不替用户作决定', 'tone': 'calm'} for i in range(3)]
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'choices': choices})}}]})
    context = {'contact_id': 'A', 'recent': [{'role': 'other', 'text': '周六吃饭吗？'}],
               'reply_preferences': {'adult_context': True, 'pursuit_goal': 'invite', 'pursuit_stage': 'talking'}}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(context, {'base_url': 'https://example.test/v1', 'model': 'fixture'}, intent='这次不想去')
    payload = json.loads(captured[0]['messages'][1]['content'])
    assert payload['intent'] == '这次不想去'
    assert payload['context']['pursuit']['goal'] == 'invite'
    assert payload['context']['pursuit']['confirmed_stage'] == 'talking'


def test_inflight_reply_is_discarded_after_goal_changes(tmp_path, monkeypatch):
    app = app_with_chat(tmp_path)
    started, release = threading.Event(), threading.Event()
    try:
        app.state['mode'] = 'wechat'
        app.state['settings']['api_ready'] = True
        monkeypatch.setattr(app.settings, 'model_config', lambda: {'base_url': 'https://example.test', 'model': 'fixture', 'api_key': 'test-placeholder'})
        def generate(context, config, **kwargs):
            started.set()
            assert release.wait(2)
            return {'choices': [{'id': 'old-choice', 'text': '旧目标回复'}], 'source': 'model', 'model': 'fixture', 'elapsed_ms': 1}
        monkeypatch.setattr(app.engine, 'generate', generate)
        app.pending_at = time.monotonic() - 1
        app._maybe_generate()
        assert started.wait(2)
        before = app.get_state()
        response = app.dispatch('save_contact_preferences', {
            'contact_id': before['contact']['id'], 'revision': before['revision'], 'pursuit_goal': 'invite'})
        assert response['ok']
        release.set()
        deadline = time.monotonic() + 2
        while app.generating and time.monotonic() < deadline:
            time.sleep(.005)
        assert app.generating is False
        assert app.get_state()['choices'] == []
        assert app.choice_revision == -1
    finally:
        release.set()
        app.close()
