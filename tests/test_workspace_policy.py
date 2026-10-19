"""Both-window exposure and idle reading; no live chat, model or desktop I/O."""
from pathlib import Path
from types import SimpleNamespace as NS
import threading

import pytest

from galchat.activity import ActivityPaused
from galchat.contact_database import ContactDatabase
from galchat.controller import Controller
from galchat.current_chat import CurrentChatAdapter
from galchat.window_group import WindowGroup


def workspace(tmp_path):
    gui = NS(fg=1, order=[2, 1, 3], hidden=set(), minimized=set(), cloaked=set(), transparent=set(),
        rects={1: (0, 0, 400, 600), 2: (408, 0, 700, 600), 3: (0, 0, 1000, 800)}, owners={})
    gui.GetForegroundWindow = lambda: gui.fg
    gui.GetAncestor = lambda h, level: gui.owners.get(h, h) if level == 3 else h
    gui.IsWindow = lambda h: h in gui.rects
    gui.IsWindowVisible = lambda h: h not in gui.hidden
    gui.IsIconic = lambda h: h in gui.minimized
    gui.GetWindowRect = lambda h: gui.rects[h]
    gui.EnumWindows = lambda callback, arg: [callback(h, arg) for h in gui.order]
    backend = NS(gui=gui, api=NS(), con=NS(), _cloaked=lambda h: h in gui.cloaked,
        _layered_visibility=lambda h: {'fully_transparent': h in gui.transparent, 'verified_cursor_overlay': False})
    controller = NS(window=None, _background_event=threading.Event(),
        settings=NS(directory=tmp_path, values={}))
    value = WindowGroup(controller, backend)
    value._hwnd = lambda: 2
    value.target = 1
    value.visible = True
    return value, gui


@pytest.mark.parametrize('foreground', [1, 2])
def test_both_windows_exposed_and_either_focused_can_work(tmp_path, foreground):
    group, gui = workspace(tmp_path)
    gui.fg = foreground
    assert group.work_state() == {'active': True, 'reason': None}


@pytest.mark.parametrize('hidden', [1, 2])
@pytest.mark.parametrize('condition', ['hidden', 'minimized', 'cloaked'])
def test_one_unavailable_window_stops_work_even_when_other_is_focused(tmp_path, hidden, condition):
    group, gui = workspace(tmp_path)
    getattr(gui, condition).add(hidden)
    gui.fg = 2 if hidden == 1 else 1
    assert group.work_state()['active'] is False


@pytest.mark.parametrize('order', [[2, 3, 1], [1, 3, 2], [3, 2, 1]])
def test_external_window_over_either_member_stops_work(tmp_path, order):
    group, gui = workspace(tmp_path)
    gui.order = order
    assert group.work_state() == {'active': False, 'reason': 'window_obscured'}


def test_unrelated_uncovered_window_and_transparent_overlay_do_not_pause(tmp_path):
    group, gui = workspace(tmp_path)
    gui.order = [3, 2, 1]
    gui.rects[3] = (800, 0, 1000, 800)
    assert group.work_state()['active']
    gui.rects[3] = (0, 0, 1000, 800)
    gui.transparent.add(3)
    assert group.work_state()['active']


def test_pair_owned_popup_is_usable_but_other_software_foreground_is_not(tmp_path):
    group, gui = workspace(tmp_path)
    gui.order, gui.fg, gui.owners[3] = [3, 2, 1], 3, 1
    assert group.work_state()['active']
    gui.owners.clear()
    assert group.work_state()['reason'] == 'background'


def test_explicit_os_minimize_or_hide_does_not_reopen_on_wechat_return(tmp_path):
    for condition in ('hidden', 'minimized'):
        group, gui = workspace(tmp_path)
        getattr(gui, condition).add(2)
        group._restore_content = lambda **kwargs: pytest.fail('dismissed helper reopened')
        for gui.fg in (1, 3, 1):
            group._frame()
        assert group.manual_minimized and not group.visible
        assert group.controller._background_event.is_set()


