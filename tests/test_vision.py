import base64
from copy import deepcopy
import json

import httpx
import pytest

from galchat.engine import ReplyEngineError
from galchat.vision import MAX_IMAGE_BYTES, VisionReader, VisionReaderError


# Synthetic one-pixel PNG, never a user screenshot.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
CONFIG = {"base_url": "https://api.example.test", "model": "deepseek-flash",
          "api_key": "test-placeholder", "provider_extra": {"thinking": {"type": "disabled"}}}
VALID = {
    "contact_name": "测试联系人",
    "messages": [
        {"role": "system", "text": "昨天 18:29", "kind": "other", "truncated": False},
        {"role": "other", "text": "周末准备做什么？", "kind": "text", "truncated": False},
        {"role": "self", "text": "暂时还没想好", "kind": "text", "truncated": False},
    ],
    "input_text": "准备去吃饭", "confidence": 0.91, "warnings": [],
}


def completion(data, finish_reason="stop"):
    content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})


def read_result(data):
    with httpx.Client(transport=httpx.MockTransport(lambda request: completion(data))) as client:
        return VisionReader(client).read(PNG, CONFIG)


@pytest.mark.parametrize('draft', ['', '   ', '回复 |\n第二行[强]'])
def test_input_request_transcribes_only_crop_and_preserves_whitespace(draft):
    def handler(request):
        body = json.loads(request.content)
        assert body['temperature'] == 0
        assert body['thinking'] == {'type': 'disabled'}
        assert '输入框' in body['messages'][0]['content']
        image = body['messages'][1]['content'][0]
        assert image['image_url']['url'] == 'data:image/png;base64,' + base64.b64encode(PNG).decode()
        return completion({'input_text': draft})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert VisionReader(client).read_input(PNG, CONFIG) == {'input_text': draft}


@pytest.mark.parametrize('value,finish', [({'input_text': '[看不清]'}, 'stop'),
    ({'input_text': None}, 'stop'), ({'input_text': 'x', 'messages': []}, 'stop'),
    ([], 'stop'), ({'input_text': 'x'}, 'length')])
def test_input_rejects_uncertain_or_truncated_result(value, finish):
    with httpx.Client(transport=httpx.MockTransport(lambda request: completion(value, finish))) as client:
        with pytest.raises(VisionReaderError):
            VisionReader(client).read_input(PNG, CONFIG)


