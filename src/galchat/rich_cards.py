"""Whitelisted card/event facts. Metadata never implies reading a resource."""
import math
import re


def direct(node, path, limit=1200):
    nodes = node.findall(path) if node is not None else []
    if len(nodes) != 1 or list(nodes[0]):
        return ''
    return (nodes[0].text or '').strip()[:limit]


def application(root, code):
    apps = ([root] if root is not None and root.tag == 'appmsg' else
            root.findall('appmsg') if root is not None else [])
    app = apps[0] if len(apps) == 1 else None
    subtype = direct(app, 'type', 20)
    high = int(code) >> 32
    if app is None or len(app.findall('type')) > 1 or not re.fullmatch(r'\d{1,6}', subtype or str(high)):
        return None, ''
    if high and subtype and high != int(subtype):
        return None, ''
    return app, subtype or str(high)


def card_result(kind, label, meta, code, *, scope='metadata_only', known=False):
    meta = {k: v for k, v in meta.items() if v != '' and v is not None}
    meta.setdefault('status', 'card_metadata_only' if scope == 'metadata_only' else 'event_only')
    meta['content_scope'] = scope
    title = meta.get('title') or meta.get('display_name') or meta.get('place_name') or ''
    return {'kind': kind, 'text': '[' + label + '] ' + title,
            'content_known': known, 'content_scope': scope, 'local_type': int(code), 'attachment': meta}


def location(root, code):
    nodes = ([root] if root is not None and root.tag == 'location' else
             root.findall('location') if root is not None else [])
    node = nodes[0] if len(nodes) == 1 else None
    meta = {'event': 'location_shared', 'status': 'card_metadata_only'}
    if node is not None:
        for attr, key in [('poiname', 'place_name'), ('label', 'address'), ('cityname', 'city')]:
            if node.get(attr): meta[key] = node.get(attr)[:300]
        # Coordinates describe a shared pin, not the sender's actual whereabouts.
        try:
            lat, lon = float(node.get('x', '')), float(node.get('y', ''))
            if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                meta.update(latitude=lat, longitude=lon)
        except ValueError:
            pass
    return card_result('location', '分享位置', meta, code)


def parse_card(app, subtype, code, record_parser):
    title, description = direct(app, 'title', 300), direct(app, 'des')
    meta = {'title': title, 'description': description, 'app_subtype': subtype}
    if subtype == '1':
        body = direct(app, 'content') or title
        return {**card_result('text', '文本卡片', meta, code, scope='text', known=bool(body)), 'text': body or '[文本卡片]'}
    if subtype in {'19', '24'}:
        record = record_parser(app)
        record['title'] = title
        record['content_scope'] = 'quoted_material'
        kind = 'forward' if subtype == '19' else 'note'
        return {**card_result(kind, '合并聊天记录' if kind == 'forward' else '收藏笔记', {}, code,
                             scope='quoted_material', known=record['status'] == 'complete'),
                'text': ('[合并聊天记录] ' if kind == 'forward' else '[收藏笔记] ') + title,
                'attachment': record}
    if subtype in {'6', '130'}:
        meta.update(file_extension=direct(app, 'appattach/fileext', 32), file_size=direct(app, 'appattach/totallen', 24))
        return card_result('file', '文件' if subtype == '6' else '文件更新', meta, code)
    if subtype == '5':
        # Do not dereference links or upload credential-bearing query strings.
        return card_result('link', '链接', meta, code)
    if subtype in {'33', '36'}:
        meta['publisher'] = direct(app, 'sourcedisplayname', 160)
        return card_result('mini_program', '购物小程序' if subtype == '36' else '小程序', meta, code)
    if subtype == '3':
        meta['artist'] = direct(app, 'songartist', 160)
        return card_result('music', '音乐分享', meta, code)
    if subtype in {'51', '63'}:
        path = 'finderFeed' if subtype == '51' else 'finderLive'
        meta.update(author=direct(app, path + '/nickname', 160), description=direct(app, path + '/desc'))
        meta['title'] = direct(app, path + '/title', 300) or title
        if subtype == '63': meta['live_status'] = direct(app, path + '/liveStatus', 40)
        return card_result('channel' if subtype == '51' else 'live', '视频号分享' if subtype == '51' else '直播卡片', meta, code)
    if subtype == '17':
        meta.update(event='live_location_shared', status='event_only')
        return card_result('live_location', '共享实时位置事件', meta, code, scope='event_only')
    if subtype == '62':
        meta.update(event='pat', status='event_only')
        return card_result('pat', '拍一拍', meta, code, scope='event_only')
    if subtype == '87':
        body = direct(app, 'xmlpuretext') or direct(app, 'textannouncement') or direct(app, 'announcement/textannouncement/xmlpuretext')
        # Some versions store textannouncement directly; nested IDs are never prose.
        body = body or direct(app, 'announcement/textannouncement')
        return {**card_result('announcement', '群公告', meta, code, scope='text' if body else 'metadata_only', known=bool(body)),
                'text': body or '[群公告] ' + title}
    if subtype == '53':
        # solitaire_info may be structured/encoded. Title is not the full roster.
        return card_result('solitaire', '群接龙', meta, code)
    # 4 is also used by product/service cards: do not label every type4 a video.
    return card_result('application_card', '应用卡片', meta, code)


def system_event(root, text, code):
    event = root.get('type', '') if root is not None and root.tag == 'sysmsg' else ''
    event = event if re.fullmatch('[A-Za-z0-9_]{1,60}', event) else 'system_message'
    description = ''
    if root is not None:
        for path in [event + '/replacemsg', event + '/content', 'replacemsg', 'content']:
            description = direct(root, path, 600)
            if description: break
    elif not text.startswith('<') and len(text) <= 1200:
        description = text
    result = card_result('system', '系统事件', {'event': event, 'description': description,
                         'status': 'event_only'}, code, scope='event_only')
    result['text'] = '[系统事件] ' + (description or {'revokemsg': '撤回了一条消息', 'pat': '拍一拍'}.get(event, '事件正文未取得'))
    return result
