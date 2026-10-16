from contact_fixtures import apply_enabled_snapshot
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from galchat.app import create_server
from galchat.controller import Controller


@pytest.fixture
def controller(tmp_path):
    app = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)
    app._require_api_config = lambda config: None
    app.state["settings"]["api_ready"] = True
    yield app
    app.close()


def snapshot(name="A", text="周末想出去玩", mid="message1"):
    return {"contact": {"id": name, "name": name}, "messages": [
        {"id": mid, "role": "other", "text": text, "timestamp": None, "source": "fixture"}]}


CHOICES = [{"id": f"choice-{i}", "text": f"选项{i}", "label": f"方向{i}", "hint": "提示", "tone": "curious"} for i in range(3)]


def wait_finished(controller):
    deadline = time.monotonic() + 3
    while controller.generating and time.monotonic() < deadline:
        time.sleep(.01)
    assert not controller.generating


def test_late_model_result_cannot_cross_contact(controller):
    entered, release = threading.Event(), threading.Event()

    class SlowEngine:
        def generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return {"choices": CHOICES, "source": "model", "model": "fixture", "elapsed_ms": 1}

    controller.engine = SlowEngine()
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot())
    controller.dispatch("generate")
    assert entered.wait(2)
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot("B", "晚饭吃了没", "b1"))
    release.set()
    wait_finished(controller)
    state = controller.get_state()
    assert state["contact"]["id"] == "B"
    assert state["choices"] == []


def test_history_does_not_become_the_latest_reply_target(controller):
    seen = []

    class Engine:
        def generate(self, context, *args, **kwargs):
            seen.append(context)
            return {"choices": CHOICES, "source": "model", "model": "fixture", "elapsed_ms": 1}

    controller.engine = Engine()
    latest = snapshot()["messages"]
    old = {"id": "old", "role": "other", "text": "去年说过的事", "timestamp": None, "source": "fixture"}
    with controller.lock:
        apply_enabled_snapshot(controller, {"contact": {"id": "A", "name": "A"},
                                    "messages": [old] + latest, "latest_messages": latest})
    controller.dispatch("generate")
    wait_finished(controller)
    assert seen[0]["recent"] == latest
    assert controller.get_state()["history"]["count"] == 2


def test_opening_chat_after_own_reply_generates_once_with_full_turn(controller):
    seen=[]
    class Engine:
        def generate(self,context,*args,**kwargs):
            seen.append(context)
            return {"choices":CHOICES,"source":"model","model":"fixture","elapsed_ms":1}
    controller.engine=Engine()
    current=snapshot()
    current['messages'].append({'id':'own-1','role':'self','text':'好呀，那我们周末再商量',
                                'timestamp':None,'source':'fixture'})
    with controller.lock:
        apply_enabled_snapshot(controller, current)
    assert controller.pending_at is not None
    controller.pending_at=0
    controller._maybe_generate()
    wait_finished(controller)
    assert [m['role'] for m in seen[0]['recent']]==['other','self']
    assert len(controller.get_state()['choices'])==3
    with controller.lock:
        apply_enabled_snapshot(controller, current)
    controller._maybe_generate()
    assert len(seen)==1  # Identical background polls must not spend another API call.


@pytest.mark.parametrize('latest',[{'role':'unknown','kind':'text'}, {'role':'self','kind':'image'}, {'role':'unknown','kind':'emoji'}])
def test_unclear_or_media_latest_still_does_not_auto_generate(controller,latest):
    current=snapshot()
    current['messages'][0].update(latest)
    if latest['role']=='unknown':
        current['generation_blocked_reason']='发送方未确定'
    with controller.lock:
        apply_enabled_snapshot(controller, current)
    assert controller.pending_at is None
    assert controller.get_state()['choices']==[]


@pytest.mark.parametrize('role',['self','other'])
def test_known_emoji_auto_generates_with_preserved_context(controller,role):
    seen=[]
    class Engine:
        def generate(self,context,*args,**kwargs):
            seen.append(context)
            return {'choices':CHOICES,'source':'model','model':'fixture','elapsed_ms':1}
    controller.engine=Engine()
    current=snapshot()
    current['messages'].append({'id':'emoji-1','role':role,'kind':'emoji','text':'[表情]',
                                'timestamp':None,'source':'fixture'})
    apply_enabled_snapshot(controller, current)
    assert controller.pending_at is not None
    controller.pending_at=0
    controller._maybe_generate()
    wait_finished(controller)
    assert seen[0]['recent']==current['messages']
    assert len(controller.get_state()['choices'])==3


def test_import_rejects_another_contacts_name(controller):
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot())
    result = controller.dispatch("import_history", {"contact_name": "B", "text": "对方: 不该串进来"})
    assert not result["ok"]
    assert controller.memory.list_contacts()[0]["message_count"] == 1


def test_new_message_invalidates_selected_draft(controller):
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot())
        controller.state["choices"] = CHOICES
        controller.choice_revision = controller.revision
    assert controller.dispatch("choose", {"id": "choice-0"})["ok"]
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot(text="算了今天不出去了", mid="new"))
    with pytest.raises(ValueError, match="重新选择"):
        controller._handle_native("fill_draft", {"text": "还想约你"})


