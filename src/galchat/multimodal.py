"""Bounded inline visual inputs, associated with one recorded chat message."""
from __future__ import annotations

import base64
import binascii
import io
import json
from collections import Counter

from PIL import Image

MAX_VISUALS = 6
MAX_VISUAL_BYTES = 12_000_000
MIMES = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif', 'WEBP': 'image/webp'}


def validate_visual_url(url):
    if not isinstance(url, str) or len(url) > MAX_VISUAL_BYTES * 4 // 3 + 100:
        raise ValueError('图片数据为空或超过本次请求限制。')
    header, separator, encoded = url.partition(',')
    if not separator or header not in {f'data:{mime};base64' for mime in MIMES.values()}:
        raise ValueError('图片必须是已读取的本地图片数据。')
    try:
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_VISUAL_BYTES:
            raise ValueError('图片数据为空或过大。')
        with Image.open(io.BytesIO(data)) as picture:
            if MIMES.get(picture.format) != header[5:-7] or picture.width * picture.height > 40_000_000:
                raise ValueError('图片格式或像素大小不符合要求。')
            picture.verify()
    except (binascii.Error, OSError, Image.DecompressionBombError) as error:
        raise ValueError('图片数据不完整或格式不支持。') from error
    return len(data)


def first_frame_visual(url):
    """Animation is intentionally represented by frame zero, as requested."""
    validate_visual_url(url)
    raw = base64.b64decode(url.partition(',')[2], validate=True)
    with Image.open(io.BytesIO(raw)) as picture:
        if not getattr(picture, 'is_animated', False):
            return url, False
        picture.seek(0)
        out = io.BytesIO()
        picture.convert('RGBA').save(out, format='PNG')
    result = 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode('ascii')
    validate_visual_url(result)
    return result, True


def visual_parts(context, payload):
    """Never admit an unbound visual or quietly upload a different contact."""
    visuals = context.get('visual_inputs') or []
    if not isinstance(visuals, list) or len(visuals) > MAX_VISUALS:
        raise ValueError('一次最多加入6张图片或表情包。')
    if visuals and (not isinstance(context.get('contact_id'), str) or not context['contact_id'].strip()):
        raise ValueError('图片尚未绑定确定的联系人。')
    recent = payload['recent']
    targets = {message['id']: message for message in recent}
    occurrences = Counter(message['id'] for message in recent)
    ordered, seen, size = [], set(), 0
    for visual in visuals:
        if not isinstance(visual, dict) or visual.get('contact_id') != context.get('contact_id'):
            raise ValueError('图片不属于当前联系人，已停止生成。')
        message = targets.get(visual.get('message_id'))
        if message is None or message['kind'] not in {'image', 'emoji'} or message['id'] in seen or occurrences[message['id']] != 1:
            raise ValueError('图片未对应到本次上下文中的唯一消息。')
        url, first_frame_only = first_frame_visual(visual.get('data_url'))
        size += validate_visual_url(url)
        if size > MAX_VISUAL_BYTES:
            raise ValueError('本次图片总量超过12MB，请减少图片。')
        tier = visual.get('tier')
        if tier not in {'original', 'mid', 'thumb', 'sticker'}:
            raise ValueError('图片缓存来源未确定。')
        message['visual_input'] = {'status': 'provided', 'cache_tier': tier}
        if first_frame_only:
            message['visual_input'].update(content_scope='first_frame_only', frame_index=0)
        seen.add(message['id'])
        ordered.append((message['position'], message, {**visual, 'data_url': url}))
    parts = []
    for _, message, visual in sorted(ordered, key=lambda value: value[0]):
        # This header makes sender and time explicit even though all images
        # must be carried by a user API message, including images sent by self.
        parts.extend([
            {'type': 'text', 'text': json.dumps({'visual_message_id': message['id'],
                'chat_role': message['role'],
                'speaker_id': message['speaker_id'],
                'sender_notice': ('这是微信用户本人已经发给对方的图，当前正在替发图者写下一句。发送不代表创作、拍摄或拥有图中物品，不能据此编造这些事实。'
                    if message['role'] == 'self' else '这是当前聊天对方发来的图，当前正在替收图者写回复。'),
                'content_scope': message['visual_input'].get('content_scope', 'image'),
                'timestamp': message['timestamp'],
                'kind': message['kind'], 'cache_tier': visual['tier']}, ensure_ascii=False)},
            {'type': 'image_url', 'image_url': {'url': visual['data_url']}}
        ])
    return parts
