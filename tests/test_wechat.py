"""Fixture tests for adapter safety; these never operate the user's desktop."""
from types import SimpleNamespace
import unittest

from galchat.wechat import WeChatAdapter, WeChatError


class Pattern:
    IsReadOnly = False

    def __init__(self, value="", setter=True):
        self.Value = value
        self.writes = []
        self.setter = setter

    def SetValue(self, value):
        self.writes.append(value)
        if self.setter:
            self.Value = value
        return self.setter


class Node:
    def __init__(self, cls="", name="", aid="", children=(), rect=(0, 0, 900, 800), pattern=None):
        self.ClassName, self.Name, self.AutomationId = cls, name, aid
        self.children = list(children)
        self.bottom_children = list(children)
        self.IsOffscreen = False
        self.BoundingRectangle = SimpleNamespace(**dict(zip(("left", "top", "right", "bottom"), rect)))
        self.NativeWindowHandle = 123
        self.pattern = pattern
        self.actions = []
        self.pages = []

    def GetChildren(self):
        return self.children

    def GetValuePattern(self):
        return self.pattern

    def GetLegacyIAccessiblePattern(self):
        return None

    def GetScrollPattern(self):
        return None

    def WheelUp(self, **kwargs):
        self.actions.append("up")
        if self.pages:
            self.children = self.pages.pop(0)

    def WheelDown(self, **kwargs):
        self.actions.append("down")
        self.children = list(self.bottom_children)


def row(text, side="other"):
    x = 320 if side == "other" else 840
    avatar = Node(cls="mmui::AvatarView", rect=(x, 140, x + 35, 175))
    return Node("mmui::ChatTextItemView", text, "message_1", [avatar], rect=(300, 130, 900, 200))


def fixture(name="小雨", draft="", rows=None):
    edit = Node("mmui::ChatInputField", name, "MainView.body.chat_input_field", pattern=Pattern(draft))
    messages = Node("mmui::RecyclerListView", aid="MainView.body.chat_message_list", children=rows or [], rect=(300, 100, 900, 650))
    window = Node("mmui::MainWindow", "我的昵称", children=[messages, edit])
    root = Node(children=[window])
    auto = SimpleNamespace(GetRootControl=lambda: root)
    return WeChatAdapter(auto=auto, executable="missing.exe"), edit, messages, window


