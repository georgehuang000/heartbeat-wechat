from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from galchat.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path / "history.sqlite3")
    yield memory
    memory.close()


def test_history_is_scoped_to_contact_and_choices_are_not_sent_messages(store):
    store.import_text("a", "小夏", "我：哈哈，周末去吃火锅？\n对方：好呀，我喜欢番茄锅")
    store.import_text("b", "小秋", "我：另一个人的秘密\n对方：别混进其它联系人")
    store.record_choice("a", "我明天就去接你", "试着约一下")
    context = store.get_context("a")
    assert context["counts"] == {"messages": 2, "self": 1, "other": 1, "choices": 1, "retrieval_candidates": 0}
    assert len(context["recent"]) == 2
    assert "另一个人" not in json.dumps(context, ensure_ascii=False)
    assert "我明天就去接你" not in json.dumps(context, ensure_ascii=False)
    assert context["style_summary"]["preferred_branches"] == [{"label": "试着约一下", "count": 1}]
    assert {c["id"]: c["message_count"] for c in store.list_contacts()} == {"a": 2, "b": 2}


def test_reimport_deduplicates_but_keeps_repeated_messages(store):
    text = "我：哈哈\n对方：嗯\n我：哈哈"
    assert store.import_text("a", "小夏", text) == 3
    assert store.import_text("a", "小夏", text) == 0
    assert [m["text"] for m in store.get_context("a")["recent"]] == ["哈哈", "嗯", "哈哈"]


def test_upsert_corrects_message_without_counting_it_twice(store):
    assert store.upsert_messages("a", "旧名字", [{"id": "1", "role": "self", "text": "误识别"}]) == 1
    assert store.upsert_messages("a", "新名字", [{"id": "1", "role": "other", "text": "已修正"}]) == 0
    context = store.get_context("a")
    assert context["contact_name"] == "新名字"
    assert context["recent"][0]["role"] == "other"
    assert context["recent"][0]["text"] == "已修正"


def test_backfilled_old_messages_stay_in_old_context_and_can_be_retrieved(store):
    store.upsert_messages("a", "小夏", [
        {"id": "recent", "role": "other", "text": "周末想吃火锅", "timestamp": "2026-10-07T12:00:00+08:00"},
    ])
    store.upsert_messages("a", "小夏", [
        {"id": "old", "role": "other", "text": "那家番茄火锅我一直想试试", "timestamp": "2026-09-01T12:00:00+08:00", "source": "import-json"},
        {"id": "unrelated", "role": "self", "text": "工作报告已经写好了", "timestamp": "2026-09-02T12:00:00+08:00"},
    ])
    context = store.get_context("a", query="吃火锅", limit=1)
    assert [m["id"] for m in context["recent"]] == ["recent"]
    assert [m["id"] for m in context["relevant"]] == ["old"]
    assert context["relevant"][0]["source"] == "import-json"
    assert context["counts"]["messages"] == 3


def test_invalid_import_is_atomic_and_has_useful_line_error(store):
    with pytest.raises(ValueError, match="第 2 行"):
        store.import_text("a", "小夏", "我：你好\n未知发言人：这条不能默认归给我")
    assert store.list_contacts() == []
    with pytest.raises(ValueError, match="role"):
        store.upsert_messages("a", "小夏", [{"role": "self", "text": "合法"}, {"role": "system", "text": "非法"}])
    assert store.list_contacts() == []


def test_json_import_and_multiline_text(store):
    payload = [{"role": "other", "text": "周末去哪里", "timestamp": "2026-10-01"}]
    assert store.import_text("a", "小夏", json.dumps(payload)) == 1
    assert store.import_text("a", "小夏", "我：先吃饭\n  再去散步") == 1
    context = store.get_context("a")
    assert context["recent"][-1]["text"] == "先吃饭\n再去散步"
    assert context["recent"][0]["source"] == "import-json"


def test_style_uses_only_self_messages_of_current_contact(store):
    store.import_text("a", "小夏", "我：哈哈好呀🙂\n对方：救命救命救命\n我：哈哈🙂")
    store.import_text("b", "小秋", "我：笑死笑死")
    style = store.get_context("a")["style_summary"]
    assert style["self_sample_count"] == 2
    assert "哈哈" in style["common_phrases"]
    assert "救命" not in style["common_phrases"]
    assert "笑死" not in style["common_phrases"]
    assert "🙂" in style["emoji_examples"]


def test_memory_persists_and_supports_worker_threads(tmp_path):
    path = tmp_path / "threaded.sqlite3"
    memory = MemoryStore(path)
    def add(index):
        memory.upsert_messages("a", "小夏", [{"id": str(index), "role": "other", "text": f"消息 {index}"}])
        return memory.get_context("a")["counts"]["messages"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(add, range(15)))
    memory.close()
    with MemoryStore(path) as reopened:
        assert reopened.get_context("a")["counts"]["messages"] == 15


def test_unknown_contact_returns_empty_context(store):
    context = store.get_context("unknown")
    assert context["recent"] == []
    assert context["counts"]["messages"] == 0

