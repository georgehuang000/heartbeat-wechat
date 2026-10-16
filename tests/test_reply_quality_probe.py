"""Verify evaluation evidence boundaries, not model quality with mocked prose."""
import importlib.util
from pathlib import Path
import sys

import pytest


PATH = Path(__file__).resolve().parents[1] / 'scripts/probe_reply_quality.py'
SPEC = importlib.util.spec_from_file_location('reply_quality_probe', PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_default_evaluation_never_requests_api_or_loads_credentials(monkeypatch, capsys):
    from galchat.settings import Settings
    monkeypatch.setattr(Settings, 'model_config', lambda *args: pytest.fail('must not load credentials'))
    monkeypatch.setattr(probe.ReplyEngine, '_request', lambda *args: pytest.fail('must not request API'))
    monkeypatch.setattr(sys, 'argv', ['probe_reply_quality.py'])
    assert probe.main() == 0
    assert '"api_called": false' in capsys.readouterr().out


def test_live_mode_requires_evidence_destination_before_loading_credentials(monkeypatch):
    from galchat.settings import Settings
    monkeypatch.setattr(Settings, 'model_config', lambda *args: pytest.fail('must not load credentials'))
    monkeypatch.setattr(sys, 'argv', ['probe_reply_quality.py', '--live'])
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


def test_probe_preserves_evidence_and_never_overwrites_existing_report(tmp_path, monkeypatch):
    output = tmp_path / 'report.json'
    output.write_text('original evidence', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['probe_reply_quality.py', '--output', str(output)])
    with pytest.raises(SystemExit):
        probe.main()
    assert output.read_text(encoding='utf-8') == 'original evidence'


def test_corpus_context_preserves_both_roles_unknown_media_and_original_dates():
    cases = {c['id']: c for c in probe.load_cases()['cases']}
    engine = probe.ReplyEngine()
    self_context = engine._payload_context(probe.case_context(cases['regression-self-last']))
    assert [m['role'] for m in self_context['recent']] == ['other', 'self']
    unread = engine._payload_context(probe.case_context(cases['regression-unread']))
    assert unread['recent'][0]['content_known'] is False
    assert unread['recent'][0]['kind'] == 'forward'
    switched = engine._payload_context(probe.case_context(cases['regression-topic-switch']))
    assert switched['recent'][0]['timestamp'] == '2026-10-01T10:00:00'
    assert switched['reply_focus']['priority_message_ids'] == ['regression-topic-switch-1']
