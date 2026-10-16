"""Bounded local message parsing. Forwarded speakers never become chat identity."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from .payment_events import parse_payment
from .rich_cards import application, direct, location, parse_card, system_event

MAX_XML = 256_000
MAX_ITEMS = 50


def xml_root(value):
    if not isinstance(value, str) or len(value) > MAX_XML:
        return None
    if '<!DOCTYPE' in value.upper() or '<!ENTITY' in value.upper():
        return None
    value = re.sub(r'^[A-Za-z0-9_.@-]{1,128}:\s*\n', '', value.strip(), count=1)
    try:
        return ET.fromstring(value)
    except ET.ParseError:
        return None


def field(node, tag, maximum=1200):
    found = node.find('.//' + tag) if node is not None else None
    return (found.text or '').strip()[:maximum] if found is not None else ''


def forwarded(root, depth=0):
    record = root if root is not None and root.tag == 'recordinfo' else None
    if record is None and root is not None:
        record = root.find('.//recordinfo')
    if record is None and root is not None:
        record = xml_root(field(root, 'recorditem', MAX_XML))
    if record is None or depth > 2:
        return {'items': [], 'status': 'preview_only', 'read_count': 0, 'missing_count': None}
    data = record.find('.//datalist')
    nodes = list(data.findall('dataitem')) if data is not None else []
    try:
        declared = int(data.get('count', len(nodes))) if data is not None else 0
    except ValueError:
        declared = len(nodes)
    items, budget, unknown = [], 10_000, 0
    for item in nodes[:MAX_ITEMS]:
        if budget <= 0:
            break
        datatype = item.get('datatype', '')
        full_text = field(item, 'datadesc', MAX_XML)
        text = full_text[:min(1200, budget)]
        truncated = len(text) < len(full_text)
        known = datatype in ('1', '') and bool(text)
        nested = None
        if datatype == '17':
            nested = forwarded(item, depth + 1)
            known = nested['status'] == 'complete'
        kind = {'1': 'text', '2': 'image', '3': 'voice', '4': 'video',
                '8': 'file', '17': 'forward'}.get(datatype, 'other')
        if not known or truncated:
            unknown += 1
        if not known:
            text = '[引用附件内容未读取]'
        budget -= len(text)
        entry = {'author': field(item, 'sourcename', 100),
                 'timestamp': field(item, 'sourcetime', 80), 'kind': kind,
                 'text': text, 'content_known': known}
        if truncated:
            entry['truncated'] = True
        if nested:
            entry['attachment'] = nested
        items.append(entry)
    missing = max(0, declared - len(items)) + unknown
    return {'items': items, 'status': 'complete' if items and not missing else 'partial' if items else 'preview_only',
            'read_count': len(items) - unknown, 'missing_count': missing, 'declared_count': declared}


def parse_message(code, content):
    """Preserve full type number, return readable facts separately from placeholders."""
    base = int(code) & 0xffffffff
    payment = parse_payment(code, str(content or '').strip())
    if payment is not None:
        return payment
    kind = {1: 'text', 3: 'image', 34: 'voice', 42: 'card', 43: 'video', 62: 'video', 48: 'location',
            47: 'emoji', 11000: 'emoji', 50: 'call', 10000: 'system',
            10002: 'system'}.get(base, 'other')
    text = str(content or '').strip()
    result = {'kind': kind, 'text': text if kind == 'text' else '', 'content_known': kind == 'text',
              'local_type': int(code)}
    root = xml_root(text) if kind != 'text' else None
    if base == 49:
        app, subtype = application(root, code)
        if root is not None and root.tag == 'recordinfo' and int(code) >> 32 in {19, 24}:
            record = forwarded(root)
            return {**result, 'kind': 'forward' if int(code) >> 32 == 19 else 'note',
                    'text': '[引用记录]', 'attachment': record, 'content_scope': 'quoted_material',
                    'content_known': record['status'] == 'complete'}
        if app is not None and subtype == '57':
            kind = result['kind'] = 'quote'
            result['text'] = direct(app, 'title') or '[引用消息]'
            result['content_known'] = bool(direct(app, 'title'))
            quoted = app.find('refermsg')
            if quoted is not None:
                quoted_type = direct(quoted, 'type', 20)
                quoted_text = direct(quoted, 'content') if quoted_type in {'', '1'} else '[引用附件正文未取得]'
                result['attachment'] = {'quoted_author': direct(quoted, 'displayname', 100),
                    'quoted_text': quoted_text, 'quoted_kind': quoted_type,
                    'quoted_timestamp': direct(quoted, 'createtime', 80), 'status': 'quoted_text_only'}
        elif app is not None:
            return parse_card(app, subtype, code, forwarded)
    if kind == 'location':
        return location(root, code)
    if kind == 'card':
        card = root if root is not None and root.tag == 'msg' else None
        name = card.get('nickname', '')[:160] if card is not None else ''
        result.update(text='[个人名片] ' + name, content_known=bool(name), content_scope='metadata_only',
                      attachment={'display_name': name, 'status': 'card_metadata_only'})
    elif kind == 'call':
        visible = field(root, 'msg', 300) or (text if root is None and not text.startswith('<') else '')
        result.update(text='[通话记录] ' + visible, content_known=True, content_scope='event_only',
                      attachment={'status': 'call_event_only'})
    elif kind == 'voice':
        voice = root.find('.//voicemsg') if root is not None else None
        if root is not None and root.tag == 'voicemsg':
            voice = root
        try:
            duration = float(voice.get('voicelength', '')) / 1000 if voice is not None else None
            if duration is not None and 0 <= duration <= 3600:
                result['duration_seconds'] = duration
        except ValueError:
            pass
        transcript = field(root, 'voicetrans') or field(root, 'voicetranscription') or field(root, 'transcription')
        if transcript:
            result.update(text=transcript, content_known=True, content_source='wechat_transcription')
    elif kind == 'video':
        result.update(text='[视频消息：未提供视频内容]', content_known=False, content_scope='event_only',
                      attachment={'status': 'video_event_only', 'event': 'video_message'})
    elif kind == 'system':
        return system_event(root, text, code)
    if not result['text']:
        result['text'] = {'voice': '[语音]', 'image': '[图片]', 'emoji': '[表情]',
                          'video': '[视频]', 'file': '[文件]'}.get(result['kind'], '[其他消息]')
    return result


def readiness(messages):
    """Content gaps produce an honest waiting route, never age-based chat ending."""
    human = [m for m in messages if m.get('role') in {'self', 'other'} and m.get('kind') != 'system']
    if not human:
        return '尚未取得可确认发言方的聊天消息。'
    last = human[-1]
    if last.get('truncated'):
        return '最新消息正文不完整，请补充正文后继续。'
    kind = last.get('kind', 'text')
    known = last.get('content_known', kind == 'text')
    if kind in {'image', 'emoji', 'voice'} and last.get('media_read_status') in {'skipped', 'unavailable'}:
        return None
    if (kind == 'video' and last.get('attachment', {}).get('status') == 'video_event_only'):
        return None
    if known or (kind == 'image' and last.get('visual_input', {}).get('status') == 'ready') or kind in {'emoji', 'call', 'card', 'forward', 'link', 'file', 'transfer', 'red_envelope', 'payment', 'location', 'live_location', 'mini_program', 'music', 'channel', 'live', 'note', 'pat', 'announcement', 'solitaire', 'application_card'}:
        return None
    if kind in {'image', 'voice', 'video'}:
        return {'image': '最新图片尚未加入模型上下文，可直接传图或补充内容。',
                'voice': '最新语音尚未转写，可转写或补充内容。',
                'video': '最新视频正文未读取，可补充你希望讨论的内容。'}[kind]
    return '最新消息类型尚未识别，可补充内容后继续。'
