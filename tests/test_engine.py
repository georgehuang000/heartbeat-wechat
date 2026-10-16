import json

import httpx
import pytest

from galchat.engine import ReplyEngine, ReplyEngineError


CONFIG = {"base_url": "https://api.example.test/v1", "model": "example-chat", "provider_extra": {"enable_thinking": False}}
CONTEXT = {
    "contact_id": "a", "contact_name": "小夏",
    "recent": [{"id": "r1", "role": "other", "text": "周末又要躺两天了", "timestamp": "2026-10-07"}],
    "relevant": [{"id": "r0", "role": "other", "text": "想吃那家番茄火锅", "source": "import-text"}],
    "style_summary": {"common_phrases": ["哈哈"]},
}
VALID = {"choices": [
    {"label": "一起摆烂", "text": "同款计划，咱俩主打一个节能。", "hint": "接住她的玩笑", "tone": "playful"},
    {"label": "接着聊", "text": "准备配什么下饭剧？我最近正缺一部。", "hint": "从周末安排找话题", "tone": "curious"},
    {"label": "试着约", "text": "要不要抽半天去试试那家火锅？", "hint": "接上之前提过的店", "tone": "bold"},
]}


def completion(content):
    return httpx.Response(200, json={"model": "example-chat", "choices": [{"message": {"content": content}}]})


def test_generation_transmits_scoped_context_and_returns_three_model_choices():
    requests = []
    def handler(request):
        requests.append(request)
        return completion(json.dumps(VALID, ensure_ascii=False))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ReplyEngine(client).generate(CONTEXT, CONFIG, intent="轻松一点")
    assert result["source"] == "model"
    assert len(result["choices"]) == 3
    assert [c["id"] for c in result["choices"]] == ["choice-1", "choice-2", "choice-3"]
    body = json.loads(requests[0].content)
    assert str(requests[0].url).endswith("/v1/chat/completions")
    assert body["enable_thinking"] is False
    assert "chat_template_kwargs" not in body
    data = json.loads(body["messages"][1]["content"])
    assert data["context"]["relevant"][0]["text"] == "想吃那家番茄火锅"
    assert data["intent"] == "轻松一点"
    assert "api_key" not in data


def test_markdown_json_fence_is_accepted():
    with httpx.Client(transport=httpx.MockTransport(lambda request: completion("```json\n" + json.dumps(VALID) + "\n```"))) as client:
        assert len(ReplyEngine(client).generate(CONTEXT, CONFIG)["choices"]) == 3


def test_emoji_placeholder_is_explicitly_unknown_in_api_context():
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return completion(json.dumps(VALID))
    context={**CONTEXT,'recent':CONTEXT['recent']+[
        {'role':'other','kind':'emoji','text':'[表情]'}]}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(context,CONFIG)
    payload=json.loads(requests[0]['messages'][1]['content'])['context']['recent']
    assert payload[-1]['kind']=='emoji' and payload[-1]['content_known'] is False
    assert payload[-2]['content_known'] is True
    assert '不能把它理解成赞同' in requests[0]['messages'][0]['content']


def test_full_hundred_turns_include_both_speakers_in_chronological_order():
    rows=[{'id':str(i),'role':'self' if i%2 else 'other','text':f'消息{i}',
           'kind':'text','contact_id':'a'} for i in range(100)]
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return completion(json.dumps(VALID))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate({**CONTEXT,'recent':rows},CONFIG)
    recent=json.loads(requests[0]['messages'][1]['content'])['context']['recent']
    assert [m['text'] for m in recent]==[m['text'] for m in rows]
    assert sum(m['role']=='self' for m in recent)==50
    assert sum(m['role']=='other' for m in recent)==50


def test_malformed_output_gets_one_informative_repair():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return completion("不是 JSON" if len(requests) == 1 else json.dumps(VALID))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ReplyEngine(client).generate(CONTEXT, CONFIG)["source"] == "model"
    assert len(requests) == 2
    assert "校验未通过" in requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize("bad", [
    {"choices": VALID["choices"][:2]},
    {"choices": [VALID["choices"][0]] * 3},
    {"choices": [{**VALID["choices"][0], "text": "长" * 100}, *VALID["choices"][1:]]},
    {"choices": [{**VALID["choices"][0], "tone": "unsupported"}, *VALID["choices"][1:]]},
])
def test_invalid_choices_never_get_silently_replaced_by_fake_options(bad):
    calls = []
    def handler(request):
        calls.append(request)
        return completion(json.dumps(bad))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError, match="已修正一次"):
            ReplyEngine(client).generate(CONTEXT, CONFIG)
    assert len(calls) == 2