def test_title_request_contains_only_provided_crop():
    def handler(request):
        body=json.loads(request.content)
        assert body['max_tokens']==256 and body['temperature']==0
        assert body['thinking']=={'type':'disabled'}
        content=body['messages'][1]['content']
        assert len(content)==1
        assert content[0]['image_url']['url']=='data:image/png;base64,'+base64.b64encode(PNG).decode()
        return completion({'contact_name':'唯一备注'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert VisionReader(client).read_title(PNG,CONFIG)=={'contact_name':'唯一备注'}


@pytest.mark.parametrize('data,finish',[({'contact_name':''},'stop'),({'contact_name':'unknown'},'stop'),
    ({'contact_name':'A','messages':[]},'stop'),({'contact_name':'A'},'length'),([], 'stop')])
def test_title_rejects_uncertain_or_unexpected_responses(data,finish):
    with httpx.Client(transport=httpx.MockTransport(lambda request:completion(data,finish))) as client:
        with pytest.raises(VisionReaderError,match='标题'):
            VisionReader(client).read_title(PNG,CONFIG)


def test_sends_only_given_image_and_extracts_reviewable_fields():
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.example.test/v1/chat/completions"
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        image = body["messages"][1]["content"][1]
        assert image["type"] == "image_url" and image["image_url"]["detail"] == "high"
        assert image["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
        assert "test-placeholder" not in json.dumps(body)
        return completion(VALID)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = VisionReader(client).read(PNG, CONFIG)
    assert len(calls) == 1
    assert result["contact_name"] == "测试联系人"
    assert result["messages"] == VALID["messages"]
    assert result["input_text"] == "准备去吃饭"
    assert all("timestamp" not in message for message in result["messages"])
    assert any("尚未自动存档" in warning for warning in result["warnings"])


def test_preserves_unknown_sender_cropped_text_and_draft_separately():
    data = deepcopy(VALID)
    data["messages"].append({"role": "unknown", "text": "我刚才[看不清]", "kind": "text", "truncated": True})
    result = read_result(data)
    assert result["messages"][-1] == data["messages"][-1]
    assert result["input_text"] not in [item["text"] for item in result["messages"]]
    assert any("unknown" in warning for warning in result["warnings"])
    assert any("裁切" in warning for warning in result["warnings"])


@pytest.mark.parametrize("kind,placeholder", [("image", "[图片]"), ("emoji", "[表情]"), ("voice", "[语音]")])
def test_does_not_turn_guessed_media_contents_into_transcribed_text(kind, placeholder):
    data = deepcopy(VALID)
    data["messages"].append({"role": "other", "text": "模型猜想的媒体内容", "kind": kind, "truncated": False})
    result = read_result(data)
    assert result["messages"][-1]["text"] == placeholder
    assert "模型猜想的媒体内容" not in json.dumps(result, ensure_ascii=False)
    assert any("未采用模型" in warning for warning in result["warnings"])


def test_all_media_page_preserves_presence_without_claiming_content_is_understood():
    messages = [
        {"role": "other", "text": "[语音]", "kind": "voice", "truncated": False},
        {"role": "self", "text": "[图片]", "kind": "image", "truncated": False},
        {"role": "other", "text": "[表情]", "kind": "emoji", "truncated": False},
    ]
    result = read_result({**VALID, "messages": messages})
    assert result["messages"] == messages
    assert any("尚未理解" in warning and "不能据此续写" in warning for warning in result["warnings"])


@pytest.mark.parametrize("kind,text", [("image", "[图片]"), ("emoji", "[表情]"),
                                       ("voice", "[语音]"), ("file", "行程.pdf")])
@pytest.mark.parametrize("role,truncated", [("unknown", False), ("other", True)])
def test_all_media_page_still_rejects_unknown_senders_or_cropped_messages(kind, text, role, truncated):
    with pytest.raises(VisionReaderError, match="完整可辨"):
        read_result({**VALID, "messages": [{"role": role, "text": text, "kind": kind, "truncated": truncated}]})


def test_clear_file_name_can_be_reviewed_without_text_conversation():
    message = {"role": "other", "text": "行程.pdf", "kind": "file", "truncated": False}
    result = read_result({**VALID, "messages": [message]})
    assert result["messages"] == [message]
    assert any("不能据此续写" in warning for warning in result["warnings"])


def test_visible_wechat_voice_transcription_is_a_separate_text_message():
    messages = [
        {"role": "other", "text": "[语音]", "kind": "voice", "truncated": False},
        {"role": "other", "text": "我们下午三点在公园门口见。", "kind": "text", "truncated": False},
    ]
    result = read_result({**VALID, "messages": messages})
    assert result["messages"] == messages
    assert result["messages"][0]["text"] == "[语音]"
    assert result["messages"][-1]["kind"] == "text"
    assert result["messages"][-1]["role"] == result["messages"][0]["role"]
    assert not any("本页只识别到非文字" in warning for warning in result["warnings"])


def test_cropped_visible_transcription_retains_its_uncertainty_after_voice_placeholder():
    messages = [
        {"role": "self", "text": "[语音]", "kind": "voice", "truncated": False},
        {"role": "self", "text": "我下午[看不清]", "kind": "text", "truncated": True},
    ]
    result = read_result({**VALID, "messages": messages})
    assert result["messages"] == messages
    assert result["messages"][-1]["truncated"] is True
    assert any("裁切" in warning for warning in result["warnings"])


@pytest.mark.parametrize("contact", ["", "未知", "unknown", "[看不清]"])
def test_unknown_contact_fails_without_fabricated_identity(contact):
    with pytest.raises(VisionReaderError, match="联系人"):
        read_result({**VALID, "contact_name": contact})


@pytest.mark.parametrize("messages", [[],
    [{"role": "unknown", "text": "不明发言者", "kind": "text", "truncated": False}],
    [{"role": "other", "text": "只有半句话", "kind": "text", "truncated": True}],
    [{"role": "other", "text": "[看不清]", "kind": "text", "truncated": False}],
    [{"role": "system", "text": "昨天 18:29", "kind": "other", "truncated": False}],
])
def test_all_uncertain_or_non_conversational_messages_fail(messages):
    with pytest.raises(VisionReaderError, match="完整可辨"):
        read_result({**VALID, "messages": messages})


@pytest.mark.parametrize("patch", [{"confidence": True}, {"confidence": float("nan")},
    {"confidence": 1.5}, {"input_text": None}, {"warnings": "不是列表"}, {"messages": "不是列表"},
    {"extra": "不得多字段"}, {"contact_name": "x" * 161}])
def test_invalid_types_ranges_and_fields_are_rejected(patch):
    with pytest.raises(VisionReaderError):
        read_result({**VALID, **patch})


@pytest.mark.parametrize("patch", [{"role": "assistant"}, {"kind": "video"}, {"truncated": "false"},
    {"timestamp": "2026-10-08"}, {"text": ""}, {"text": "x" * 6001}])
def test_message_field_contract_is_strict(patch):
    data = deepcopy(VALID)
    data["messages"][1].update(patch)
    with pytest.raises(VisionReaderError):
        read_result(data)


def test_low_confidence_is_not_a_truth_gate_and_json_fence_is_accepted():
    result = read_result("```json\n" + json.dumps({**VALID, "confidence": 0.1}) + "\n```")
    assert result["confidence"] == 0.1
    assert any("模型自评" in warning for warning in result["warnings"])


def test_malformed_output_does_not_automatically_resend_image():
    calls = []
    def handler(request):
        calls.append(request)
        return completion("不是 JSON")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VisionReaderError, match="有效 JSON"):
            VisionReader(client).read(PNG, CONFIG)
    assert len(calls) == 1


def test_api_errors_reuse_sanitized_reply_error():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(402, json={"error": "test-placeholder private image"}))) as client:
        with pytest.raises(ReplyEngineError, match="余额不足") as exc:
            VisionReader(client).read(PNG, CONFIG)
    assert "private image" not in str(exc.value)
    assert "test-placeholder" not in str(exc.value)


def test_output_length_cutoff_is_clear_error():
    with httpx.Client(transport=httpx.MockTransport(lambda request: completion(VALID, "length"))) as client:
        with pytest.raises(VisionReaderError, match="长度限制截断"):
            VisionReader(client).read(PNG, CONFIG)


@pytest.mark.parametrize("image", [b"", b"not-an-image", "not bytes"])
def test_bad_input_fails_before_network(image):
    with pytest.raises(VisionReaderError):
        VisionReader().read(image, CONFIG)


def test_oversized_image_fails_before_network():
    with pytest.raises(VisionReaderError, match="20 MiB"):
        VisionReader().read(b"\x89PNG\r\n\x1a\n" + bytes(MAX_IMAGE_BYTES), CONFIG)


def test_provider_extra_cannot_override_screenshot_request():
    with pytest.raises(VisionReaderError, match="扩展参数"):
        VisionReader().read(PNG, {**CONFIG, "provider_extra": {"messages": []}})
