"""Foreground transitions must stop work, not merely hide its status label."""
from contact_fixtures import apply_enabled_snapshot
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import httpx
import pytest

from galchat.activity import ActivityPaused
from galchat.controller import Controller
from galchat.engine import ReplyEngine


class Group:
    active = True
    diagnostic = {}
    thread = None

    def is_active(self):
        return self.active

    def capture_guard(self):
        assert self.active
        return nullcontext()

    def close(self):
        pass


def snapshot(name='A', text='今天有点累', mid='a1'):
    return {'contact': {'id': name, 'name': name}, 'messages': [
        {'id': mid, 'role': 'other', 'kind': 'text', 'text': text, 'timestamp': '', 'source': 'fixture'}]}


CHOICES = [{'id': f'c{i}', 'text': f'虚构选项{i}', 'label': '回应', 'hint': 'fixture', 'tone': 'curious'} for i in range(3)]
RESULT = {'choices': CHOICES, 'source': 'model', 'model': 'fixture', 'elapsed_ms': 1}


@pytest.fixture
def app(tmp_path):
    value = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    value.window_group = Group()
    value._sync_activity()
    apply_enabled_snapshot(value, snapshot())
    value.connected = True
    value.state['connection'].update(can_read=True, can_fill=True)
    value.state['settings']['api_ready'] = True
    value._require_api_config = lambda _: None
    value.adapter = SimpleNamespace(snapshot=lambda: snapshot(), status=lambda: {'can_read': True, 'can_fill': True})
    yield value
    value.close()


def finish(app):
    deadline = time.monotonic() + 3
    while app.generating and time.monotonic() < deadline:
        time.sleep(.005)
    assert not app.generating


@pytest.mark.parametrize('operation', ['connect', 'open_wechat', 'load_history', 'choose_fill', 'fill_draft',
    'attachment_preview', 'analyze_attachment', 'transcribe_voice', 'refresh_voice_text', 'visual_setup_capture'])
def test_background_native_commands_stop_before_touching_an_adapter(app, operation):
    app.window_group.active = False
    app.adapter = app.current_adapter = None
    with pytest.raises(ActivityPaused):
        app._handle_native(operation, {})
    assert app.pending_at is None


@pytest.mark.parametrize('method', ['generate', 'copy_draft', 'get_sticker', 'test_model', 'choose', 'save_settings'])
def test_direct_routes_are_gated_too(app, method):
    app.window_group.active = False
    response = app.dispatch(method, {'text': '虚构草稿'})
    assert response['ok'] is False
    assert '暂停后台' in response['error']
    assert response['state']['activity']['active'] is False


def test_background_clears_pending_jobs_and_never_starts_model_or_media(app):
    app.pending_at = 0
    app._voice_job = {'message': {'id': 'v1'}}
    app._image_job = {'deadline': 0}
    app.engine = SimpleNamespace(generate=lambda *_a, **_k: pytest.fail('background model call'))
    app.window_group.active = False
    app._maybe_generate()
    app._maybe_prepare_voice()
    app._maybe_prepare_image()
    assert app.pending_at is None and app._voice_job is None and app._image_job is None
    assert app.get_state()['can_fill_draft'] is False


def test_background_poll_never_reads_the_current_chat(app):
    app.adapter.snapshot = lambda: pytest.fail('background snapshot')
    app.window_group.active = False
    with pytest.raises(ActivityPaused):
        app._poll_current()


def test_current_chat_stops_before_native_write_if_focus_leaves_during_read():
    from galchat.current_chat import CurrentChatAdapter
    active = True
    writes = []
    pattern = SimpleNamespace(Value='', SetValue=writes.append)
    edit = SimpleNamespace(GetValuePattern=lambda: pattern)
    def guard():
        if not active:
            raise ActivityPaused('paused')
    def recent(*_):
        nonlocal active
        active = False
        return []
    backend = SimpleNamespace(focus=lambda _: pytest.fail('must not reclaim foreground'))
    adapter = CurrentChatAdapter(SimpleNamespace(resolve=lambda n: {'id': n}, recent=recent),
        SimpleNamespace(backend=backend), lambda: {'history_limit': 100})
    adapter.activity_check = guard
    adapter._last = {'contact': {'id': 'A'}, 'messages': []}
    adapter.current = lambda **_: {'name': 'A', 'mode': 'uia', 'window': {}, 'edit': edit}
    with pytest.raises(ActivityPaused):
        adapter.fill_draft('A', '虚构台词')
    assert writes == []


def test_return_requires_new_identity_before_any_generation(app):
    calls = []
    app.engine = SimpleNamespace(generate=lambda context, *_a, **_k: calls.append(context) or RESULT)
    app.window_group.active = False
    app._sync_activity()
    app.window_group.active = True
    app._sync_activity()
    app.pending_at = 0
    app._maybe_generate()
    assert calls == []
    app.adapter.snapshot = lambda: snapshot('B', '新联系人的话', 'b1')
    app.contact_preferences.save('B', {'assistance_enabled': True})
    app._poll_current()
    app.pending_at = 0
    app._maybe_generate()
    finish(app)
    assert calls[0]['contact_id'] == 'B'
    assert app.get_state()['activity'] == {'active': True, 'needs_refresh': False}


