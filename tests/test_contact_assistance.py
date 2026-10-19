"""Allowlist defaults, identity separation and cancellation, without WeChat/model access."""
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from galchat.activity import ActivityPaused
from galchat.contact_database import contact_key
from galchat.contact_preferences import ContactPreferences
from galchat.controller import Controller
from galchat.current_chat import CurrentChatAdapter
from galchat.wechat import WeChatError

ROOT = Path(__file__).resolve().parents[1]


def snapshot(identity='alice', kind='text'):
    return {'contact': {'id': identity, 'name': identity}, 'messages': [
        {'id': identity+'-1', 'role': 'other', 'text': '今天有点累', 'kind': kind,
         'content_known': kind == 'text', 'timestamp': '2026-10-15T12:00:00'}]}


@pytest.fixture
def app(tmp_path):
    value = Controller(ROOT, tmp_path, start_worker=False)
    value.state['settings'] = value.settings.save({'ask_before_assist': True})
    yield value
    value.close()


def select(app, identity, enabled=False, kind='text'):
    if enabled:
        app.contact_preferences.save(identity, {'assistance_enabled': True})
    app._apply_snapshot(snapshot(identity, kind))
    return app.get_state()


def test_new_and_legacy_contacts_default_off_and_strict_bool(tmp_path):
    prefs = ContactPreferences(tmp_path)
    assert prefs.get('new')['assistance_enabled'] is False
    prefs.save('legacy', {'role': 'young-female'})
    assert prefs.get('legacy')['assistance_enabled'] is False
    for bad in ('true', 1, None):
        with pytest.raises(ValueError):
            prefs.save('new', {'assistance_enabled': bad})
    assert prefs.get('new')['assistance_enabled'] is False


def test_opt_in_persists_under_identity_not_remark_or_avatar(tmp_path):
    prefs = ContactPreferences(tmp_path)
    identity = contact_key('account', 'alice')
    prefs.save(identity, {'assistance_enabled': True, 'role': 'young-female'})
    prefs.save(identity, {'reset_appearance': True})
    restored = ContactPreferences(tmp_path)
    assert restored.get(identity, {'name': '新备注'})['assistance_enabled'] is True
    assert restored.get(contact_key('account', 'bob'))['assistance_enabled'] is False
    assert restored.get(contact_key('another-account', 'alice'))['assistance_enabled'] is False
    assert restored.get(contact_key('account', 'room@chatroom'))['assistance_enabled'] is False


def test_disabled_chat_has_local_text_but_no_persisted_context_or_jobs(app):
    state = select(app, 'alice', kind='image')
    assert state['contact']['name'] == 'alice'
    assert state['generation']['status'] == 'contact_disabled'
    assert len(state['messages']) == 1
    assert state['choices'] == state['memories'] == []
    assert app.pending_at is None and app._prepared_context is None
    assert app._automatic_image_candidate() is None
    assert app._automatic_voice_candidate() is None
    assert app.memory.list_contacts() == []
    app.pending_at = 0
    app.engine = SimpleNamespace(generate=lambda *a, **k: pytest.fail('disabled model call'))
    app._maybe_generate()
    assert app.pending_at is None


def test_only_explicit_chat_remains_enabled_after_switching(app):
    select(app, 'alice', enabled=True)
    assert len(app.state['messages']) == 1 and app.pending_at is not None
    select(app, 'bob')
    assert app.state['messages'][0]['id'] == 'bob-1' and app.pending_at is None
    select(app, 'alice')
    assert app.state['contact_preferences']['assistance_enabled'] is True
    assert len(app.state['messages']) == 1


@pytest.mark.parametrize('method', ['generate', 'choose', 'choose_fill', 'load_history',
    'get_sticker', 'transcribe_voice', 'analyze_attachment', 'attachment_preview'])
def test_disabled_routes_do_not_queue_work_or_model(app, method):
    select(app, 'alice')
    result = app.dispatch(method, {})
    assert result['ok'] is False and '尚未开启辅助' in result['error']
    assert app.commands.empty()


