"""Selected-chat native transcript research; local-only, bounded, no UI input.

Reports shapes/counts, never transcript text, audio, account names or keys.
Field 5 / nested field 2 is a research candidate, not a verified UI transcript.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from galchat.contact_database import ContactDatabase


def protobuf_fields(value):
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) > 65536:
        raise ValueError('unsupported or oversized buffer')
    data, index, fields = bytes(value), 0, []
    def varint():
        nonlocal index
        result = 0
        for shift in range(0, 70, 7):
            if index >= len(data):
                raise ValueError('truncated varint')
            byte = data[index]; index += 1
            if shift == 63 and byte > 1:
                raise ValueError('varint overflow')
            result |= (byte & 127) << shift
            if not byte & 128:
                return result
        raise ValueError('varint overflow')
    while index < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not 0 < number <= 0x1fffffff or len(fields) >= 256:
            raise ValueError('invalid field number or too many fields')
        if wire == 0:
            item = varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else 8 if wire == 1 else 4
            if size > len(data) - index:
                raise ValueError('truncated bytes')
            item = data[index:index + size]; index += size
        else:
            raise ValueError('unsupported wire type')
        fields.append((number, wire, item))
    return fields


def inspect_packed(value):
    result = {'bytes': len(value) if isinstance(value, (bytes, bytearray, memoryview)) else 0,
              'valid': False, 'candidate_count': 0}
    try:
        outer = protobuf_fields(value)
        result['valid'] = True
        result['outer_shape'] = [f'{n}:{w}' for n,w,_ in outer]
        children = []
        for number, wire, child in outer:
            if (number, wire) != (5, 2):
                continue
            nested = protobuf_fields(child)
            shape = {'shape': [f'{n}:{w}' for n,w,_ in nested],
                     'field1_varints': [v for n,w,v in nested if (n,w)==(1,0)], 'texts': []}
            for n,w,v in nested:
                if (n,w) != (2,2):
                    continue
                try:
                    text = v.decode('utf-8')
                    valid = bool(text.strip()) and len(text) <= 12000 and not any(
                        ord(c) < 32 and c not in '\n\r\t' for c in text)
                    shape['texts'].append({'utf8': True, 'characters': len(text), 'candidate': valid})
                    result['candidate_count'] += int(valid)
                except UnicodeDecodeError:
                    shape['texts'].append({'utf8': False, 'candidate': False})
            children.append(shape)
        result['field5_children'] = children
    except ValueError:
        result['valid'] = False
        result['candidate_count'] = 0
    return result


def probe(contact_name, count):
    database = ContactDatabase(ROOT, ROOT / 'data')
    try:
        contact = database.resolve(contact_name)
        rows = database.recent(contact, count)
        voice = [r for r in rows if r['kind'] == 'voice']
        samples = []
        for row in voice:
            source = database.media_source(contact['id'], row['id'])['row']
            samples.append({'message_id': row['id'], 'timestamp': row['timestamp'],
                'already_read_by_xml': row.get('content_source') == 'wechat_transcription',
                'duration_seconds': row.get('duration_seconds'),
                'packed': inspect_packed(source.get('_packed_info'))})
        return {'status': 'sampled', 'current_contact_hash': hashlib.sha256(contact['id'].encode()).hexdigest(),
            'requested_rows': count, 'rows': len(rows), 'kinds': dict(Counter(r['kind'] for r in rows)),
            'voices': samples, 'packed_candidates': sum(r['packed']['candidate_count'] for r in samples),
            'gui_match_verified': False, 'scope': 'explicit_observed_chat_only'}
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contact', required=True)
    parser.add_argument('--count', type=int, default=100, choices=(10,30,50,100,200))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / 'data') or output.exists():
        raise ValueError('use a new report file inside data')
    suppressed = io.StringIO()
    try:
        with redirect_stdout(suppressed), redirect_stderr(suppressed):
            report = probe(args.contact, args.count)
    except Exception as error:
        report = {'status': 'failed', 'error_type': type(error).__name__, 'code': getattr(error, 'code', '')}
    report.update({'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
        'private_text_exported': False, 'cloud_requests': False, 'ui_operated': False,
        'keys_saved': False, 'parser_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
