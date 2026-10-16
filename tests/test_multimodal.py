from contact_fixtures import apply_enabled_snapshot
import io
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from galchat.controller import Controller
from galchat.engine import ReplyEngine, ReplyEngineError
from galchat.media import data_url, image_bytes

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {'base_url': 'https://example.test/v1', 'model': 'vision'}
CHOICES = {'choices': [
    {'label': '接住', 'text': '这配色挺有意思，你觉得呢？', 'hint': '讨论图像', 'tone': 'curious'},
    {'label': '轻松', 'text': '今日份颜色补给到了。', 'hint': '轻松接话', 'tone': 'playful'},
    {'label': '继续', 'text': '你最喜欢里面哪一块？', 'hint': '顺着聊', 'tone': 'warm'},
]}


def pixels(format='PNG'):
    out = io.BytesIO()
    Image.new('RGB', (40, 40), 'pink').save(out, format=format)
    return out.getvalue()


def context():
    return {'contact_id': 'A', 'recent': [
        {'id': 'own-image', 'role': 'self', 'text': '[图片]', 'kind': 'image',
         'timestamp': '2026-09-01T10:00:00', 'content_known': False},
        {'id': 'other-sticker', 'role': 'other', 'text': '[表情]', 'kind': 'emoji',
         'timestamp': '2026-10-12T10:00:00', 'content_known': False},
        {'id': 'target', 'role': 'other', 'text': '你觉得这个配色怎么样？',
         'timestamp': '2026-10-12T10:00:20'}],
        'visual_inputs': [
            {'contact_id': 'A', 'message_id': 'other-sticker', 'data_url': data_url(pixels('GIF')), 'tier': 'sticker'},
            {'contact_id': 'A', 'message_id': 'own-image', 'data_url': data_url(pixels()), 'tier': 'thumb'}]}