class WeChatAdapterTests(unittest.TestCase):
    def test_snapshot_is_read_only_and_accepts_dotted_ids(self):
        adapter, edit, messages, _ = fixture(rows=[row("周末一起吃饭？"), row("好呀", "self")])
        first, second = adapter.snapshot(), adapter.snapshot()
        self.assertEqual(first["contact"]["name"], "小雨")
        self.assertEqual([m["role"] for m in first["messages"]], ["other", "self"])
        self.assertEqual(first["messages"], second["messages"])
        self.assertEqual(messages.actions, [])
        self.assertEqual(edit.pattern.writes, [])
        self.assertIsNone(first["messages"][0]["timestamp"])

    def test_repeated_messages_keep_distinct_stable_ids(self):
        adapter, _, messages, _ = fixture(rows=[row("哈哈"), row("哈哈"), row("那就周末吧")])
        first = adapter.snapshot()["messages"]
        messages.children = [row("哈哈"), row("那就周末吧"), row("几点？")]
        second = adapter.snapshot()["messages"]
        self.assertNotEqual(first[0]["id"], first[1]["id"])
        self.assertEqual(first[1]["id"], second[0]["id"])
        self.assertEqual(first[2]["id"], second[1]["id"])

    def test_unknown_sender_is_never_assigned_to_user(self):
        adapter, _, _, _ = fixture(rows=[Node("mmui::ChatTextItemView", "周末见", "message_1")])
        with self.assertRaises(WeChatError) as caught:
            adapter.snapshot()
        self.assertEqual(caught.exception.code, "ambiguous_sender")
        self.assertFalse(adapter.status()["can_read"])

    def test_existing_draft_is_preserved(self):
        adapter, edit, _, _ = fixture(draft="我自己写到一半")
        contact = adapter.snapshot()["contact"]["id"]
        outcome = adapter.fill_draft(contact, "新的选项")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["code"], "draft_exists")
        self.assertEqual(edit.pattern.Value, "我自己写到一半")
        self.assertEqual(edit.pattern.writes, [])

    def test_changed_chat_cannot_receive_old_choice(self):
        adapter, edit, _, _ = fixture()
        old_contact = adapter.snapshot()["contact"]["id"]
        edit.Name = "另一个联系人"
        self.assertFalse(adapter.fill_draft(old_contact, "周末见")["ok"])
        self.assertEqual(edit.pattern.writes, [])

    def test_untouched_owned_draft_is_replaceable(self):
        adapter, edit, _, _ = fixture(draft='first')
        contact = adapter.snapshot()['contact']['id']
        self.assertTrue(adapter.fill_draft(contact, 'second', replace_text='first')['ok'])
        self.assertEqual(edit.pattern.Value, 'second')
        self.assertTrue(adapter.fill_draft(contact, 'third', replace_text='second')['ok'])
        self.assertEqual(edit.pattern.writes, ['second', 'third'])

    def test_modified_owned_draft_and_unowned_matching_choice_stay(self):
        adapter, edit, _, _ = fixture(draft='first edited')
        contact = adapter.snapshot()['contact']['id']
        self.assertEqual(adapter.fill_draft(contact, 'second', replace_text='first')['code'], 'draft_edited')
        self.assertEqual(adapter.fill_draft(contact, 'first edited')['code'], 'draft_exists')
        self.assertEqual(edit.pattern.writes, [])

    def test_edit_during_reacquire_stops_replacement(self):
        adapter, edit, _, _ = fixture(draft='first')
        contact = adapter.snapshot()['contact']['id']
        original = adapter._context
        calls = []
        def context():
            calls.append(True)
            if len(calls) == 2:
                edit.pattern.Value = 'first edited'
            return original()
        adapter._context = context
        self.assertEqual(adapter.fill_draft(contact, 'second', replace_text='first')['code'], 'draft_changed')
        self.assertEqual(edit.pattern.writes, [])

    def test_fill_only_sets_value_and_verifies_it(self):
        adapter, edit, _, _ = fixture()
        contact = adapter.snapshot()["contact"]["id"]
        outcome = adapter.fill_draft(contact, "明天见\n先休息吧")
        self.assertTrue(outcome["ok"])
        self.assertEqual(edit.pattern.writes, ["明天见\n先休息吧"])
        self.assertEqual(edit.pattern.Value, "明天见\n先休息吧")

    def test_failed_native_setter_does_not_claim_success(self):
        adapter, edit, _, _ = fixture()
        edit.pattern.setter = False
        outcome = adapter.fill_draft(adapter.snapshot()["contact"]["id"], "新的选项")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["code"], "fill_unsupported")

    def test_read_unavailable_draft_is_not_assumed_empty(self):
        adapter, edit, _, _ = fixture()
        edit.pattern = None
        outcome = adapter.fill_draft(adapter.snapshot()["contact"]["id"], "新的选项")
        self.assertEqual(outcome["code"], "draft_unreadable")

    def test_history_prepends_and_deduplicates_overlap(self):
        adapter, _, messages, _ = fixture(rows=[row("B"), row("C")])
        messages.pages = [[row("A"), row("B")]]
        result = adapter.load_history(pages=1)
        self.assertEqual([m["text"] for m in result["messages"]], ["A", "B", "C"])
        self.assertEqual(messages.actions.count("up"), 1)
        self.assertEqual(messages.actions[-1], "down")
        self.assertEqual([m["text"] for m in result["latest_messages"]], ["B", "C"])
        self.assertEqual([n.Name for n in messages.children], ["B", "C"])
        self.assertIn("翻阅 1 次", result["coverage"])

    def test_contact_identity_survives_window_recreation(self):
        adapter, _, _, window = fixture()
        first_id = adapter.snapshot()["contact"]["id"]
        window.NativeWindowHandle = 987654
        self.assertEqual(adapter.snapshot()["contact"]["id"], first_id)
        window.Name = "另一个账号"
        self.assertNotEqual(adapter.snapshot()["contact"]["id"], first_id)

    def test_generic_window_title_uses_only_account_navigation_avatar(self):
        adapter, _, _, window = fixture()
        window.Name = "微信"
        # A conversation-row avatar must not be mistaken for the logged-in user.
        window.children.append(Node("mmui::AvatarView", "小雨"))
        self.assertEqual(adapter.status()["status"], "account_unavailable")
        window.children.append(Node("mmui::MainTabBar", children=[Node("mmui::AvatarView", "我的账号")]))
        self.assertEqual(adapter.status()["status"], "connected")

    def test_same_name_visible_sessions_are_rejected(self):
        adapter, _, _, window = fixture()
        window.children.extend([Node("mmui::ChatSessionCell", "小雨\n今天"), Node("mmui::ChatSessionCell", "小雨\n昨天")])
        self.assertEqual(adapter.status()["status"], "ambiguous_contact")

    def test_restart_uses_persisted_overlap_without_counter_collisions(self):
        first, _, _, _ = fixture(rows=[row("A"), row("B")])
        previous = first.snapshot()
        restarted, _, _, _ = fixture(rows=[row("B"), row("C")])
        restarted.history_loader = lambda contact_id: previous["messages"] if contact_id == previous["contact"]["id"] else []
        result = restarted.snapshot()["messages"]
        self.assertEqual(result[0]["id"], previous["messages"][1]["id"])
        self.assertNotIn(result[1]["id"], [m["id"] for m in previous["messages"]])

    def test_login_window_is_reported_without_actions(self):
        login = Node("mmui::LoginWindow", "微信")
        auto = SimpleNamespace(GetRootControl=lambda: Node(children=[login]))
        status = WeChatAdapter(auto=auto, executable="missing.exe").status()
        self.assertEqual(status["status"], "login_required")
        self.assertFalse(status["can_fill"])


if __name__ == "__main__":
    unittest.main()
