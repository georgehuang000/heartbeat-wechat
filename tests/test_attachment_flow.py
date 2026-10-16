from contact_fixtures import apply_enabled_snapshot
from copy import deepcopy
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from galchat.attachments import parse_message
from galchat.contact_database import ContactDatabase
from galchat.controller import Controller
from galchat.current_chat import CurrentChatAdapter
from galchat.wechat import WeChatError

ROOT = Path(__file__).resolve().parents[1]


def test_attachment_annotation_survives_restart_without_changing_time_or_identity(tmp_path):
    class Reader:
        wxid = 'self'
        def search_contact(self, name):
            return [{'username': 'other', 'nick_name': name}]
        def get_messages(self, *args, **kwargs):
            return [{'local_id': 7, 'sort_seq': 8, 'local_type': 34,
                     'content': '<msg><voicemsg voicelength="5000"/></msg>',
                     'sender_username': 'other', 'create_time': 1750000000, '_galchat_shard': 'one'}]
    db = ContactDatabase(ROOT, tmp_path, Reader())
    contact = db.resolve('甲')
    original = db.recent(contact, 100)[0]
    db.enrich(contact['id'], original['id'], '周六去吧', 'speech_api')
    assert b'\xe5\x91\xa8\xe5\x85\xad' not in next((tmp_path/'attachment-text').glob('*.dpapi')).read_bytes()
    restarted = ContactDatabase(ROOT, tmp_path, Reader())
    result = restarted.recent(contact, 100)[0]
    assert result['text'] == '周六去吧' and result['content_known']
    assert (result['id'], result['timestamp'], result['kind']) == (original['id'], original['timestamp'], 'voice')
    with pytest.raises(WeChatError, match='不属于'):
        restarted.enrich('different-account-contact', original['id'], '污染', 'user_supplied')


def test_long_forwarded_item_is_reported_partial_not_complete():
    xml = '<recordinfo><datalist count="1"><dataitem datatype="1"><datadesc>' + '字'*1500 + '</datadesc></dataitem></datalist></recordinfo>'
    parsed = parse_message((19 << 32) | 49, xml)
    assert not parsed['content_known']
    assert parsed['attachment']['status'] == 'partial'
    assert parsed['attachment']['items'][0]['truncated']


def test_new_database_message_between_display_and_fill_stops_before_input():
    class Database:
        def resolve(self, name): return {'id': 'A'}
        def recent(self, *args): return [{'id': 'new', 'role': 'other', 'text': '换个安排吧'}]
    adapter = CurrentChatAdapter(Database(), SimpleNamespace(), lambda: {'history_limit': 100})
    adapter._last = {'contact': {'id': 'A'}, 'messages': [{'id': 'old', 'role': 'other', 'text': '去吃饭'}]}
    adapter.current = lambda **kwargs: {'name': '甲', 'mode': 'uia'}
    result = adapter.fill_draft('A', '好呀')
    assert not result['ok'] and result['code'] == 'messages_changed'


def test_image_analysis_cannot_apply_after_contact_switch(tmp_path):
    app = Controller(ROOT, tmp_path, start_worker=False)
    app.settings.save({'api_key': 'fixture'})
    apply_enabled_snapshot(app, {'contact': {'id': 'A', 'name': '甲'}, 'messages': [
        {'id': 'image', 'role': 'other', 'kind': 'image', 'text': '[图片]', 'content_known': False}]})
    names = iter(['甲', '乙'])
    app.current_adapter = SimpleNamespace(current=lambda: {'name': next(names)})
    writes = []
    app.database = SimpleNamespace(resolve=lambda name: {'id': 'A' if name == '甲' else 'B'},
                                   enrich=lambda *args: writes.append(args))
    pixels = io.BytesIO()
    Image.new('RGB', (20, 20), 'pink').save(pixels, format='PNG')
    app.local_media = SimpleNamespace(image=lambda *args: {'bytes': pixels.getvalue(), 'tier': 'original'})
    app.cloud_media = SimpleNamespace(describe=lambda *args: '图片描述')
    try:
        with pytest.raises(ValueError, match='切换'):
            app._handle_native('analyze_attachment', {'contact_id': 'A', 'id': 'image',
                'revision': app.revision, 'consent': True})
        assert writes == []
    finally:
        app.close()


def test_no_cloud_consent_never_retrieves_or_sends_image(tmp_path):
    app = Controller(ROOT, tmp_path, start_worker=False)
    apply_enabled_snapshot(app, {'contact': {'id': 'A', 'name': '甲'}, 'messages': [
        {'id': 'image', 'role': 'other', 'kind': 'image', 'text': '[图片]'}]})
    app.current_adapter = SimpleNamespace(current=lambda: {'name': '甲'})
    app.database = SimpleNamespace(resolve=lambda name: {'id': 'A'})
    app.local_media = SimpleNamespace(image=lambda *args: pytest.fail('must not retrieve image'))
    try:
        with pytest.raises(ValueError, match='确认'):
            app._handle_native('analyze_attachment', {'contact_id': 'A', 'id': 'image', 'revision': app.revision})
    finally:
        app.close()


def test_old_generated_caption_does_not_keep_image_on_text_only_route(tmp_path):
    class Reader:
        wxid = 'self'
        def search_contact(self, name): return [{'username': 'other', 'nick_name': name}]
        def get_messages(self, *args, **kwargs):
            return [{'local_id': 7, 'sort_seq': 8, 'local_type': 3,
                'content': '<msg><img/></msg>', 'sender_username': 'other',
                'create_time': 1750000000, '_galchat_shard': 'one'}]
    db = ContactDatabase(ROOT, tmp_path, Reader())
    contact = db.resolve('甲')
    message = db.recent(contact, 10)[0]
    db.enrich(contact['id'], message['id'], '旧模型描述', 'image_analysis_thumb')
    restored = ContactDatabase(ROOT, tmp_path, Reader())
    image = restored.recent(contact, 10)[0]
    assert image['content_known'] is False and image['text'] == '[图片]'
    assert (image['id'], image['timestamp']) == (message['id'], message['timestamp'])
    restored.enrich(contact['id'], message['id'], '用户说这图是昨天拍的', 'user_supplied')
    assert restored.recent(contact, 10)[0]['text'] == '用户说这图是昨天拍的'
