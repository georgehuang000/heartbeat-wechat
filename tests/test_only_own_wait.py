"""An own archive must not turn into a fictional peer/image response."""
import json
import httpx
import pytest

from galchat.engine import ReplyEngine


CONFIG = {'base_url': 'https://api.test', 'model': 'test', 'verify_replies': True}


@pytest.mark.parametrize('kind', ['text', 'image', 'voice'])
def test_own_archive_waits_without_model_or_fake_closing(kind):
    context = {'recent': [
        {'id': 'old', 'role': 'self', 'kind': kind, 'text': '[自己的附件]',
         'timestamp': '2026-10-11T10:00:00', 'content_known': False},
        {'id': 'new', 'role': 'self', 'kind': 'text', 'text': '记录一下新的实现想法',
         'timestamp': '2026-10-14T10:00:00'},
    ]}
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('must not call model'))) as client:
        result = ReplyEngine(client).generate(context, CONFIG)
    assert result['choices'] == [] and result['conversation_state'] == 'wait'
    assert result['review']['reason'] == 'only_own_messages_in_context'
    assert not result.get('retained_closing')  # An own note is not a sent farewell.


@pytest.mark.parametrize('variant', ['explicit_new_topic', 'peer_present'])
def test_explicit_direction_or_actual_peer_still_reaches_model(variant):
    calls = []
    context = {'recent': [{'id': 'own', 'role': 'self', 'kind': 'text', 'text': '记录新的实现想法'}]}
    intent = '想补充一下这个想法' if variant == 'explicit_new_topic' else ''
    if variant == 'peer_present':
        context['recent'].append({'id': 'peer', 'role': 'other', 'kind': 'text', 'text': '你想怎么做？'})
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'conversation_state': 'reply', 'choices': [
                {'label': '说明方向', 'text': '先把流程理清，再做一个小样。', 'hint': '说清下一步', 'tone': 'calm'},
                {'label': '讨论范围', 'text': '可以先确定这一轮需要解决什么。', 'hint': '先确定范围', 'tone': 'curious'},
                {'label': '先做对照', 'text': '要不先拿两种做法比一下？', 'hint': '明确提议', 'tone': 'warm'},
            ]})}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ReplyEngine(client).generate(context, {**CONFIG, 'verify_replies': False}, intent=intent)
    assert len(calls) == 1 and len(result['choices']) == 3
