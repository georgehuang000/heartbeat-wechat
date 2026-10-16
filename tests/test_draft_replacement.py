"""Ownership comes from a verified write, never from a client-selected draft."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from galchat.controller import Controller
from galchat.current_chat import CurrentChatAdapter


@pytest.fixture
def controller(tmp_path):
    app = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    app._demo(True)
    app.state['mode'] = 'wechat'
    app.contact_preferences.save(app.state['contact']['id'], {'assistance_enabled': True})
    calls = []
    class Adapter:
        ok = True
        def fill_draft(self, contact, text, **kwargs):
            calls.append((contact, text, kwargs))
            return {'ok': self.ok, 'message': 'filled' if self.ok else 'not verified'}
    app.adapter = Adapter()
    yield app, calls
    app.close()


def choose(app, index=0, **overrides):
    payload = {'contact_id': app.state['contact']['id'], 'revision': app.revision,
               'id': app.state['choices'][index]['id'], **overrides}
    return app._handle_native('choose_fill', payload)


def test_only_verified_fill_owns_previous_text(controller):
    app, calls = controller
    first, second, third = [c['text'] for c in app.state['choices']]
    app.state['draft'] = first  # Selection alone does not grant overwrite rights.
    app.selected_revision = app.revision
    choose(app)
    choose(app, 1)
    choose(app, 2)
    assert [c[2] for c in calls] == [{}, {'replace_text': first}, {'replace_text': second}]
    assert app._filled_draft == (app.state['contact']['id'], app.revision, third)


def test_failed_candidate_never_gains_ownership_and_verified_prior_can_retry(controller):
    app, calls = controller
    choose(app)
    prior = app.state['draft']
    app.adapter.ok = False
    with pytest.raises(ValueError, match='not verified'):
        choose(app, 1)
    assert app.state['draft'] == prior
    assert app._filled_draft == (app.state['contact']['id'], app.revision, prior)
    app.adapter.ok = True
    choose(app, 2)
    assert calls[-1][2] == {'replace_text': prior}


@pytest.mark.parametrize('change', ['revision', 'contact', 'demo'])
def test_ownership_is_scoped_to_contact_and_round(controller, change):
    app, calls = controller
    if change == 'demo':
        app.state['mode'] = 'demo'
        choose(app)
        app.state['mode'] = 'wechat'
    else:
        choose(app)
        if change == 'revision':
            app.revision += 1
            app.choice_revision = app.revision
        else:
            app.state['contact'] = {'id': 'another-account:another-person', 'name': 'other'}
            app.contact_preferences.save(app.state['contact']['id'], {'assistance_enabled': True})
    choose(app, 1)
    assert calls[-1][2] == {}


def test_stale_click_never_uses_owned_text(controller):
    app, calls = controller
    choose(app)
    with pytest.raises(ValueError, match='更新'):
        choose(app, 1, revision=app.revision - 1)
    with pytest.raises(ValueError, match='更新'):
        choose(app, 1, contact_id='wrong')
    assert len(calls) == 1


def current_fixture(value='first', focus=None):
    class Pattern:
        Value = value
        IsReadOnly = False
        writes = []
        def SetValue(self, text):
            self.writes.append(text)
            self.Value = text
    pattern = Pattern()
    edit = SimpleNamespace(GetValuePattern=lambda: pattern)
    database = SimpleNamespace(resolve=lambda name: {'id': name}, recent=lambda *args: [])
    visual = SimpleNamespace(backend=SimpleNamespace(focus=focus or (lambda window: None)))
    adapter = CurrentChatAdapter(database, visual, lambda: {'history_limit': 100})
    adapter._last = {'contact': {'id': 'A'}, 'messages': []}
    adapter.current = lambda **kwargs: {'name': 'A', 'mode': 'uia', 'window': {}, 'edit': edit}
    return adapter, pattern


def test_current_uia_replaces_owned_draft_and_reselects_without_writing():
    adapter, pattern = current_fixture()
    assert adapter.fill_draft('A', 'second', replace_text='first')['ok']
    assert adapter.fill_draft('A', 'second', replace_text='second')['ok']
    assert pattern.writes == ['second']


def test_current_uia_preserves_manual_and_unowned_matching_text():
    adapter, pattern = current_fixture('first edited')
    assert adapter.fill_draft('A', 'second', replace_text='first')['code'] == 'draft_edited'
    assert adapter.fill_draft('A', 'first edited')['code'] == 'draft_exists'
    assert pattern.writes == []


def test_current_uia_edit_during_focus_is_preserved():
    adapter, pattern = current_fixture()
    adapter.visual.backend.focus = lambda window: setattr(pattern, 'Value', 'manual edit')
    assert not adapter.fill_draft('A', 'second', replace_text='first')['ok']
    assert pattern.Value == 'manual edit'
    assert pattern.writes == []
