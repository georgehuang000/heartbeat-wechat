"""Bounded, version-pinned database reader. No GUI imports or sending API."""
from __future__ import annotations

import hashlib
import importlib.util
import re
import json
import os
from datetime import datetime
from pathlib import Path

from .wechat import WeChatError
from .activity import check_activity
from .contact_profile import contact_gender
from .stickers import sticker_reference, read_sticker
from .attachments import parse_message
from .native_voice import stored_voice_text
from .conversation_identity import HISTORY_LIMITS, group_participant, is_group


def contact_key(account: str, username: str) -> str:
    return 'wxdb:' + hashlib.sha256((account + '\0' + username).encode()).hexdigest()[:32]


def read_self_info(reader):
    """Close profile reads deterministically before Windows cache replacement."""
    conn=reader._contact_conn()
    if conn is None:
        raise WeChatError('account_unreadable','暂时无法核对微信账号，请稍后重试。')
    try:
        row=conn.execute('SELECT username,nick_name,remark FROM contact WHERE username=? LIMIT 1',
                         (reader.wxid,)).fetchone()
        return dict(row) if row else {'username':reader.wxid,'nick_name':'','remark':''}
    finally:
        conn.close()


def resolve_contact(name: str, candidates: list[dict]) -> dict:
    # A LIKE query is only candidate discovery; never pick its first row.
    matches = {r['username']: r for r in candidates
               if (r.get('remark') or r.get('nick_name') or r.get('username')) == name}
    if len(matches) != 1:
        raise WeChatError('ambiguous_contact' if matches else 'contact_not_found',
                          '联系人重名，请给当前对象设置唯一备注后继续。' if matches else '当前聊天未能对应到联系人数据库。')
    return next(iter(matches.values()))