def test_settings_clear_selected_draft(controller):
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot())
        controller.state["draft"] = "旧台词"
        controller.selected_revision = controller.revision
    assert controller.dispatch("save_settings", {"emoji_mode": "off"})["ok"]
    assert controller.get_state()["draft"] == ""
    assert controller.selected_revision == -1


def test_browser_api_requires_per_run_token(controller):
    client = TestClient(create_server(controller, controller.root, "test-session-secret"))
    assert client.post("/api/get_state", json={}).status_code == 403
    response = client.post("/api/get_state", json={}, headers={"X-Galchat-Token": "test-session-secret"})
    assert response.status_code == 200
    assert response.json()["mode"] == "wechat"


def test_missing_key_never_calls_api_or_starts_generation(tmp_path):
    app = Controller(Path(__file__).resolve().parents[1], tmp_path, start_worker=False)

    class UnexpectedAPI:
        def generate(self, *args, **kwargs):
            pytest.fail("Unconfigured UI must not call the API")

        def probe(self, *args, **kwargs):
            pytest.fail("Connection test needs a key before making requests")

    app.engine = UnexpectedAPI()
    try:
        assert app.get_state()["generation"]["status"] == "unconfigured"
        state = app.dispatch("demo")["state"]
        assert state["generation"]["source"] == "demo"
        assert not app.generating
        assert len(state["choices"]) == 3
        assert "API Key" in app.dispatch("generate")["error"]
        assert "API Key" in app.dispatch("test_model")["error"]
    finally:
        app.close()


def test_saving_settings_does_not_send_chat_to_api(controller):
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot())
    assert controller.pending_at is not None
    assert controller.dispatch("save_settings", {"follow_window": False})["ok"]
    assert controller.pending_at is None
    assert not controller.generating


def test_repeated_generate_while_busy_cannot_queue_extra_api_call(controller):
    entered, release = threading.Event(), threading.Event()
    requests = []

    class SlowEngine:
        def generate(self, *args, **kwargs):
            requests.append(True)
            entered.set()
            assert release.wait(3)
            return {"choices": CHOICES, "source": "model", "model": "fixture", "elapsed_ms": 1}

    controller.engine = SlowEngine()
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot())
    assert controller.dispatch("generate")["ok"]
    assert entered.wait(2)
    result = controller.dispatch("generate")
    assert not result["ok"]
    assert "正在生成" in result["error"]
    release.set()
    wait_finished(controller)
    controller._maybe_generate()
    wait_finished(controller)
    assert len(requests) == 1


def test_unreadable_latest_message_cannot_reply_to_older_text(controller):
    seen = snapshot()
    seen["needs_transcript"] = True
    with controller.lock:
        apply_enabled_snapshot(controller, seen)
    assert controller.pending_at is None
    assert "正文" in controller.dispatch("generate")["error"]
    controller.pending_at = time.monotonic()
    controller._maybe_generate()
    assert not controller.generating


def test_connection_loss_invalidates_in_flight_reply_and_selected_draft(controller):
    entered, release = threading.Event(), threading.Event()

    class SlowEngine:
        def generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return {"choices": CHOICES, "source": "model", "model": "fixture", "elapsed_ms": 1}

    controller.engine = SlowEngine()
    with controller.lock:
        apply_enabled_snapshot(controller, snapshot())
        controller.connected = True
        controller.state["connection"] = {"can_read": True, "can_fill": True}
        controller.state["draft"] = "已选择但尚未发送"
        controller.selected_revision = controller.revision
    assert controller.dispatch("generate")["ok"]
    assert entered.wait(2)
    with controller.lock:
        controller._connection_error("窗口已切换")
    release.set()
    wait_finished(controller)
    assert controller.get_state()["choices"] == []
    assert controller.get_state()["draft_stale"]
    assert not controller.get_state()["can_fill_draft"]
    assert not controller.dispatch("generate")["ok"]


def test_visual_preview_is_not_archived_before_name_confirmation(controller):
    class Visual:
        def get_window_rect(self):
            return None

        def preview(self):
            return {"contact_name": "读错的名字", "messages": snapshot()["messages"]}

        def bind_contact(self, name):
            return snapshot(name)

        def status(self):
            return {"status": "connected", "can_read": True, "can_fill": True}

    controller.visual_adapter = Visual()
    controller._handle_native("visual_preview", {})
    assert controller.memory.list_contacts() == []
    controller.contact_preferences.save('确认的名字', {'assistance_enabled': True})
    controller._handle_native("visual_bind", {"contact_name": "确认的名字"})
    assert controller.get_state()["contact"]["name"] == "确认的名字"
    assert controller.memory.list_contacts()[0]["message_count"] == 1


def test_loading_history_refreshes_count_even_when_latest_is_unchanged(controller):
    current = snapshot()
    old = snapshot(text="更早的约定", mid="older")["messages"]

    class Adapter:
        def load_history(self, pages):
            return {**current, "messages": old + current["messages"],
                    "latest_messages": current["messages"], "coverage": "已翻页并恢复"}

    controller.adapter = Adapter()
    controller.connected = True
    with controller.lock:
        apply_enabled_snapshot(controller, current)
    controller._handle_native("load_history", {"pages": 1})
    assert controller.get_state()["history"]["count"] == 2
    assert controller.get_state()["messages"] == current["messages"]
