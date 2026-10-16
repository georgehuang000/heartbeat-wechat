"""Local request accounting. Never store prompts, pictures or credentials."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading


class ApiUsageLog:
    def __init__(self, directory: Path):
        self.path = directory / 'api-usage.jsonl'
        self.lock = threading.Lock()

    def record(self, event):
        # The engine emits only this numeric/request metadata contract.
        permitted = ('started_at', 'elapsed_ms', 'model', 'api_type', 'provider_host', 'operation', 'status_code',
            'error_code', 'response_id', 'usage', 'usage_known')
        row = {key: event[key] for key in permitted if key in event}
        row.update(pid=os.getpid(), runtime='packaged' if getattr(sys, 'frozen', False) else 'source')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock, self.path.open('a', encoding='utf-8') as file:
                file.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass  # Accounting must not interrupt a response or a draft action.

    def summary(self, provider_host=None):
        today = datetime.now().astimezone().date()
        rows = []
        if self.path.exists():
            with self.lock, self.path.open(encoding='utf-8') as file:
                for line in file:
                    try:
                        row = json.loads(line)
                        if (datetime.fromisoformat(row['started_at']).astimezone().date() == today
                                and (provider_host is None or row.get('provider_host') == provider_host)):
                            rows.append(row)
                    except (ValueError, KeyError, TypeError):
                        continue
        measured = [row for row in rows if row.get('usage_known') is True]
        return {'date': str(today), 'requests': len(rows), 'measured_requests': len(measured),
            'model_requests': sum(row.get('operation') != 'model_probe' for row in rows),
            'metadata_requests': sum(row.get('operation') == 'model_probe' for row in rows),
            'failed_requests': sum(bool(row.get('error_code')) or (row.get('status_code') or 0) >= 400 for row in rows),
            'unknown_usage_requests': len(rows)-len(measured),
            'total_tokens': sum(row['usage'].get('total_tokens', 0) for row in measured),
            'input_tokens': sum(row['usage'].get('prompt_tokens', 0) for row in measured),
            'output_tokens': sum(row['usage'].get('completion_tokens', 0) for row in measured),
            'scope': 'Only this application after the local usage log was enabled; not the shared API key total',
            'historical_usage_recovered': False}
