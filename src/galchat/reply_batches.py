"""Displayed reply identity is separate from the growing incoming transcript."""
from copy import deepcopy
import json
import time
from uuid import uuid4


def message_version(message):
    return json.dumps({k: message.get(k) for k in ('id', 'role', 'kind', 'text', 'timestamp',
        'content_known', 'content_source', 'attachment', 'visual_input', 'participant_id',
        'sender_display_name', 'conversation_kind')}, sort_keys=True, ensure_ascii=False)


def incoming_append_only(before, after):
    """Allow bounded-window trimming, never self replies, edits or removals."""
    if not before or not after:
        return False
    anchor = before[-1].get('id')
    if not anchor:
        return False
    positions = [i for i, message in enumerate(after) if message.get('id') == anchor]
    if len(positions) != 1:
        return False
    overlap = positions[0] + 1
    if overlap > len(before) or overlap == len(after):
        return False
    if [message_version(m) for m in before[-overlap:]] != [message_version(m) for m in after[:overlap]]:
        return False
    return all(m.get('role') == 'other' and m.get('id') for m in after[overlap:])


class ReplyBatches:
    """Keep a bounded set of server-issued batches to finish a visible click."""
    def __init__(self):
        self.epoch = 0
        self.batches = {}
        self.current = None

    def clear(self):
        self.epoch += 1
        self.batches.clear()
        self.current = None

    def publish(self, contact_id, revision, messages, choices):
        token = uuid4().hex
        self.current = token
        self.batches[token] = {'contact_id': contact_id, 'epoch': self.epoch,
            'created': time.monotonic(), 'source_revision': revision,
            'source_message_id': messages[-1].get('id') if messages else None,
            'source_timestamp': messages[-1].get('timestamp') if messages else None,
            'source_excerpt': str(messages[-1].get('text') or '')[:32] if messages else '',
            'source_count': len(messages), 'choices': deepcopy(choices)}
        # Old snapshots are only needed for a click already visible/in progress.
        while len(self.batches) > 16:
            del self.batches[next(iter(self.batches))]
        return [{**deepcopy(c), 'batch_id': token} for c in choices]

    def choice(self, token, contact_id, choice_id):
        batch = self.batches.get(token)
        if (batch is None or batch['contact_id'] != contact_id or batch['epoch'] != self.epoch
                or token != self.current and time.monotonic() - batch['created'] > 120):
            return None
        return next((deepcopy(c) for c in batch['choices'] if c['id'] == choice_id), None)

    def describe(self, token, messages):
        batch = self.batches.get(token)
        if batch is None:
            return None
        positions = [i for i, m in enumerate(messages) if m.get('id') == batch['source_message_id']]
        count = len(messages) - positions[0] - 1 if len(positions) == 1 else None
        return {k: batch[k] for k in ('source_revision', 'source_message_id', 'source_timestamp', 'source_count', 'source_excerpt')} | {
            'id': token, 'new_message_count': count, 'has_new_messages': count is None or count > 0}

class BurstSchedule:
    """Optional trailing quiet period, with the last arrival kept for mode changes."""
    def __init__(self, quiet=3.0, max_wait=None):
        self.quiet, self.max_wait = quiet, max_wait
        self.started = None
        self.last_update = None

    def update(self, now, reset=False):
        self.last_update = now
        if reset or self.started is None:
            self.started = now
        return now + self.quiet if self.max_wait is None else min(now + self.quiet, self.started + self.max_wait)

    def clear(self):
        self.started = None
        self.last_update = None