def test_same_chat_restores_ready_options_without_spending_again(app):
    app.pending_at = None
    app.state['choices'] = CHOICES
    app.state['generation'] = {'status': 'ready', 'source': 'model'}
    app.window_group.active = False
    app._sync_activity()
    app.window_group.active = True
    app._poll_current()
    app.engine = SimpleNamespace(generate=lambda *_a, **_k: pytest.fail('unnecessary repeat request'))
    app._maybe_generate()
    assert [c['text'] for c in app.state['choices']] == [c['text'] for c in CHOICES]
    assert app.choice_revision == app.revision and app.pending_at is None


def test_changed_reply_settings_cancel_saved_options_before_foreground_refresh(app):
    app.pending_at = None
    app.state['choices'] = CHOICES
    app.state['generation'] = {'status': 'ready'}
    app.window_group.active = False
    app._sync_activity()
    app.window_group.active = True
    assert app.dispatch('save_settings', {'reply_style': 'serious'})['ok']
    app._poll_current()
    assert app.state['choices'] == [] and app.pending_at is not None


def test_hook_remembers_a_brief_leave_even_if_foreground_already_returned(app):
    old = app._activity_epoch
    app._background_event.set()
    app._sync_activity()
    assert app._activity_epoch != old
    with pytest.raises(ActivityPaused):
        app._handle_native('choose_fill', {'_activity_epoch': old})
    assert app._needs_fresh_snapshot


def test_late_generator_result_is_discarded_without_blocking_the_provider(app):
    entered, released = threading.Event(), threading.Event()
    def generate(*_a, **_k):
        entered.set()
        assert released.wait(3)
        return RESULT
    app.engine = SimpleNamespace(generate=generate)
    app.pending_at = 0
    app._maybe_generate()
    assert entered.wait(2)
    app.window_group.active = False
    app._sync_activity()
    released.set()
    finish(app)
    assert app.state['choices'] == []
    assert app.model_availability.blocked(app._model_signature()) is None


def test_interrupted_input_is_not_automatically_resubmitted_on_repeated_returns(app):
    entered, released = threading.Event(), threading.Event()
    calls = []
    def generate(*_a, **_k):
        calls.append(1)
        entered.set()
        assert released.wait(3)
        return RESULT
    app.engine = SimpleNamespace(generate=generate)
    app.pending_at = 0
    app._maybe_generate()
    assert entered.wait(2)
    app.window_group.active = False
    app._sync_activity()
    released.set()
    finish(app)
    for _ in range(3):
        app.window_group.active = True
        app._poll_current()
        app._maybe_generate()
        assert app.state['generation']['status'] == 'interrupted'
        assert app.pending_at is None
        app.window_group.active = False
        app._sync_activity()
    assert len(calls) == 1 and app.state['choices'] == []
    app.window_group.active = True
    app.adapter.snapshot = lambda: snapshot(text='新的虚构消息', mid='a2')
    app._poll_current()
    assert app.pending_at is not None  # New content is a new task.


def test_manual_retry_can_resubmit_an_interrupted_generation(app):
    app._interrupted_signature = app._generation_signature()
    app.pending_at = None
    app.engine = SimpleNamespace(generate=lambda *_a, **_k: RESULT)
    response = app.dispatch('generate')
    assert response['ok']
    finish(app)
    assert len(app.state['choices']) == 3


def test_background_http_result_cannot_start_fact_review_or_format_repair():
    active = True
    calls, usage = [], []
    def guard():
        if not active:
            raise ActivityPaused('paused')
    def handler(request):
        nonlocal active
        calls.append(request)
        active = False
        # Bad candidate would ordinarily start another HTTP request.
        return httpx.Response(200, json={'choices': [{'message': {'content': 'not JSON'}}],
            'usage': {'total_tokens': 21}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        engine = ReplyEngine(client, usage_observer=usage.append)
        with pytest.raises(ActivityPaused):
            engine.generate({'contact_id': 'A', 'recent': snapshot()['messages']},
                {'base_url': 'https://fixture.test/v1', 'model': 'fixture',
                 'verify_replies': True, 'activity_check': guard})
    assert len(calls) == 1 and usage[0]['usage']['total_tokens'] == 21


def test_background_preflight_performs_zero_http_calls():
    def handler(_):
        pytest.fail('HTTP must not start')
    def guard():
        raise ActivityPaused('paused')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ActivityPaused):
            ReplyEngine(client).probe({'base_url': 'https://fixture.test/v1', 'model': 'fixture', 'activity_check': guard})


def test_pause_does_not_change_user_preferences_or_native_draft(app):
    app.settings.save({'history_limit': 1000})
    app.state['paused'] = True
    app.state['draft'] = '用户保留的草稿'
    app.window_group.active = False
    app._sync_activity()
    app.window_group.active = True
    app._sync_activity()
    assert app.state['paused'] is True
    assert app.state['draft'] == '用户保留的草稿'
    assert app.settings.values['history_limit'] == 1000
