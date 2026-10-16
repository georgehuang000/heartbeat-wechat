import json
from pathlib import Path

import pytest

from galchat.contact_database import contact_key
from galchat.contact_preferences import ContactPreferences
from galchat.controller import Controller
from galchat.scenes import SCENE_IDS, default_scene_id


def test_initial_scene_is_stable_without_writing_or_using_remark(tmp_path):
    identity = contact_key('account', 'person')
    first = ContactPreferences(tmp_path).get(identity, {'name': '旧备注', 'gender': 2})
    second = ContactPreferences(tmp_path).get(identity, {'name': '新备注', 'gender': 1})
    assert first['scene_id'] == second['scene_id'] == default_scene_id(identity)
    assert first['scene_id'] in SCENE_IDS
    assert not (tmp_path / 'contact-preferences.json').exists()


def test_scene_selection_persists_without_changing_role_stage_or_other_contacts(tmp_path):
    identity = contact_key('account', 'person')
    other = contact_key('other-account', 'person')
    prefs = ContactPreferences(tmp_path)
    prefs.save(identity, {'role': 'young-female', 'adult_context': True, 'pursuit_stage': 'talking'})
    prefs.save(identity, {'scene_id': 'cafe'})
    loaded = ContactPreferences(tmp_path)
    assert loaded.get(identity)['scene_id'] == 'cafe'
    assert loaded.get(identity)['role'] == 'young-female'
    assert loaded.get(identity)['pursuit_stage'] == 'talking'
    assert loaded.get(identity)['adult_context'] is True
    assert other not in loaded.values
    assert loaded.get(other)['scene_id'] == default_scene_id(other)


@pytest.mark.parametrize('scene', [True, 4, None, {}, [], 'https://example.com/image.png', '../scene.png'])
def test_invalid_scene_does_not_write_preference(tmp_path, scene):
    prefs = ContactPreferences(tmp_path)
    with pytest.raises(ValueError, match='背景不存在'):
        prefs.save('contact', {'scene_id': scene})
    assert not prefs.path.exists()


def test_corrupt_saved_scene_falls_back_without_losing_legacy_preferences(tmp_path):
    (tmp_path / 'contact-preferences.json').write_text(
        json.dumps({'contact': {'scene_id': [], 'role': 'middle-male', 'pursuit_stage': 'accepted'}}),
        encoding='utf-8',
    )
    person = ContactPreferences(tmp_path).get('contact')
    assert person['scene_id'] == default_scene_id('contact')
    assert person['role'] == 'middle-male'
    assert person['pursuit_stage'] == 'accepted'


def test_scene_change_keeps_choices_selected_draft_revision_and_pending_generation(tmp_path):
    app = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    try:
        app._demo(True)
        first = app.get_state()
        selected = app.dispatch('choose', {'id': first['choices'][0]['id']})
        assert selected['ok']
        before = app.get_state()
        selected_revision = app.selected_revision
        app.pending_at = 123.0
        result = app.dispatch('save_contact_preferences', {
            'contact_id': before['contact']['id'], 'revision': before['revision'], 'scene_id': 'park',
        })
        assert result['state']['contact_preferences']['scene_id'] == 'park'
        assert app.revision == before['revision']
        assert app.state['choices'] == before['choices']
        assert app.state['draft'] == before['draft']
        assert app.pending_at == 123.0
        assert app.selected_revision == selected_revision == before['revision']
    finally:
        app.close()
