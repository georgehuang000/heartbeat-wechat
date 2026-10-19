from types import SimpleNamespace
import pytest
from galchat.wechat import WeChatError, _chat_inputs, _input_contact
from galchat.current_chat import CurrentChatAdapter
from galchat.native_accessibility import NativeAccessibility


def edit(name='千总', value=''):
    pattern = SimpleNamespace(Value=value)
    return SimpleNamespace(ClassName='mmui::ChatInputField', ControlTypeName='EditControl',
        AutomationId='chat_input_field', Name=name, GetValuePattern=lambda: pattern,
        IsOffscreen=False, GetChildren=lambda: [])


def test_viewport_does_not_hide_real_edit_and_offscreen_edit_is_excluded():
    actual = edit()
    viewport = SimpleNamespace(ClassName='QWidget', ControlTypeName='GroupControl',
        AutomationId='chat_input_field.qt_scrollarea_viewport', IsOffscreen=False)
    hidden = edit('旧联系人')
    hidden.IsOffscreen = True
    assert _chat_inputs([actual, viewport, hidden]) == [actual]
    dotted = edit()
    dotted.ClassName = 'OtherEdit'
    dotted.AutomationId = 'main.chat_input_field'
    assert _chat_inputs([dotted]) == [dotted]


def test_empty_voice_placeholder_only_is_removed_when_value_is_known_empty():
    name = '千总按住鼠标 语音输入文字'
    assert _input_contact(edit(name)) == '千总'
    assert _input_contact(edit(name, 'manual')) == name
    assert _input_contact(edit(name, None)) == name
    with pytest.raises(WeChatError):
        _input_contact(edit('按住鼠标 语音输入文字'))


@pytest.mark.parametrize('suffix', ['长按鼠标 语音输入', '长按鼠标语音输入', '按住鼠标 语音输入'])
def test_new_voice_placeholder_does_not_become_part_of_contact_identity(suffix):
    name = '测试联系人' + suffix
    assert _input_contact(edit(name)) == '测试联系人'
    assert _input_contact(edit(name, '已有草稿')) == name
    assert _input_contact(edit(name, None)) == name
    with pytest.raises(WeChatError):
        _input_contact(edit(suffix))


def test_native_current_reads_exact_name_without_capture_or_model():
    actual = edit('千总', '手动草稿')
    viewport = SimpleNamespace(ClassName='QWidget', AutomationId='chat_input_field.qt_scrollarea_viewport',
        ControlTypeName='GroupControl', GetChildren=lambda: [])
    root = SimpleNamespace(GetChildren=lambda: [viewport, actual])
    visual = SimpleNamespace(backend=SimpleNamespace(window=lambda: {'hwnd': 1}))
    adapter = CurrentChatAdapter(None, visual, lambda: {}, auto=SimpleNamespace(ControlFromHandle=lambda h: root))
    result = adapter.current(force=True)
    assert result['name'] == '千总' and result['edit'] is actual and result['mode'] == 'uia'


class Gate:
    def __init__(self, value=0):
        self.value = value
        self.writes = []
        self.closed = False
    def read(self):
        return self.value
    def write(self, value):
        self.writes.append(value)
        self.value = value
    def close(self):
        self.closed = True


def test_lease_activates_once_and_restores_original_state():
    gate = Gate()
    created = []
    def factory(window):
        created.append(window)
        return gate
    lease = NativeAccessibility(factory)
    window = {'pid': 1, 'hwnd': 2, 'fingerprint': 'first'}
    assert lease.ensure(window) and lease.ensure(window)
    assert len(created) == 1 and gate.writes == [1]
    lease.close()
    lease.close()
    assert gate.writes == [1, 0] and gate.closed


def test_lease_never_disables_preexisting_accessibility():
    gate = Gate(1)
    lease = NativeAccessibility(lambda w: gate)
    assert lease.ensure({'pid': 1, 'hwnd': 2})
    lease.close()
    assert gate.writes == [] and gate.value == 1


def test_unreviewed_version_is_not_retried_every_poll():
    attempts = []
    def factory(window):
        attempts.append(window)
        raise ValueError('unreviewed Weixin version')
    lease = NativeAccessibility(factory)
    assert not lease.ensure({'pid': 1, 'hwnd': 2})
    assert not lease.ensure({'pid': 1, 'hwnd': 2})
    assert len(attempts) == 1
    assert not lease.ensure({'pid': 3, 'hwnd': 4})
    assert len(attempts) == 2


def test_failed_activation_restores_partial_write():
    class Partial(Gate):
        def write(self, value):
            super().write(value)
            if value == 1:
                raise OSError('partial activation')
    gate = Partial()
    lease = NativeAccessibility(lambda w: gate)
    assert not lease.ensure({'pid': 1, 'hwnd': 2})
    assert gate.writes == [1, 0] and gate.closed


def test_unexpected_gate_byte_is_never_modified():
    gate = Gate(3)
    lease = NativeAccessibility(lambda w: gate)
    assert not lease.ensure({'pid': 1, 'hwnd': 2})
    assert gate.writes == [] and gate.closed


def test_new_process_restores_old_handle_before_activation():
    one, two = Gate(), Gate()
    gates = iter([one, two])
    lease = NativeAccessibility(lambda w: next(gates))
    assert lease.ensure({'pid': 1, 'hwnd': 2})
    assert lease.ensure({'pid': 3, 'hwnd': 4})
    assert one.closed and one.writes == [1, 0]
    lease.close()
    assert two.closed and two.writes == [1, 0]


def test_native_loss_never_invokes_ocr_mid_write():
    visual = SimpleNamespace(backend=SimpleNamespace(window=lambda: {'hwnd': 1}))
    adapter = CurrentChatAdapter(None, visual, lambda: {})
    adapter._uia_current = lambda window: None
    with pytest.raises(WeChatError) as error:
        adapter.current(require_native=True)
    assert error.value.code == 'native_input_lost'