def test_enable_only_changes_current_contact_then_disable_clears_permissions(app):
    select(app, 'alice')
    result = app.dispatch('save_contact_preferences', {'contact_id': 'alice',
        'revision': app.revision, 'assistance_enabled': True})
    assert result['ok'] is True
    app._apply_snapshot(snapshot('alice'))
    assert app.pending_at is not None
    app.state['draft'] = '仅程序中的草稿'
    app._voice_job = {'message': {'id': 'v'}}
    app._image_job = {'late': True}
    result = app.dispatch('save_contact_preferences', {'contact_id': 'alice',
        'revision': app.revision, 'assistance_enabled': False})
    assert result['ok'] is True
    assert app.pending_at is app._image_job is app._voice_job is None
    assert len(app.state['messages']) == 1 and app.state['choices'] == []
    assert app.state['generation']['status'] == 'contact_disabled'
    assert app.contact_preferences.get('bob')['assistance_enabled'] is False


def test_stale_toggle_cannot_enable_different_person(app):
    select(app, 'alice')
    old = app.revision
    select(app, 'bob')
    result = app.dispatch('save_contact_preferences', {'contact_id': 'alice',
        'revision': old, 'assistance_enabled': True})
    assert result['ok'] is False
    assert not app.contact_preferences.get('alice')['assistance_enabled']
    assert not app.contact_preferences.get('bob')['assistance_enabled']


def test_queued_operation_is_not_retargeted_to_another_enabled_person(app):
    select(app, 'alice', enabled=True)
    select(app, 'bob', enabled=True)
    with pytest.raises(ActivityPaused):
        app._handle_native('load_history', {'_assist_contact_id': 'alice'})


def test_disable_cancels_inflight_progress_and_late_choices(app):
    select(app, 'alice', enabled=True)
    app.settings.save({'api_key': 'fictional-not-real'})
    app.state['settings'] = app.settings.public()
    started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    def generate(context, config, **kwargs):
        started.set()
        assert release.wait(3)
        try:
            config['activity_check']()
        except ActivityPaused:
            cancelled.set()
            raise
        pytest.fail('disabled generation continued')
    app.engine = SimpleNamespace(generate=generate)
    app.pending_at = 0
    app._maybe_generate()
    assert started.wait(2)
    try:
        app.dispatch('save_contact_preferences', {'contact_id': 'alice',
            'revision': app.revision, 'assistance_enabled': False})
    finally:
        release.set()
    deadline = time.monotonic()+3
    while app.generating and time.monotonic() < deadline:
        time.sleep(.005)
    assert cancelled.is_set() and not app.generating
    assert app.state['choices'] == []
    assert app.state['generation']['status'] == 'contact_disabled'


def test_reader_recognizes_locally_before_model_permission_without_title_model():
    reads = []
    database = SimpleNamespace(resolve=lambda name: {'id': name, 'name': name},
        recent=lambda *args: reads.append(args) or [])
    adapter = CurrentChatAdapter(database, None, lambda: {'history_limit': 100})
    adapter.contact_enabled = lambda contact: contact['id'] == 'alice'
    def current(**kwargs):
        assert kwargs == {'require_native': True}
        return {'name': 'bob', 'mode': 'uia', 'window': {'fingerprint': 'w', 'rect': []}}
    adapter.current = current
    result = adapter.snapshot()
    assert result['contact']['id'] == 'bob' and result['messages'] == []
    assert result['assistance_disabled'] is True and len(reads) == 1
    assert adapter.status()['can_read'] is True
    assert adapter.status()['can_fill'] is False


def test_allowlist_identity_failure_never_uses_visual_fallback():
    adapter = CurrentChatAdapter(SimpleNamespace(), None, lambda: {})
    adapter.contact_enabled = lambda contact: False
    def current(**kwargs):
        assert kwargs.get('require_native') is True
        raise WeChatError('native_input_lost', 'unavailable')
    adapter.current = current
    with pytest.raises(WeChatError):
        adapter.snapshot()
    assert adapter._last is None


