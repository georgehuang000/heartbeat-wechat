from datetime import datetime, timezone
import json
import httpx
import pytest
from galchat.timeline import build_timeline
from galchat.engine import ReplyEngine


def row(mid, time, text='正文', kind='text', role='other', **extra):
    return {'id': mid, 'timestamp': time, 'text': text, 'kind': kind, 'role': role, **extra}


def test_week_gap_is_candidate_not_automatic_chat_end():
    data = build_timeline([row('old', '2026-10-01T12:00:00'), row('new', '2026-10-08T12:00:00')])
    assert data['messages'][-1]['gap_prev_seconds'] == 7*86400
    assert data['candidate_boundaries'][0]['before_message_id'] == 'new'
    assert 'not_topic_decision' in data['candidate_boundaries'][0]['meaning']
    assert 'status' not in data and 'can_generate' not in data


def test_voice_is_not_removed_and_duration_does_not_shift_time():
    rows = [row('a', '2026-10-08T12:00:00'),
            row('voice', '2026-10-08T12:01:00', '[语音]', 'voice', duration_seconds=30),
            row('b', '2026-10-08T12:01:20')]
    data = build_timeline(rows)
    assert [m['message_id'] for m in data['messages']] == ['a', 'voice', 'b']
    assert [m['gap_prev_seconds'] for m in data['messages']] == [None, 60, 20]
    assert data['messages'][1]['duration_seconds'] == 30


@pytest.mark.parametrize('kind', ['image', 'emoji', 'card', 'call', 'video', 'file', 'forward'])
def test_other_types_preserve_temporal_position(kind):
    data = build_timeline([row('a', '2026-10-08T12:00:00'), row('b', '2026-10-08T12:00:20', kind=kind)])
    assert data['messages'][1]['kind'] == kind
    assert data['messages'][1]['gap_prev_seconds'] == 20


def test_forward_original_time_does_not_corrupt_outer_timeline():
    data = build_timeline([row('a', '2026-10-08T12:00:00'),
        row('f', '2026-10-08T12:00:20', kind='forward', attachment={'items': [
            {'timestamp': '2026-09-01T09:00:00', 'author': '第三人'}]}),
        row('b', '2026-10-08T12:00:40')])
    assert data['messages'][-1]['gap_prev_seconds'] == 20
    assert data['quoted_timelines'][0]['original_times'] == ['2026-09-01T09:00:00']
    assert '第三人' not in json.dumps(data, ensure_ascii=False)


def test_system_event_does_not_hide_week_between_people():
    data = build_timeline([row('a', '2026-10-01T12:00:00'),
        row('s', '2026-10-08T11:59:59', kind='system', role='system'),
        row('b', '2026-10-08T12:00:00')])
    assert data['messages'][-1]['gap_prev_seconds'] == 1
    assert data['messages'][-1]['gap_prev_human_seconds'] == 604800


@pytest.mark.parametrize('value', [None, '', '昨天', '2026-10-08', 'bad'])
def test_missing_or_date_only_is_not_fake_precise_time(value):
    data = build_timeline([row('a', '2026-10-01T12:00:00'), row('b', value)])
    assert data['messages'][-1]['gap_prev_seconds'] is None
    assert data['messages'][-1]['time_status'] == 'unknown'


def test_reverse_clock_and_equal_second_keep_order():
    data = build_timeline([row('a', '2026-10-08T12:00:10'), row('b', '2026-10-08T12:00:00'),
                           row('c', '2026-10-08T12:00:00')])
    assert [m['message_id'] for m in data['messages']] == ['a', 'b', 'c']
    assert data['messages'][1]['gap_prev_seconds'] is None
    assert data['messages'][1]['time_status'] == 'out_of_order'
    assert data['messages'][2]['gap_prev_seconds'] == 0


def test_timezone_equivalent_timestamps():
    data = build_timeline([row('a', '2026-10-08T12:00:00+08:00'), row('b', '2026-10-08T04:01:00Z')])
    assert data['messages'][1]['gap_prev_seconds'] == 60


def test_old_only_context_is_still_sent_to_model_with_no_end_rule():
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        choices = [{'label': f'方向{i}', 'text': f'可以试试第{i}种说法', 'hint': '提示', 'tone': 'calm'} for i in range(3)]
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'choices': choices})}}]})
    rows = [row('old', '2025-01-01T12:00:00', '那家店还想去吗？')]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ReplyEngine(client).generate({'recent': rows}, {'base_url': 'https://api.example.test', 'model': 'test'})
    assert len(result['choices']) == 3
    context = json.loads(captured[0]['messages'][1]['content'])['context']
    assert context['recent'][0]['text'] == rows[0]['text']
    assert context['timeline']['reply_target'] == 'old'


def test_payload_preserves_media_and_system_gap_without_sending_system_as_person():
    rows = [row('a', '2026-10-01T12:00:00'), row('s', '2026-10-08T11:59:59', role='system', kind='system'),
            row('v', '2026-10-08T12:00:00', '[语音]', 'voice'), row('b', '2026-10-08T12:00:20')]
    payload = ReplyEngine._payload_context({'recent': rows})
    assert [m['id'] for m in payload['recent']] == ['a', 'v', 'b']
    assert [m['message_id'] for m in payload['timeline']['messages']] == ['a', 's', 'v', 'b']
    assert payload['timeline']['messages'][2]['gap_prev_human_seconds'] == 604800
    assert not payload['recent'][1]['content_known']


def test_identical_idless_messages_are_distinct_positions():
    rows = [{'role': 'other', 'text': '哈哈', 'timestamp': '2026-10-08T12:00:00'}] * 2
    payload = ReplyEngine._payload_context({'recent': rows})
    assert [m['id'] for m in payload['recent']] == ['position-0', 'position-1']


def test_focus_keeps_old_background_available_for_cross_week_reference():
    rows = [row('invite', '2026-10-01T12:00:00', '去吃番茄锅？', role='self'),
            row('resume', '2026-10-08T12:00:00', '上次那家还想去吗？')]
    payload = ReplyEngine._payload_context({'recent': rows})
    assert payload['reply_focus']['target_message_id'] == 'resume'
    assert payload['reply_focus']['priority_message_ids'] == ['resume']
    assert payload['reply_focus']['background_message_ids'] == ['invite']
    assert payload['recent'][0]['role'] == 'self'
    assert payload['recent'][0]['text'] == '去吃番茄锅？'


def test_missing_time_does_not_hide_previous_context_from_focus():
    payload = ReplyEngine._payload_context({'recent': [row('a', None), row('b', None)]})
    assert payload['reply_focus']['priority_message_ids'] == ['a', 'b']
    assert payload['reply_focus']['background_message_ids'] == []


@pytest.mark.parametrize('attachment', [None, 'bad', {'items': None}, {'items': {}}])
def test_unreadable_forward_metadata_preserves_outer_timeline(attachment):
    data = build_timeline([row('f', '2026-10-08T12:00:00', kind='forward', attachment=attachment),
                           row('b', '2026-10-08T12:00:20')])
    assert data['quoted_timelines'][0]['original_times'] == []
    assert data['messages'][1]['gap_prev_seconds'] == 20