def generate(value, requests, response=None):
    def handler(request):
        requests.append(json.loads(request.content))
        text = response(len(requests)) if response else json.dumps(CHOICES)
        return httpx.Response(200, json={'choices': [{'message': {'content': text}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return ReplyEngine(client).generate(value, CONFIG)


def test_images_and_both_speakers_are_in_one_reply_request_with_original_times():
    requests = []
    original = context()
    assert len(generate(original, requests)['choices']) == 3
    assert len(requests) == 1
    parts = requests[0]['messages'][1]['content']
    payload = json.loads(parts[0]['text'])['context']
    assert payload['timeline']['reply_target'] == 'target'
    assert [m['role'] for m in payload['recent']] == ['self', 'other', 'other']
    assert payload['recent'][0]['timestamp'] == '2026-09-01T10:00:00'
    assert payload['recent'][0]['visual_input']['cache_tier'] == 'thumb'
    assert payload['recent'][0]['content_known'] is False  # Pixels aren't an invented text transcript.
    assert json.loads(parts[1]['text'])['chat_role'] == 'self'
    assert json.loads(parts[1]['text'])['speaker_id'] == 'wechat_user'
    assert '本人已经发给对方' in json.loads(parts[1]['text'])['sender_notice']
    assert json.loads(parts[3]['text'])['visual_message_id'] == 'other-sticker'
    assert json.loads(parts[3]['text'])['speaker_id'] == 'chat_peer'
    assert '对方发来的' in json.loads(parts[3]['text'])['sender_notice']
    assert parts[4]['image_url']['url'].startswith('data:image/gif;base64,')
    assert 'data:image' not in json.dumps(payload)  # Binary isn't copied into memory/timeline JSON.
    assert 'visual_input' not in original['recent'][0]


@pytest.mark.parametrize('problem', ['foreign', 'missing', 'duplicate', 'external', 'corrupt', 'text_target', 'oversize_count', 'unbound_contact', 'duplicate_message'])
def test_unbound_or_invalid_images_stop_before_api(problem):
    value = context()
    v = value['visual_inputs'][0]
    if problem == 'foreign': v['contact_id'] = 'B'
    if problem == 'missing': v['message_id'] = 'outside-history'
    if problem == 'duplicate': value['visual_inputs'].append(deepcopy(v))
    if problem == 'external': v['data_url'] = 'https://arbitrary.test/image.png'
    if problem == 'corrupt': v['data_url'] = 'data:image/png;base64,YmFk'
    if problem == 'text_target': v['message_id'] = 'target'
    if problem == 'oversize_count': value['visual_inputs'] *= 4
    if problem == 'unbound_contact':
        value['contact_id'] = None
        for visual in value['visual_inputs']: visual['contact_id'] = None
    if problem == 'duplicate_message':
        value['recent'].insert(0, deepcopy(value['recent'][1]))
    requests = []
    with pytest.raises(ReplyEngineError): generate(value, requests)
    assert not requests


def test_format_repair_retains_the_same_visual_inputs_without_caption_request():
    requests = []
    generate(context(), requests, response=lambda n: 'bad' if n == 1 else json.dumps(CHOICES))
    assert len(requests) == 2
    assert requests[0]['messages'][1] == requests[1]['messages'][1]
    assert all(request['messages'][0]['content'].startswith('你是微信聊天') for request in requests)


@pytest.mark.parametrize('format', ['PNG', 'JPEG', 'GIF', 'WEBP'])
def test_small_original_image_is_preserved_instead_of_extracted_text(format):
    original = pixels(format)
    assert image_bytes(original) == original
    assert data_url(original).startswith('data:image/' + ('jpeg' if format == 'JPEG' else format.lower()))


def prepared_app(tmp_path, kind='image'):
    app = Controller(ROOT, tmp_path, start_worker=False)
    app.state['settings'] = app.settings.save({'api_key': 'fixture'})
    current = {'contact': {'id': 'A', 'name': '甲'}, 'messages': [
        {'id': 'pic', 'role': 'other', 'kind': kind, 'text': '[图片]',
         'timestamp': '2026-10-12T10:00:00', 'content_known': False}]}
    apply_enabled_snapshot(app, current)
    app.current_adapter = SimpleNamespace(current=lambda: {'name': '甲'})
    app.database = SimpleNamespace(resolve=lambda name: {'id': 'A'},
        enrich=lambda *args: pytest.fail('must not save a generated caption'),
        sticker=lambda *args: data_url(pixels('GIF')))
    app.local_media = SimpleNamespace(image=lambda *args: {'bytes': pixels(), 'tier': 'original'})
    app.cloud_media = SimpleNamespace(describe=lambda *args: pytest.fail('no separate caption call'))
    app.adapter = SimpleNamespace(snapshot=lambda: deepcopy(current))
    app._handle_native('analyze_attachment', {'contact_id': 'A', 'id': 'pic',
        'revision': app.revision, 'consent': True})
    return app, current


@pytest.mark.parametrize('kind', ['image', 'emoji'])
def test_prepared_visual_survives_identical_db_poll_and_reaches_reply_engine(tmp_path, kind):
    app, current = prepared_app(tmp_path, kind)
    try:
        state = app.get_state()
        assert state['generation_blocked_reason'] is None
        assert state['messages'][0]['content_known'] is False
        assert 'data:image' not in json.dumps(state)
        revision = app.revision
        apply_enabled_snapshot(app, current)
        assert app.revision == revision
        seen = []
        class Engine:
            def generate(self, value, *args, **kwargs):
                seen.append(value)
                return {'choices': [], 'source': 'model', 'model': 'fixture', 'elapsed_ms': 1}
        import time
        app.engine = Engine()
        app.pending_at = 0
        app._maybe_generate()
        deadline = time.monotonic() + 3
        while app.generating and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(seen) == 1
        assert seen[0]['visual_inputs'][0]['message_id'] == 'pic'
        assert seen[0]['recent'][0]['timestamp'] == '2026-10-12T10:00:00'
    finally:
        app.close()


def test_switching_contact_drops_approved_image_and_does_not_claim_text_known(tmp_path):
    app, current = prepared_app(tmp_path)
    try:
        current['contact'] = {'id': 'B', 'name': '乙'}
        apply_enabled_snapshot(app, current)
        assert app._visual_inputs == {}
        assert app.get_state()['generation_blocked_reason']
        assert 'visual_input' not in app.get_state()['messages'][0]
    finally: app.close()


def test_changing_api_policy_revokes_prepared_image(tmp_path):
    app, current = prepared_app(tmp_path)
    try:
        result = app.dispatch('save_settings', {'media_policy': 'off'})
        assert result['ok']
        assert app._visual_inputs == {}
        assert app.get_state()['generation_blocked_reason']
        assert 'visual_input' not in app.get_state()['messages'][0]
    finally: app.close()


def test_explicit_visual_request_can_generate_with_auto_generation_disabled(tmp_path):
    app, current = prepared_app(tmp_path)
    try:
        app.state['settings'] = app.settings.save({'auto_generate': False})
        app.pending_at = None
        app._handle_native('analyze_attachment', {'contact_id': 'A', 'id': 'pic',
            'revision': app.revision, 'consent': True})
        assert app.pending_at is not None
        assert app.get_state()['generation_blocked_reason'] is None
    finally: app.close()


def test_changing_original_message_revokes_prepared_image(tmp_path):
    app, current = prepared_app(tmp_path)
    try:
        current['messages'][0]['timestamp'] = '2026-10-12T11:00:00'
        apply_enabled_snapshot(app, current)
        assert app._visual_inputs == {}
        assert app.get_state()['generation_blocked_reason']
    finally: app.close()