def test_controller_checks_both_windows_before_read_or_model_and_keeps_settings_usable(tmp_path):
    controller = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    group, gui = workspace(tmp_path)
    controller.window_group = group
    gui.order = [2, 3, 1]
    controller.adapter = NS(snapshot=lambda: pytest.fail('covered source read'))
    controller.engine = NS(generate=lambda *_a, **_k: pytest.fail('covered model request'))
    try:
        with pytest.raises(ActivityPaused):
            controller._poll_current()
        controller._maybe_generate()
        assert controller.get_state()['activity']['reason'] == 'window_obscured'
        assert controller.dispatch('save_settings', {'font_size': 17})['ok']
    finally:
        controller.close()


def monitor():
    state = NS(token=1, name='alice', text='虚构消息', limit=100, reads=0)
    def recent(contact, count):
        state.reads += 1
        return [{'id': 'same-id', 'role': 'other', 'text': state.text, 'kind': 'text', 'content_known': True}]
    database = NS(change_token=lambda: state.token,
        resolve=lambda name: {'id': name, 'name': name}, recent=recent)
    adapter = CurrentChatAdapter(database, None, lambda: {'history_limit': state.limit})
    adapter.contact_enabled = lambda contact: False
    adapter.current = lambda **kwargs: {'name': state.name, 'mode': 'uia',
        'window': {'fingerprint': 'window', 'rect': (0, 0, 400, 600)}}
    return adapter, state


def test_static_chat_reads_once_then_probes_without_reloading_history():
    adapter, state = monitor()
    assert adapter.poll()['messages'][0]['text'] == state.text
    for _ in range(150):  # Five minutes of 2-second probes.
        assert adapter.poll() is None
    assert state.reads == 1
    assert adapter.monitor_stats == {'checks': 151, 'full_reads': 1, 'idle': True}
    assert adapter.status()['can_read'] and not adapter.status()['can_fill']


def test_change_signal_catches_same_id_edits_and_chat_or_history_switch():
    adapter, state = monitor()
    adapter.poll()
    state.text, state.token = '同一条消息的修改', 2
    assert adapter.poll()['messages'][0]['text'] == state.text
    state.name = 'bob'
    assert adapter.poll()['contact']['id'] == 'bob'
    state.limit = 200
    assert adapter.poll() is not None
    assert adapter.poll(force=True) is not None
    assert state.reads == 5


def test_missing_change_metadata_falls_back_to_reads_and_never_freezes():
    adapter, state = monitor()
    state.token = None
    assert adapter.poll() is not None
    state.text = '更新'
    assert adapter.poll()['messages'][0]['text'] == '更新'
    assert state.reads == 2


def test_local_metadata_token_includes_wal_replacement_new_shard_and_annotations(tmp_path):
    storage = tmp_path / 'account' / 'db_storage' / 'message'
    storage.mkdir(parents=True)
    db = ContactDatabase(tmp_path, tmp_path / 'data', NS(account_dir=str(storage.parent.parent)))
    (storage / 'message_0.db').write_bytes(b'fixture')
    token = db.change_token()
    assert token and db.change_token() == token
    for name in ('message_0.db-wal', 'message_1.db'):
        (storage / name).write_bytes(b'new')
        updated = db.change_token()
        assert updated != token
        token = updated
    annotation = db.directory / 'attachment-text'
    annotation.mkdir(parents=True)
    assert db.change_token() != token


def test_controller_skips_context_rebuild_on_idle_but_force_refresh_remains_authoritative(tmp_path):
    controller = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    adapter, state = monitor()
    controller.adapter = adapter
    try:
        controller._poll_current()
        revision = controller.revision
        controller._apply_snapshot = lambda _: pytest.fail('idle context rebuilt')
        for _ in range(3):
            assert controller._poll_current() is None
        assert state.reads == 1 and controller.revision == revision
    finally:
        controller.close()