def test_all_automatic_identity_checks_are_native_when_allowlist_is_installed():
    backend = SimpleNamespace(window=lambda: {'hwnd': 1})
    visual = SimpleNamespace(backend=backend,
        _capture=lambda: pytest.fail('unknown contact triggered screenshot/title model'))
    adapter = CurrentChatAdapter(SimpleNamespace(), visual, lambda: {})
    adapter.contact_enabled = lambda contact: False
    adapter._uia_current = lambda window: None
    with pytest.raises(WeChatError, match='尚未确认'):
        adapter.current()


def test_global_question_mode_defaults_off_and_all_chats_are_available(tmp_path):
    value = Controller(ROOT, tmp_path, start_worker=False)
    try:
        assert value.settings.values['ask_before_assist'] is False
        select(value, 'alice')
        assert value._assist_allowed() and len(value.state['messages']) == 1
        assert value.get_state()['assistance']['needs_choice'] is False
        value.contact_preferences.save('bob', {'assistance_enabled': False})
        select(value, 'bob')
        assert value._assist_allowed() and len(value.state['messages']) == 1
    finally:
        value.close()


def test_unanswered_and_explicit_no_have_different_persistent_states(app):
    state = select(app, 'alice')
    assert state['assistance']['needs_choice'] and state['assistance']['decision'] is None
    result = app.dispatch('save_contact_preferences', {'contact_id': 'alice', 'revision': app.revision,
        'assistance_enabled': False})
    assert result['ok'] and not result['state']['assistance']['needs_choice']
    assert result['state']['assistance']['decision'] == 'no'
    restored = ContactPreferences(app.settings.directory)
    assert restored.get('alice')['assistance_decided'] is True
    assert restored.get('alice')['assistance_enabled'] is False
    select(app, 'bob')
    state = select(app, 'alice')
    assert not state['assistance']['needs_choice'] and not state['assistance']['enabled']


def test_global_off_assists_everyone_and_on_restores_their_choices(app):
    select(app, 'alice')
    app.dispatch('save_contact_preferences', {'contact_id': 'alice', 'revision': app.revision,
        'assistance_enabled': False})
    result = app.dispatch('save_settings', {'ask_before_assist': False})
    assert result['ok'] and result['state']['assistance']['enabled']
    app._apply_snapshot(snapshot('alice'))
    assert len(app.state['messages']) == 1
    result = app.dispatch('save_settings', {'ask_before_assist': True})
    assert result['ok'] and not result['state']['assistance']['enabled']
    assert not result['state']['assistance']['needs_choice']
    assert len(result['state']['messages']) == 1


def test_switching_mode_preserves_finished_options_when_contact_stays_allowed(app):
    select(app, 'alice', enabled=True)
    app.state['choices'] = [{'id': 'saved', 'text': '不必重新生成'}]
    before = app.revision
    assert app.dispatch('save_settings', {'ask_before_assist': False})['ok']
    assert app.dispatch('save_settings', {'ask_before_assist': True})['ok']
    assert app.revision == before and app.state['choices'][0]['id'] == 'saved'


def test_legacy_yes_is_remembered_but_old_default_no_is_unanswered(tmp_path):
    prefs = ContactPreferences(tmp_path)
    prefs.values['old-yes'] = {'assistance_enabled': True}
    prefs.values['old-default'] = {'assistance_enabled': False, 'role': 'young-female'}
    assert prefs.get('old-yes')['assistance_decided'] is True
    assert prefs.get('old-default')['assistance_decided'] is False
    prefs.save('new', {'role': 'young-female'})
    assert prefs.get('new')['assistance_decided'] is False


@pytest.mark.parametrize('value', ['true', None, 1])
def test_question_mode_only_accepts_bool_and_does_not_partially_save(app, value):
    before = dict(app.settings.values)
    result = app.dispatch('save_settings', {'ask_before_assist': value, 'history_limit': 5000})
    assert result['ok'] is False and app.settings.values == before
