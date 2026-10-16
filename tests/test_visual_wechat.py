"""Offline screen fixtures: no desktop input, screenshots, or real API calls."""
from copy import deepcopy
import io
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from galchat.visual_wechat import VisualWeChatAdapter, Win32ScreenBackend, _possible_caret_change
from galchat.wechat import WeChatError


CONFIG = {"width": 600, "height": 500, "account_name": "test-account",
          "region": [180, 0, 600, 500], "title_region": [210, 10, 460, 55],
          "input_region": [185, 370, 595, 490]}


def message(text, role="other", kind="text", truncated=False):
    return {"text": text, "role": role, "kind": kind, "truncated": truncated}


class Screen:
    def __init__(self):
        self.info = {"hwnd": 42, "fingerprint": "window-one", "rect": [10, 10, 610, 510]}
        self.page = 0
        self.pages = [[message("哈哈"), message("后一句"), message("最新一句")],
                      [message("哈哈"), message("哈哈"), message("后一句")]]
        self.title = 1
        self.caret = False
        self.caret_size = (2, 16)
        self.draft = ""
        self.actions = []
        self.capture_error = None
        self.fail_restore = False

    def window(self):
        return deepcopy(self.info)

    def capture(self, window, region=None):
        if self.capture_error:
            raise self.capture_error
        image = Image.new("RGB", (600, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((230, 20, 250, 40), fill=(self.title, 0, 0))
        draw.rectangle((200, 100, 230, 110), fill=(self.page, 0, 0))
        if self.draft == "|":
            draw.rectangle((205, 380, 204 + self.caret_size[0], 379 + self.caret_size[1]), fill="black")
        elif self.draft:
            draw.rectangle((205, 380, 300, 395), fill=tuple(hashlib.sha256(self.draft.encode()).digest()[:3]))
            if self.caret:
                draw.rectangle((305, 380, 304 + self.caret_size[0], 379 + self.caret_size[1]), fill='black')
        elif self.caret:
            draw.rectangle((205, 380, 204 + self.caret_size[0], 379 + self.caret_size[1]), fill="black")
        return image if region is None else image.crop(region)

    def focus(self, window):
        self.actions.append("focus")

    def scroll(self, window, point, direction, amount):
        self.actions.append(direction)
        if direction == "up":
            self.page = min(self.page + 1, len(self.pages) - 1)
        elif self.fail_restore and "up" in self.actions:
            raise WeChatError("latest_unverified", "could not restore")
        else:
            self.page = 0

    def paste(self, window, point, text, *, before_write=None, after_focus=None):
        replace = before_write() if before_write is not None else False
        if after_focus is not None:
            after_focus()
        if before_write is not None:
            self.actions.append(('replace', replace))
        self.actions.append(("paste", text))
        self.draft = text


class Reader:
    def __init__(self, screen):
        self.screen = screen
        self.calls = 0
        self.after_read = None
        self.fail_page = None

    def read(self, image_bytes, config):
        self.calls += 1
        image = Image.open(io.BytesIO(image_bytes))
        page = image.getpixel((20, 100))[0]
        if self.fail_page == page:
            raise RuntimeError("fixture recognition failed")
        result = {"contact_name": "OCR 错别字", "messages": deepcopy(self.screen.pages[page]),
                  "input_text": self.screen.draft, "confidence": 0.9, "warnings": []}
        if self.after_read:
            self.after_read(self)
        return result

    def read_input(self, image_bytes, config):
        self.calls += 1
        image = Image.open(io.BytesIO(image_bytes))
        assert image.size == (410, 120)  # No title, messages or contact list sent.
        result = {"input_text": self.screen.draft}
        if self.after_read:
            self.after_read(self)
        return result


def fixture(bind=True):
    screen = Screen()
    reader = Reader(screen)
    adapter = VisualWeChatAdapter(lambda: {"api_key": "fixture-only"}, reader=reader, backend=screen)
    adapter.configure(CONFIG)
    if bind:
        adapter.preview()
        adapter.bind_contact("人工确认的名字")
    return adapter, screen, reader


class VisualAdapterTests(unittest.TestCase):
    def test_setup_is_local_and_returns_physical_size(self):
        adapter, screen, reader = fixture(False)
        setup = adapter.capture_setup()
        self.assertEqual((setup["width"], setup["height"]), (600, 500))
        self.assertTrue(setup["image_data_url"].startswith("data:image/png;base64,"))
        self.assertEqual(reader.calls, 0)
        self.assertEqual(screen.actions, [])

    def test_config_is_serializable_and_validates_nested_regions(self):
        adapter, _, _ = fixture(False)
        config = adapter.configure(CONFIG)
        self.assertEqual(config, CONFIG)
        config["region"][0] = 0
        self.assertEqual(adapter.calibration["region"][0], 180)
        for invalid in ({**CONFIG, "title_region": [0, 0, 50, 50]},
                        {**CONFIG, "input_region": [185, 20, 595, 490]},
                        {**CONFIG, "account_name": ""}):
            with self.assertRaises(WeChatError):
                adapter.configure(invalid)

    def test_confirmation_required_even_with_saved_calibration(self):
        adapter, _, _ = fixture(False)
        with self.assertRaises(WeChatError) as caught:
            adapter.snapshot()
        self.assertEqual(caught.exception.code, "contact_confirmation_required")
        with self.assertRaises(WeChatError):
            adapter.bind_contact("name")

    def test_confirmed_identity_is_stable_but_profile_namespaces_it(self):
        adapter, _, _ = fixture()
        first = adapter.snapshot()
        second, _, _ = fixture()
        self.assertEqual(first["contact"], second.snapshot()["contact"])
        self.assertEqual(first["contact"]["name"], "人工确认的名字")
        self.assertEqual(first["contact"]["identity_kind"], "confirmed_display_name")
        second.configure({**CONFIG, "account_name": "another-account"})
        second.preview()
        other = second.bind_contact("人工确认的名字")
        self.assertNotEqual(first["contact"]["id"], other["contact"]["id"])

    def test_snapshot_is_read_only_and_caret_does_not_call_api(self):
        adapter, screen, reader = fixture()
        first = adapter.snapshot()
        for _ in range(4):
            screen.caret = not screen.caret
            self.assertEqual(adapter.snapshot()["messages"], first["messages"])
            self.assertTrue(adapter.status()["can_read"])
        self.assertEqual(reader.calls, 1)
        self.assertEqual(screen.actions, [])
        screen.page = 1
        adapter.snapshot()
        self.assertEqual(reader.calls, 2)

    def test_title_switch_never_silently_rebinds_or_calls_api(self):
        adapter, screen, reader = fixture()
        screen.title = 2
        with self.assertRaises(WeChatError) as caught:
            adapter.snapshot()
        self.assertEqual(caught.exception.code, "contact_confirmation_required")
        self.assertEqual(reader.calls, 1)

    def test_preview_cannot_bind_after_the_chat_changed(self):
        adapter, screen, _ = fixture(False)
        adapter.preview()
        screen.page = 1
        with self.assertRaises(WeChatError):
            adapter.bind_contact("name")

    def test_resize_and_new_window_require_new_confirmation(self):
        adapter, screen, _ = fixture()
        screen.info["rect"][2] += 10
        with self.assertRaises(WeChatError) as caught:
            adapter.snapshot()
        self.assertEqual(caught.exception.code, "calibration_changed")
        adapter, screen, _ = fixture()
        screen.info["fingerprint"] = "recreated"
        with self.assertRaises(WeChatError):
            adapter.snapshot()

    def test_screen_change_while_model_reads_is_not_archived(self):
        adapter, screen, reader = fixture()
        prior = deepcopy(adapter._history)
        screen.page = 1
        reader.after_read = lambda _: setattr(screen, "title", 2)
        with self.assertRaises(WeChatError):
            adapter.snapshot()
        self.assertEqual(adapter._history, prior)

    def test_failed_background_image_requests_have_a_cooldown(self):
        adapter, screen, reader = fixture()
        screen.page = 1
        reader.fail_page = 1
        with self.assertRaises(RuntimeError):
            adapter.snapshot()
        self.assertEqual(reader.calls, 2)
        with self.assertRaises(WeChatError) as caught:
            adapter.snapshot()
        self.assertEqual(caught.exception.code, "vision_cooldown")
        self.assertEqual(reader.calls, 2)

    def test_unstable_local_frames_do_not_call_api(self):
        adapter, screen, reader = fixture()
        original_capture = screen.capture
        screen.page = 1
        def animated_capture(*args):
            result = original_capture(*args)
            screen.page = 1 - screen.page
            return result
        screen.capture = animated_capture
        with self.assertRaises(WeChatError) as caught:
            adapter.snapshot()
        self.assertEqual(caught.exception.code, "screen_unstable")
        self.assertEqual(reader.calls, 1)

    def test_occlusion_pauses_without_api_or_ui_actions(self):
        adapter, screen, reader = fixture()
        screen.capture_error = WeChatError("window_obscured", "obscured")
        self.assertFalse(adapter.status()["can_read"])
        with self.assertRaises(WeChatError):
            adapter.snapshot()
        self.assertEqual(reader.calls, 1)
        self.assertEqual(screen.actions, [])

    def test_uncertain_latest_message_blocks_old_text_generation(self):
        for last in (message("[语音]", kind="voice"), message("半句话", truncated=True),
                     message("看见但不知谁说", role="unknown"), message("[看不清]")):
            adapter, screen, _ = fixture(False)
            screen.pages[0] = [message("可靠的前一句"), message("12:00", role="system"), last]
            adapter.preview()
            snapshot = adapter.bind_contact("name")
            self.assertTrue(snapshot["needs_transcript"])
            self.assertTrue(snapshot["generation_blocked_reason"])
            self.assertTrue(all(row["role"] in {"self", "other"} for row in snapshot["messages"]))
            if last["kind"] == "voice":
                self.assertEqual(snapshot["messages"][-1]["kind"], "voice")
            else:
                self.assertEqual(len(snapshot["messages"]), 1)

    def test_history_preserves_repeated_messages_and_restores_latest(self):
        adapter, screen, _ = fixture()
        result = adapter.load_history(500)
        self.assertEqual([row["text"] for row in result["messages"]], ["哈哈", "哈哈", "后一句", "最新一句"])
        self.assertEqual(len({row["id"] for row in result["messages"]}), 4)
        self.assertEqual([row["text"] for row in result["latest_messages"]], ["哈哈", "后一句", "最新一句"])
        self.assertEqual(screen.page, 0)
        self.assertLessEqual(screen.actions.count("up"), 5)

    def test_history_error_restores_viewport_and_rolls_back_memory(self):
        adapter, screen, reader = fixture()
        prior = deepcopy(adapter._history)
        reader.fail_page = 1
        with self.assertRaises(RuntimeError):
            adapter.load_history()
        self.assertEqual(screen.page, 0)
        self.assertEqual(adapter._history, prior)

    def test_history_restore_failure_cannot_report_success(self):
        adapter, screen, _ = fixture()
        prior = deepcopy(adapter._history)
        screen.fail_restore = True
        with self.assertRaises(WeChatError):
            adapter.load_history()
        self.assertEqual(adapter._history, prior)

    def test_fill_existing_draft_and_wrong_contact_never_pastes(self):
        adapter, screen, _ = fixture()
        contact_id = adapter.snapshot()["contact"]["id"]
        self.assertFalse(adapter.fill_draft("wrong-contact", "reply")["ok"])
        screen.draft = "existing"
        self.assertFalse(adapter.fill_draft(contact_id, "reply")["ok"])
        self.assertEqual(screen.draft, "existing")
        self.assertFalse(any(isinstance(action, tuple) for action in screen.actions))

    def test_fill_pastes_only_and_requires_readback(self):
        adapter, screen, _ = fixture()
        contact_id = adapter.snapshot()["contact"]["id"]
        result = adapter.fill_draft(contact_id, "reply")
        self.assertTrue(result["ok"], result)
        self.assertEqual(screen.actions, ["focus", ("paste", "reply")])
        self.assertEqual(screen.draft, "reply")

    def test_fill_handles_blink_by_confirming_both_images_not_masking_text(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()["contact"]["id"]
        def blink(_):
            if not screen.draft:
                screen.caret = not screen.caret
        reader.after_read = blink
        result = adapter.fill_draft(contact_id, "reply")
        self.assertTrue(result["ok"], result)
        self.assertEqual(reader.calls, 4)  # preview, two empty caret states, readback

    def test_live_four_pixel_caret_is_allowed_only_with_verified_high_dpi(self):
        before = Image.new('RGB', (807, 225), 'white')
        after = before.copy()
        ImageDraw.Draw(after).rectangle((29, 27, 32, 63), fill='black')
        self.assertFalse(_possible_caret_change(before, after))
        self.assertTrue(_possible_caret_change(before, after, 2))
        self.assertFalse(_possible_caret_change(before, after, float('nan')))
        ImageDraw.Draw(after).rectangle((100, 40, 120, 60), fill='black')
        self.assertFalse(_possible_caret_change(before, after, 2))

    def test_fill_verifies_both_high_dpi_caret_phases_then_pastes(self):
        adapter, screen, reader = fixture()
        screen.caret_size = (4, 37)
        screen.dpi_scale = lambda _: 2
        contact_id = adapter.snapshot()['contact']['id']
        def blink(_):
            if not screen.draft:
                screen.caret = not screen.caret
        reader.after_read = blink
        result = adapter.fill_draft(contact_id, 'reply')
        self.assertTrue(result['ok'], result)
        self.assertEqual(reader.calls, 4)
        self.assertEqual(screen.actions, ['focus', ('paste', 'reply')])

    def test_high_dpi_vertical_bar_typed_during_verification_is_not_overwritten(self):
        adapter, screen, reader = fixture()
        screen.caret_size = (4, 37)
        screen.dpi_scale = lambda _: 2
        contact_id = adapter.snapshot()['contact']['id']
        reader.after_read = lambda _: setattr(screen, 'draft', '|')
        result = adapter.fill_draft(contact_id, 'reply')
        self.assertFalse(result['ok'])
        self.assertEqual(screen.draft, '|')
        self.assertFalse(any(isinstance(action, tuple) for action in screen.actions))

    def test_animated_messages_do_not_block_draft_check_or_readback(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()["contact"]["id"]
        reader.after_read = lambda _: setattr(screen, "page", 1 - screen.page)
        result = adapter.fill_draft(contact_id, "reply")
        self.assertTrue(result["ok"], result)
        self.assertEqual(screen.actions, ["focus", ("paste", "reply")])
        self.assertEqual(reader.calls, 3)

    def test_title_or_window_switch_during_input_read_never_pastes(self):
        for change in (lambda screen: setattr(screen, "title", 2),
                       lambda screen: screen.info.update(fingerprint="another-window")):
            adapter, screen, reader = fixture()
            contact_id = adapter.snapshot()["contact"]["id"]
            reader.after_read = lambda _, change=change: change(screen)
            result = adapter.fill_draft(contact_id, "reply")
            self.assertFalse(result["ok"])
            self.assertEqual(screen.actions, ["focus"])

    def test_whitespace_draft_is_preserved(self):
        adapter, screen, _ = fixture()
        contact_id = adapter.snapshot()["contact"]["id"]
        screen.draft = "   "
        result = adapter.fill_draft(contact_id, "reply")
        self.assertEqual(result["code"], "draft_exists")
        self.assertEqual(screen.draft, "   ")
        self.assertEqual(screen.actions, ["focus"])

    def test_fill_does_not_ignore_a_user_typed_vertical_bar(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()["contact"]["id"]
        reader.after_read = lambda _: setattr(screen, "draft", "|")
        result = adapter.fill_draft(contact_id, "reply")
        self.assertFalse(result["ok"])
        self.assertEqual(screen.draft, "|")
        self.assertFalse(any(isinstance(action, tuple) for action in screen.actions))

    def test_fill_does_not_ignore_a_user_typed_word(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()["contact"]["id"]
        reader.after_read = lambda _: setattr(screen, "draft", "new text")
        self.assertFalse(adapter.fill_draft(contact_id, "reply")["ok"])
        self.assertEqual(screen.draft, "new text")
        self.assertFalse(any(isinstance(action, tuple) for action in screen.actions))

    def test_failed_readback_never_claims_success(self):
        adapter, screen, _ = fixture()
        screen.paste = lambda window, point, text: setattr(screen, "draft", "wrong")
        result = adapter.fill_draft(adapter.snapshot()["contact"]["id"], "reply")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "fill_unverified")

    def test_untouched_owned_draft_can_change_twice_and_reselect(self):
        adapter, screen, _ = fixture()
        contact_id = adapter.snapshot()['contact']['id']
        self.assertTrue(adapter.fill_draft(contact_id, 'first')['ok'])
        for old, new in [('first', 'second'), ('second', 'third'), ('third', 'third')]:
            self.assertTrue(adapter.fill_draft(contact_id, new, replace_text=old)['ok'])
            self.assertEqual(screen.draft, new)
        self.assertEqual(screen.actions.count(('replace', True)), 3)

    def test_modified_owned_draft_is_preserved_including_whitespace(self):
        for modified in ['first!', 'first ', ' first', 'first\n', '   ']:
            adapter, screen, _ = fixture()
            screen.draft = modified
            result = adapter.fill_draft(adapter.snapshot()['contact']['id'], 'second', replace_text='first')
            self.assertEqual(result['code'], 'draft_edited')
            self.assertEqual(screen.draft, modified)
            self.assertFalse(any(isinstance(action, tuple) for action in screen.actions))

    def test_cleared_owned_draft_does_not_select_all(self):
        adapter, screen, _ = fixture()
        result = adapter.fill_draft(adapter.snapshot()['contact']['id'], 'second', replace_text='first')
        self.assertTrue(result['ok'], result)
        self.assertIn(('replace', False), screen.actions)

    def test_edit_between_initial_check_and_selection_stops_replacement(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()['contact']['id']
        screen.draft = 'first'
        original = screen.paste
        def user_edits_before_click(*args, **kwargs):
            screen.draft = 'first with my edit'
            original(*args, **kwargs)
        screen.paste = user_edits_before_click
        result = adapter.fill_draft(contact_id, 'second', replace_text='first')
        self.assertEqual(result['code'], 'draft_edited')
        self.assertEqual(screen.draft, 'first with my edit')
        self.assertEqual(screen.actions, ['focus'])

    def test_edit_during_replacement_ocr_stops_selection(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()['contact']['id']
        screen.draft = 'first'
        reader.after_read = lambda _: setattr(screen, 'draft', 'first manually edited')
        result = adapter.fill_draft(contact_id, 'second', replace_text='first')
        self.assertEqual(result['code'], 'draft_changed')
        self.assertEqual(screen.draft, 'first manually edited')
        self.assertEqual(screen.actions, ['focus'])

    def test_new_message_at_final_replacement_check_stops_selection(self):
        adapter, screen, _ = fixture()
        contact_id = adapter.snapshot()['contact']['id']
        screen.draft = 'first'
        checks = []
        def check():
            checks.append(True)
            if len(checks) == 2:
                raise WeChatError('messages_changed', 'new message')
        result = adapter.fill_draft(contact_id, 'second', replace_text='first', validate_before_paste=check)
        self.assertEqual(result['code'], 'messages_changed')
        self.assertEqual(screen.draft, 'first')
        self.assertEqual(screen.actions, ['focus'])

    def test_message_arriving_during_last_ocr_stops_replacement(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()['contact']['id']
        screen.draft = 'first'
        changed = [False]
        reader.after_read = lambda r: changed.__setitem__(0, r.calls >= 3)
        def check():
            if changed[0]:
                raise WeChatError('messages_changed', 'arrived during final OCR')
        result = adapter.fill_draft(contact_id, 'second', replace_text='first', validate_before_paste=check)
        self.assertEqual(result['code'], 'messages_changed')
        self.assertEqual(screen.draft, 'first')
        self.assertEqual(screen.actions, ['focus'])

    def test_replacement_collects_both_caret_phases_before_fast_refocus_check(self):
        adapter, screen, reader = fixture()
        contact_id = adapter.snapshot()['contact']['id']
        screen.draft = 'first'
        original_capture = screen.capture
        def blinking_capture(*args, **kwargs):
            if reader.calls >= 3:
                screen.caret = not screen.caret
            return original_capture(*args, **kwargs)
        screen.capture = blinking_capture
        result = adapter.fill_draft(contact_id, 'second', replace_text='first')
        self.assertTrue(result['ok'], result)
        self.assertIn(('replace', True), screen.actions)
        self.assertGreaterEqual(reader.calls, 5)


class OcclusionGeometryTests(unittest.TestCase):
    def backend(self, order):
        backend = Win32ScreenBackend.__new__(Win32ScreenBackend)
        window = {"hwnd": 42, "fingerprint": "fixture", "rect": [0, 0, 600, 500]}
        backend._dpi = lambda: None
        backend._cloaked = lambda _: False
        backend.window = lambda: window
        backend.gui = SimpleNamespace(
            EnumWindows=lambda callback, arg: [callback(hwnd, arg) for hwnd in order],
            IsWindowVisible=lambda _: True, IsIconic=lambda _: False,
            GetClassName=lambda hwnd: "FixtureOverlay" if hwnd != 42 else "FixtureWeChat",
            GetWindowLong=lambda hwnd, index: 0,
            GetWindowRect=lambda hwnd: [0, 0, 600, 500] if hwnd == 42 else [300, 100, 350, 200])
        backend._exe = lambda hwnd: (123, "C:/fixture/overlay.exe")
        backend.api = SimpleNamespace(GetSystemMetrics=lambda key: {76: 0, 77: 0, 78: 1920, 79: 1080}[key])
        return backend, window

    def test_other_app_above_chat_is_rejected_even_without_foreground_test(self):
        backend, window = self.backend([7, 42])
        with self.assertRaises(WeChatError) as caught:
            backend._check(window, [180, 0, 600, 500])
        self.assertEqual(caught.exception.code, "window_obscured")
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic["target_rect"], [0, 0, 600, 500])
        self.assertEqual(diagnostic["capture_area"], [180, 0, 600, 500])
        self.assertEqual(diagnostic["blockers"], [{"hwnd": 7, "class": "FixtureOverlay",
                                                 "rect": [300, 100, 350, 200], "exe": "overlay.exe",
                                                 "ex_style": 0, "layered_attributes": None}])
        self.assertNotIn("title", str(diagnostic))

    def test_other_app_below_chat_does_not_block(self):
        backend, window = self.backend([42, 7])
        backend._check(window, [180, 0, 600, 500])

    def test_nonoverlapping_window_above_is_allowed(self):
        backend, window = self.backend([7, 42])
        backend._check(window, [400, 0, 600, 500])

    def test_explicit_layered_alpha_zero_is_not_a_blocker(self):
        backend, window = self.backend([7, 42])
        backend.gui.GetWindowLong = lambda hwnd, index: 0x80000
        backend.gui.GetLayeredWindowAttributes = lambda hwnd: (0, 0, 2)
        backend._check(window, [180, 0, 600, 500])

    def test_nonzero_alpha_and_color_key_windows_still_block(self):
        for attributes in ((0, 1, 2), (0, 255, 2), (0, 0, 1)):
            backend, window = self.backend([7, 42])
            backend.gui.GetWindowLong = lambda hwnd, index: 0x80000 | 0x20
            backend.gui.GetLayeredWindowAttributes = lambda hwnd: attributes
            with self.assertRaises(WeChatError) as caught:
                backend._check(window, [180, 0, 600, 500])
            self.assertEqual(caught.exception.diagnostic["blockers"][0]["layered_attributes"],
                             dict(zip(("color_key", "alpha", "flags"), attributes)))

    def test_per_pixel_layered_unknown_opacity_and_transparent_style_still_block(self):
        for style in (0x80000 | 0x20, 0x20):
            backend, window = self.backend([7, 42])
            backend.gui.GetWindowLong = lambda hwnd, index: style
            backend.gui.GetLayeredWindowAttributes = lambda hwnd: (_ for _ in ()).throw(OSError("per pixel"))
            with self.assertRaises(WeChatError) as caught:
                backend._check(window, [180, 0, 600, 500])
            self.assertEqual(caught.exception.diagnostic["blockers"][0]["ex_style"], style)
            self.assertIsNone(caught.exception.diagnostic["blockers"][0]["layered_attributes"])

    def test_verified_codex_cursor_overlay_combination_is_allowed(self):
        backend, window = self.backend([7, 42])
        backend.gui.GetWindowLong = lambda hwnd, index: 134742184
        backend.gui.GetLayeredWindowAttributes = lambda hwnd: (0, 255, 3)
        backend.gui.GetClassName = lambda hwnd: "CodexComputerUseCursorOverlay"
        backend._exe = lambda hwnd: (123, "C:/fixture/codex-computer-use.exe")
        backend._check(window, [180, 0, 600, 500])

    def test_cursor_exception_rejects_other_identities_and_missing_properties(self):
        cases = [
            ("AnotherOverlay", "codex-computer-use.exe", 134742184, (0, 255, 3)),
            ("CodexComputerUseCursorOverlay", "another.exe", 134742184, (0, 255, 3)),
            ("CodexComputerUseCursorOverlay", "codex-computer-use.exe", 134742184 & ~0x20, (0, 255, 3)),
            ("CodexComputerUseCursorOverlay", "codex-computer-use.exe", 134742184 & ~0x08000000, (0, 255, 3)),
            ("CodexComputerUseCursorOverlay", "codex-computer-use.exe", 134742184, (1, 255, 3)),
            ("CodexComputerUseCursorOverlay", "codex-computer-use.exe", 134742184, (0, 255, 2)),
        ]
        for class_name, executable, style, attributes in cases:
            backend, window = self.backend([7, 42])
            backend.gui.GetWindowLong = lambda hwnd, index: style
            backend.gui.GetLayeredWindowAttributes = lambda hwnd: attributes
            backend.gui.GetClassName = lambda hwnd: class_name
            backend._exe = lambda hwnd: (123, "C:/fixture/" + executable)
            with self.assertRaises(WeChatError) as caught:
                backend._check(window, [180, 0, 600, 500])
            self.assertEqual(caught.exception.code, "window_obscured")
            self.assertEqual(caught.exception.diagnostic["blockers"][0]["exe"], executable)


class FocusActivationTests(unittest.TestCase):
    def fixture(self, activate_at):
        backend = Win32ScreenBackend.__new__(Win32ScreenBackend)
        backend._dpi = lambda: None
        clock = [0.0]
        requests = []
        backend.gui = SimpleNamespace(
            SetForegroundWindow=lambda hwnd: requests.append(hwnd),
            SetWindowPos=lambda hwnd,after,x,y,w,h,flags: self.assertEqual(
                (hwnd,after,x,y,w,h,flags),(42,0,0,0,0,0,0x0013)),
            GetForegroundWindow=lambda: 42 if clock[0] >= activate_at else 7,
            GetClassName=lambda hwnd: "WeixinClass" if hwnd == 42 else "ForegroundClass")
        backend._exe = lambda hwnd: (123, "C:/fixture/Weixin.exe" if hwnd == 42 else "C:/fixture/other.exe")
        return backend, {"hwnd": 42, "rect": [0, 0, 600, 500]}, clock, requests

    def test_delayed_activation_succeeds_after_one_focus_request(self):
        backend, window, clock, requests = self.fixture(0.15)
        with patch("galchat.visual_wechat.time.monotonic", side_effect=lambda: clock[0]), \
             patch("galchat.visual_wechat.time.sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            backend.focus(window)
        self.assertEqual(requests, [42])
        self.assertGreaterEqual(clock[0], 0.15)
        self.assertLessEqual(clock[0], 0.6)

    def test_already_foreground_does_not_request_activation_again(self):
        backend, window, _, requests = self.fixture(0)
        backend.gui.SetForegroundWindow = lambda hwnd: self.fail('already foreground')
        backend.focus(window)
        self.assertEqual(requests, [])

    def test_never_activated_times_out_with_private_metadata_only(self):
        backend, window, clock, requests = self.fixture(float("inf"))
        with patch("galchat.visual_wechat.time.monotonic", side_effect=lambda: clock[0]), \
             patch("galchat.visual_wechat.time.sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            with self.assertRaises(WeChatError) as caught:
                backend.focus(window)
        self.assertEqual(requests, [42])
        self.assertAlmostEqual(clock[0], 0.6)
        self.assertEqual(caught.exception.code, "focus_failed")
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic["target"], {"hwnd": 42, "class": "WeixinClass", "exe": "Weixin.exe"})
        self.assertEqual(diagnostic["foreground"], {"hwnd": 7, "class": "ForegroundClass", "exe": "other.exe"})
        self.assertNotIn("title", str(diagnostic))


if __name__ == "__main__":
    unittest.main()