class ContactDatabase:
    def __init__(self, root: Path, directory: Path, reader=None):
        self.root, self.directory, self._reader = root, directory, reader
        self._sticker_refs = {}
        self._media_refs = {}
        self._enriched = {}

    @property
    def reader(self):
        check_activity(getattr(self, 'activity_check', None))
        if self._reader is None:
            path = self.root / 'scripts' / 'probe_wechat_db.py'
            spec = importlib.util.spec_from_file_location('galchat_verified_db_loader', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            upstream, reviewed = module.load_reader()

            class ScopedReader(reviewed):
                def _collect_db_files(self):
                    return [e for e in upstream.WeChatDB._collect_db_files(self)
                            if Path(e[1]).name in {'contact.db', 'message_resource.db'}
                            or re.fullmatch(r'(?:message|media)_\d+\.db', Path(e[1]).name)]

                def _load_or_extract_keys(self, master_key=None):
                    self._keys = self.extract_keys()
                    self.unkeyed = [rel for rel, _, _ in self._db_files if not self._key_works(rel)]
                    if any(not Path(rel).name.startswith('media_') for rel in self.unkeyed):
                        raise RuntimeError('Required database key validation failed')

                def get_self_info(self):
                    # Upstream leaves this SQLite connection open until GC.
                    return read_self_info(self)

                def _shard_rows(self, tables, *args, **kwargs):
                    rows = []
                    for conn, table in tables:
                        # row identity remains stable when shards share local_id.
                        db_path = conn.execute('PRAGMA database_list').fetchone()[2]
                        shard = Path(db_path).name
                        for row in super()._shard_rows([(conn, table)], *args, **kwargs):
                            row['_galchat_shard'] = shard
                            rows.append(row)
                    return rows

                def _msg_row_to_dict(self, row):
                    result = super()._msg_row_to_dict(row)
                    result['_galchat_shard'] = row['_galchat_shard']
                    # Upstream's `type` is a Chinese label, not the numeric code.
                    result['local_type'] = row['local_type']
                    result['server_id'] = row.get('server_id')
                    result['_media_content'] = row.get('message_content')
                    result['_packed_info'] = row.get('packed_info_data')
                    return result

            db_dir = upstream.auto_detect_db_dir()
            accounts = upstream.list_accounts(db_dir) if db_dir else []
            if len(accounts) != 1:
                raise WeChatError('ambiguous_account', '需要唯一的已登录微信账号数据库，请关闭其他账号后再试。')
            cache = self.directory / 'readonly-db-probe' / 'cache'
            self._reader = ScopedReader(db_dir=db_dir, account=accounts[0]['account'],
                                        workdir=str(cache), keys_file=str(cache / 'unused-keys.json'))
        return self._reader

    @property
    def account(self):
        return self.reader.wxid

    def resolve(self, name):
        check_activity(getattr(self, 'activity_check', None))
        if hasattr(self.reader, '_contact_conn'):
            conn = self.reader._contact_conn()
            if conn is None:
                raise WeChatError('contact_not_found', '联系人数据库尚未可读。')
            try:
                candidates = [dict(r) for r in conn.execute(
                    'SELECT username,nick_name,remark,extra_buffer FROM contact WHERE remark=? OR nick_name=? OR username=?',
                    (name,name,name)).fetchall()]
            finally:
                conn.close()
        else:
            candidates = self.reader.search_contact(name)
        row = resolve_contact(name, candidates)
        group = row['username'].endswith('@chatroom')
        gender=0 if group else contact_gender(row.get('extra_buffer'))
        return {'id': contact_key(self.account, row['username']), 'name': row.get('remark') or row.get('nick_name') or name,
                'username': row['username'], 'account': self.account, 'identity': 'database',
                'gender':gender, 'gender_source':'contact_extra_buffer_v4' if gender else 'unknown',
                'conversation_kind': 'group' if group else 'private'}

    def _speaker_names(self, senders):
        """Contact remarks/nicknames are labels, not unverified room aliases."""
        result = {}
        names = sorted(senders)
        if not names or not hasattr(self.reader, '_contact_conn'):
            return result
        conn = self.reader._contact_conn()
        if conn is None:
            return result
        try:
            for start in range(0, len(names), 200):
                batch = names[start:start + 200]
                rows = conn.execute('SELECT username,nick_name,remark FROM contact WHERE username IN (' +
                    ','.join('?' for _ in batch) + ')', batch).fetchall()
                for row in rows:
                    value = dict(row)
                    result[value['username']] = value.get('remark') or value.get('nick_name') or ''
        finally:
            conn.close()
        return result

    def recent(self, contact, count):
        check_activity(getattr(self, 'activity_check', None))
        if not isinstance(count, int) or isinstance(count, bool) or count not in HISTORY_LIMITS:
            raise ValueError('请选择有效消息条数，最多5000条。')
        if contact['account'] != self.account:
            raise WeChatError('account_changed', '微信账号已变化，请重新识别聊天。')
        raw = self.reader.get_messages(contact['username'], limit=count, offset=0)
        group = is_group(contact)
        names = self._speaker_names({r.get('sender_username') for r in raw
            if isinstance(r.get('sender_username'), str) and r['sender_username']}) if group else {}
        output = []
        sticker_refs = {}
        media_refs = {}
        for row in reversed(raw):
            check_activity(getattr(self, 'activity_check', None))
            sender = row.get('sender_username')
            valid_member = isinstance(sender, str) and bool(sender) and not sender.endswith('@chatroom')
            role = 'self' if sender == self.account else 'other' if (
                group and valid_member or not group and sender == contact['username']) else 'unknown'
            code = int(row.get('local_type', row.get('type', 0)))
            stamp = row.get('create_time', row.get('timestamp', 0))
            try:
                timestamp = datetime.fromtimestamp(float(stamp)).isoformat(timespec='seconds')
            except (TypeError, ValueError, OSError):
                timestamp = ''
            identity = (row.get('_galchat_shard'), row.get('local_id'), row.get('sort_seq'), row.get('server_id'))
            text = str(row.get('content') or row.get('text') or '').strip()
            if group and isinstance(sender, str):
                wrapper = re.match(re.escape(sender) + r':\r?\n', text)
                if wrapper:
                    text = text[wrapper.end():]
            message_id = hashlib.sha256(repr(identity).encode()).hexdigest()
            parsed = parse_message(code, text)
            kind = parsed['kind']
            if kind == 'voice':
                transcript = stored_voice_text(code, row.get('_packed_info'))
                if transcript:
                    parsed.update(text=transcript, content_known=True, content_source='wechat_transcription')
            if kind in {'system', 'pat'}:
                role = 'system'
            sticker = sticker_reference(text) if kind == 'emoji' else None
            if sticker:
                sticker_refs[(contact['id'], message_id)] = sticker
            media_refs[(contact['id'], message_id)] = {'contact': dict(contact), 'row': row}
            annotation = self._annotation(contact['id'], message_id)
            # Legacy generated descriptions must not keep an image on the old
            # text-only route. User-supplied notes remain useful explicit text.
            native_voice_preferred = (kind == 'voice' and parsed.get('content_source') == 'wechat_transcription'
                                      and annotation.get('content_source') == 'speech_api')
            if not native_voice_preferred and not (kind == 'image' and str(annotation.get('content_source', '')).startswith('image_analysis_')):
                parsed.update(annotation)
            speaker = ('wechat_user' if role == 'self' else group_participant(self.account, contact['username'], sender)
                if group and role == 'other' else None)
            label = (names.get(sender) or '你') if role == 'self' else names.get(sender) or ('群成员 ' + speaker[-6:] if speaker else '')
            output.append({**parsed, 'id': message_id, 'role': role, 'timestamp': timestamp,
                           'has_sticker': bool(sticker),
                           'source': 'wechat-db', 'contact_id': contact['id'],
                           **({'participant_id': speaker, 'sender_display_name': label,
                               'conversation_kind': 'group'} if group else {})})
        self._sticker_refs = sticker_refs
        self._media_refs = media_refs
        return output

    def media_source(self, contact_id, message_id):
        check_activity(getattr(self, 'activity_check', None))
        value = self._media_refs.get((contact_id, message_id))
        if value is None or value['contact']['account'] != self.account:
            raise WeChatError('stale_attachment', '附件不属于当前已读取聊天，请重新读取。')
        return value

    def enrich(self, contact_id, message_id, text, source):
        check_activity(getattr(self, 'activity_check', None))
        self.media_source(contact_id, message_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError('附件正文为空或过长')
        annotation = {
            'text': text.strip(), 'content_known': True, 'content_source': source}
        import win32crypt
        path = self._annotation_path(contact_id, message_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        protected = win32crypt.CryptProtectData(json.dumps(annotation, ensure_ascii=False).encode(),
            'HeartWeChat attachment text', None, None, None, 0)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(protected)
        os.replace(temporary, path)
        self._enriched[(contact_id, message_id)] = annotation

    def _annotation_path(self, contact_id, message_id):
        key = hashlib.sha256((contact_id + '\0' + message_id).encode()).hexdigest()
        return self.directory / 'attachment-text' / (key + '.dpapi')

    def _annotation(self, contact_id, message_id):
        key = (contact_id, message_id)
        if key not in self._enriched:
            path = self._annotation_path(*key)
            if path.is_file() and path.stat().st_size <= 64000:
                try:
                    import win32crypt
                    result = json.loads(win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1].decode())
                    if (isinstance(result, dict) and isinstance(result.get('text'), str)
                            and 0 < len(result['text']) <= 12000 and isinstance(result.get('content_source'), str)):
                        self._enriched[key] = {k:result[k] for k in ('text','content_source','content_known') if k in result}
                except Exception:
                    pass  # Unusable cache does not turn unread media into facts.
        return self._enriched.get(key, {})

    def sticker(self, contact_id, message_id):
        check_activity(getattr(self, 'activity_check', None))
        reference = self._sticker_refs.get((contact_id, message_id))
        account_dir = getattr(self.reader, 'account_dir', None)
        return read_sticker(account_dir, reference, self.directory / 'sticker-cache') if reference and account_dir else None

    def close(self):
        if self._reader is not None and hasattr(self._reader, 'close'):
            self._reader.close()
