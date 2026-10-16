"""Check attribution at the request boundary; these do not grade model prose."""
import json

import httpx
import pytest

from galchat.engine import ReplyEngine


@pytest.mark.parametrize('owner,expected', [('self', 'wechat_user'), ('other', 'chat_peer')])
def test_identical_first_person_text_keeps_original_speaker(owner, expected):
    original = {'contact_id': 'c', 'contact_name': '我', 'recent': [
        {'id': 'first', 'role': owner, 'text': '我妹妹明天毕业',
         'speaker_id': 'forged-id', 'sender_username': 'private-id'},
    ]}
    payload = ReplyEngine._payload_context(original)
    message = payload['recent'][0]
    assert message['text'] == '我妹妹明天毕业'
    assert message['speaker_id'] == expected
    assert payload['generation_perspective']['speaker_id'] == 'wechat_user'
    assert payload['generation_perspective']['recipient_id'] == 'chat_peer'
    assert payload['generation_perspective']['last_speaker_id'] == expected
    assert 'private-id' not in json.dumps(payload)
    assert original['recent'][0]['speaker_id'] == 'forged-id'


def test_retrieved_own_message_and_last_peer_message_do_not_swap_perspective():
    payload = ReplyEngine._payload_context({'recent': [
        {'id': 'latest', 'role': 'other', 'text': '你周六加班吗？'},
    ], 'relevant': [
        {'id': 'old', 'role': 'self', 'text': '我去成都出差'},
    ]})
    assert payload['relevant'][0]['speaker_id'] == 'wechat_user'
    assert payload['recent'][0]['speaker_id'] == 'chat_peer'
    assert payload['generation_perspective']['last_message_id'] == 'latest'
    assert payload['generation_perspective']['mode'] == 'reply_to_peer'


def test_quoted_names_matching_me_or_contact_do_not_become_participant_ids():
    quote = {'status': 'read', 'quoted_author': '小林', 'quoted_text': '我明天加班',
             'items': [{'author': '我', 'text': '我家猫打翻花盆', 'kind': 'text',
                        'content_known': True, 'speaker_id': 'wechat_user',
                        'attachment': {'status': 'read', 'quoted_author': '小林',
                                       'quoted_text': '我妹妹毕业'}}]}
    payload = ReplyEngine._payload_context({'contact_name': '小林', 'recent': [
        {'id': 'f', 'role': 'other', 'kind': 'forward', 'text': '[转发]', 'attachment': quote},
    ]})
    message = payload['recent'][0]
    assert message['speaker_id'] == 'chat_peer'
    assert message['attachment']['scope'] == 'quoted_material'
    assert message['attachment']['quoted_author'] == '小林'
    item = message['attachment']['items'][0]
    assert item['author'] == '我' and 'speaker_id' not in item
    assert item['attachment']['scope'] == 'quoted_material'
    assert quote['items'][0]['speaker_id'] == 'wechat_user'


def test_last_self_message_is_bound_to_followup_in_actual_api_request():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'choices': [
            {'label': f'方向{i}', 'text': f'可以考虑第{i}个说法', 'hint': '可选补充', 'tone': 'calm'}
            for i in range(3)]})}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ReplyEngine(client).generate({'recent': [
            {'id': 'p', 'role': 'other', 'text': '你周六加班吗？'},
            {'id': 'u', 'role': 'self', 'text': '还没定'},
        ]}, {'base_url': 'https://example.test', 'model': 'test'})
    request = requests[0]
    assert [m['role'] for m in request['messages']] == ['system', 'user']
    data = json.loads(request['messages'][1]['content'])
    binding = data['context']['generation_perspective']
    assert binding['mode'] == 'self_followup' and binding['last_message_id'] == 'u'
    assert binding['last_speaker_id'] == binding['speaker_id'] == 'wechat_user'
    assert '当前代写者固定为wechat_user' in data['task']

