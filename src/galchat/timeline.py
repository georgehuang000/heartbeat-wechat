"""Time features for ordered messages; never closes a chat or guesses topic links.

Boundaries are candidates for semantic interpretation, not deletions or a
decision that a question expired. Audio duration never rewrites message time.
"""
from datetime import datetime, timezone, timedelta
import re

LOCAL = timezone(timedelta(hours=8))


def parse_time(value):
    if not isinstance(value, str) or not re.match(r'^\d{4}-\d\d-\d\d[ T]\d\d:\d\d', value):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.replace(tzinfo=LOCAL) if result.tzinfo is None else result.astimezone(LOCAL)
    except ValueError:
        return None


def delta(current, previous):
    if current is None or previous is None:
        return None
    gap = (current - previous).total_seconds()
    return gap if gap >= 0 else None


def build_timeline(messages, *, now=None):
    rows = messages[-5000:]
    result, boundaries, quoted = [], [], []
    previous, previous_human = None, None
    for i, row in enumerate(rows):
        stamp = parse_time(row.get('timestamp'))
        role, kind = row.get('role', 'unknown'), row.get('kind', 'text')
        human = role in {'self', 'other'} and kind != 'system'
        gap = delta(stamp, previous)
        human_gap = delta(stamp, previous_human) if human else None
        mid = str(row.get('id') or f'position-{i}')[:160]
        result.append({'message_id': mid, 'position': i, 'role': role, 'kind': kind,
                       'timestamp': str(row.get('timestamp') or '')[:80],
                       'gap_prev_seconds': gap, 'gap_prev_human_seconds': human_gap,
                       'time_status': 'unknown' if stamp is None else
                           'out_of_order' if previous is not None and stamp < previous else 'recorded',
                       'duration_seconds': row.get('duration_seconds')
                           if isinstance(row.get('duration_seconds'), (int, float))
                           and not isinstance(row.get('duration_seconds'), bool)
                           and 0 <= row['duration_seconds'] <= 86400 else None})
        # One hour is only a marker to draw the model's attention. Semantics can
        # join across it, or split a topic at a much shorter interval.
        if human_gap is not None and human_gap >= 3600:
            boundaries.append({'before_message_id': mid, 'gap_seconds': human_gap,
                               'meaning': 'time_gap_only_not_topic_decision'})
        if kind in {'forward', 'note'}:
            attachment = row.get('attachment')
            items = attachment.get('items', []) if isinstance(attachment, dict) else []
            items = items if isinstance(items, list) else []
            quoted.append({'outer_message_id': mid, 'outer_timestamp': row.get('timestamp', ''),
                           'original_times': [str(item.get('timestamp') or '')[:80]
                               for item in items[:50] if isinstance(item, dict)],
                           'meaning': 'quoted_timeline_not_current_chat'})
        previous = stamp
        if human:
            previous_human = stamp
    now = now or datetime.now(LOCAL)
    return {'now': now.astimezone(LOCAL).isoformat(timespec='seconds'), 'timezone': 'Asia/Shanghai',
            'reply_target': result[-1]['message_id'] if result else None,
            'messages': result, 'candidate_boundaries': boundaries, 'quoted_timelines': quoted,
            'policy': 'Time gaps inform context segmentation, never automatic chat ending.'}