def test_regeneration_rejects_same_text_even_if_punctuation_changes():
    previous = [VALID["choices"][0]["text"].replace("。", "！")]
    with httpx.Client(transport=httpx.MockTransport(lambda request: completion(json.dumps(VALID)))) as client:
        with pytest.raises(ReplyEngineError, match="上一组"):
            ReplyEngine(client).generate(CONTEXT, CONFIG, previous=previous)


def test_http_error_does_not_leak_api_key_or_server_body_and_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "secret-key-and-private-message"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError, match="HTTP 401") as exc:
            ReplyEngine(client).generate(CONTEXT, {**CONFIG, "api_key": "secret-key"})
    assert "secret-key" not in str(exc.value)
    assert len(calls) == 1


def test_timeout_reports_loading_advice_without_fake_result():
    def handler(request):
        raise httpx.ReadTimeout("sensitive details", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError, match="超时"):
            ReplyEngine(client).generate(CONTEXT, CONFIG)


def test_cross_contact_context_is_rejected_before_network():
    def handler(request):
        pytest.fail("must not send cross-contact context")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError, match="其它联系人"):
            ReplyEngine(client).generate({**CONTEXT, "relevant": [{"contact_id": "b", "role": "other", "text": "secret"}]}, CONFIG)


def test_empty_history_is_rejected_before_network():
    with pytest.raises(ReplyEngineError, match="聊天记录"):
        ReplyEngine().generate({"recent": []}, CONFIG)


def test_probe_only_requests_model_metadata():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert not request.content
        return httpx.Response(200, json={"data": [{"id": "example-chat"}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ReplyEngine(client).probe(CONFIG)
    assert result["ok"] and result["model_listed"]


def test_generic_cloud_payload_does_not_send_llama_specific_fields():
    def handler(request):
        body = json.loads(request.content)
        assert "chat_template_kwargs" not in body
        assert request.headers["Authorization"] == "Bearer example-placeholder"
        return completion(json.dumps(VALID))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        config = {"base_url": "https://example.test/v1", "model": "example", "api_key": "example-placeholder"}
        ReplyEngine(client).generate(CONTEXT, config)


def test_missing_endpoint_fails_without_assuming_local_model():
    with pytest.raises(ReplyEngineError, match="API 地址"):
        ReplyEngine().generate(CONTEXT, {"model": "example-chat"})


def test_legacy_thinking_config_does_not_inject_provider_specific_fields():
    def handler(request):
        body = json.loads(request.content)
        assert "chat_template_kwargs" not in body
        assert "enable_thinking" not in body
        return completion(json.dumps(VALID))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(CONTEXT, {"base_url": "https://example.test/v1", "model": "example",
                                              "enable_thinking": False, "disable_thinking": True})


def test_provider_extra_cannot_replace_scoped_history():
    with pytest.raises(ReplyEngineError, match="不能覆盖"):
        ReplyEngine().generate(CONTEXT, {**CONFIG, "provider_extra": {"messages": []}})


@pytest.mark.parametrize("status,description", [(402, "余额不足"), (503, "API 服务繁忙")])
def test_api_status_errors_are_actionable_and_not_retried(status, description):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "server response may contain private details"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError, match=description) as exc:
            ReplyEngine(client).generate(CONTEXT, CONFIG)
    assert len(calls) == 1
    assert "private details" not in str(exc.value)
    assert exc.value.code == ('insufficient_balance' if status == 402 else 'service_unavailable')


def test_billing_failure_during_fact_review_does_not_regenerate_a_paid_candidate():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return completion(json.dumps(VALID, ensure_ascii=False))
        return httpx.Response(402, json={'error': 'private server detail'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError) as error:
            ReplyEngine(client).generate(CONTEXT, {**CONFIG, 'verify_replies': True})
    assert error.value.code == 'insufficient_balance'
    assert len(calls) == 2
    assert 'private server detail' not in str(error.value)


def test_network_failure_describes_api_connection():
    def handler(request):
        raise httpx.ConnectError("sensitive network details", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplyEngineError, match="无法连接 API") as exc:
            ReplyEngine(client).generate(CONTEXT, CONFIG)
    assert "启动" not in str(exc.value)


def test_deepseek_non_thinking_configuration_and_root_endpoint_normalization():
    def handler(request):
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert "chat_template_kwargs" not in body
        return completion(json.dumps(VALID))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate(CONTEXT, {
            "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
            "provider_extra": {"thinking": {"type": "disabled"}},
        })

