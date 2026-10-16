"""Bounded, local-only validation of the reviewed WeChat database reader.

Uses the pinned wheel's DB module without importing its GUI package entry point.
Reports counts and checks only; chat text and database keys are never reported.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import logging
from pathlib import Path
import re
import sqlite3
import sys
import time
import types
import traceback
import zipfile

ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
REVIEW = ROOT / "data" / "dependency-review"
WHEEL_SHA256 = "8f3de71dd9dc885b0b60d0d49fefaed857f44820dca8793b35e34f4c68896234"


def load_reader():
    wheel = REVIEW / "wechatauto_replica-1.2.5.1-py3-none-any.whl"
    source = REVIEW / "replica-1.2.5.1" / "wechatauto" / "db.py"
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
        raise RuntimeError("Reviewed wheel checksum mismatch")
    with zipfile.ZipFile(wheel) as archive:
        if source.read_bytes() != archive.read("wechatauto/db.py"):
            raise RuntimeError("Reviewed database module was changed")
    sys.path.insert(0, str(REVIEW / "probe-deps"))
    if "wechatauto" in sys.modules:
        raise RuntimeError("Use a fresh isolated probe process")
    package = types.ModuleType("wechatauto")
    package.__path__ = [str(source.parent)]
    sys.modules["wechatauto"] = package
    logger = types.ModuleType("wechatauto.logger")
    logger.wxlog = logging.getLogger("nextline.db-probe")
    logger.wxlog.addHandler(logging.NullHandler())
    logger.wxlog.propagate = False
    sys.modules["wechatauto.logger"] = logger
    spec = importlib.util.spec_from_file_location("wechatauto.db", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wechatauto.db"] = module
    spec.loader.exec_module(module)

    class ProbeDB(module.WeChatDB):
        def _pick_account(self):
            accounts = module.list_accounts(self.db_dir)
            if len(accounts) != 1:
                raise RuntimeError("Account selection is ambiguous; stop before reading")
            return accounts[0]["account"]

        def _collect_db_files(self):
            return [entry for entry in super()._collect_db_files()
                    if Path(entry[1]).name in {"contact.db", "message_resource.db"}
                    or re.fullmatch(r"message_\d+\.db", Path(entry[1]).name)]

        def _load_or_extract_keys(self, master_key=None):
            # Keep validated DB keys in this process only. No image/cfg probing,
            # persistent key copies, or automatic account switching.
            self._keys = self.extract_keys()
            self.unkeyed = [rel for rel, _, _ in self._db_files
                            if not self._key_works(rel)]
            if self.unkeyed:
                raise RuntimeError("Required database key validation failed")

        def _save_keys(self):
            pass

        def _shard_rows(self, tables, sql_ext, params=(), order_ext="", per_shard_limit=None):
            rows = []
            for conn, table in tables:
                batch = super()._shard_rows([(conn, table)], sql_ext, params,
                                           order_ext, per_shard_limit)
                for source in batch:
                    row = dict(source)
                    sender = conn.execute("SELECT user_name FROM Name2Id WHERE rowid=?",
                                          (row["real_sender_id"],)).fetchone()
                    row["_probe_sender"] = sender[0] if sender else ""
                    rows.append(row)
            return rows

        def _msg_row_to_dict(self, row):
            result = super()._msg_row_to_dict(row)
            # Message sender IDs refer to this message shard's Name2Id,
            # not the independently numbered media resource SenderName2Id.
            result["sender_username"] = row["_probe_sender"]
            return result

    return module, ProbeDB


def row_identity(row):
    return (row["sort_seq"], row["local_id"], row["sender_id"])


def validate(args):
    module, Reader = load_reader()
    directory = module.auto_detect_db_dir()
    accounts = module.list_accounts(directory) if directory else []
    metadata = {"database_directory_found": bool(directory),
                "account_count": len(accounts), "python_bits": 64 if sys.maxsize > 2**32 else 32,
                "reader_version": "1.2.5.1", "wheel_sha256": WHEEL_SHA256}
    if args.metadata_only:
        return {"ok": bool(directory) and len(accounts) == 1, **metadata}
    if not directory or len(accounts) != 1:
        raise RuntimeError("No unique account database directory")
    workdir = ROOT / "data" / "readonly-db-probe" / "cache"
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    db = Reader(db_dir=directory, account=accounts[0]["account"],
                workdir=str(workdir), keys_file=str(workdir / "unused-keys.json"))
    info = db.get_self_info()
    if args.account_name and info.get("nick_name") != args.account_name:
        raise RuntimeError("Logged-in account does not match the confirmed profile")
    candidates = [entry for entry in db.search_contact(args.contact)
                  if args.contact in {entry.get("nick_name"), entry.get("remark")}]
    if len(candidates) != 1:
        raise RuntimeError("Contact name is not an exact unique match")
    user = candidates[0]["username"]
    first = db.get_messages(user, limit=20)
    older = db.get_messages(user, limit=20, offset=20)
    combined = db.get_messages(user, limit=40)
    if not first:
        raise RuntimeError("Target conversation has no readable recent messages")
    sender_index = db._sender_id_index()
    roles = {}
    for row in combined:
        sender = row["sender_username"]
        roles[row_identity(row)] = ("self" if sender == info["username"]
                                   else "other" if sender == user else "unknown")
    diagnostic = {"sender_index_count": len(sender_index),
                  "sender_index_contains_self": info["username"] in sender_index.values(),
                  "sender_index_contains_contact": user in sender_index.values(),
                  "sample_sender_id_count": len({row["sender_id"] for row in combined}),
                  "sample_sender_ids_in_resource_index": sum(row["sender_id"] in sender_index for row in combined),
                  "sample_first_utc": datetime.fromtimestamp(min(row["create_time"] for row in combined), timezone.utc).isoformat(),
                  "sample_last_utc": datetime.fromtimestamp(max(row["create_time"] for row in combined), timezone.utc).isoformat()}
    memory = sqlite3.connect(f"file:{ROOT / 'data' / 'memory.sqlite3'}?mode=ro", uri=True)
    try:
        saved = memory.execute(
            "SELECT m.role,m.text FROM messages m JOIN contacts c ON c.id=m.contact_id "
            "WHERE c.name=?", (args.contact,)).fetchall()
        bounds = memory.execute(
            "SELECT MIN(sort_time),MAX(sort_time) FROM messages m "
            "JOIN contacts c ON c.id=m.contact_id WHERE c.name=?", (args.contact,)).fetchone()
    finally:
        memory.close()
    by_text = {}
    for row in combined:
        if row["type"] == "文本" and isinstance(row["content"], str):
            by_text.setdefault(row["content"].strip(), set()).add(roles[row_identity(row)])
    if bounds and bounds[0]:
        # Current messages can be much newer than yesterday's visual sample.
        # Query a bounded time window around that saved sample, target chat only.
        anchor_rows = db._run_msg_query(user, lambda tables: db._shard_rows(
            tables, "WHERE create_time BETWEEN ? AND ?",
            (int(bounds[0]) - 7200, int(bounds[1]) + 300),
            order_ext=db._MSG_ORDER_DESC, per_shard_limit=100)) or []
        for raw in anchor_rows:
            row = db._msg_row_to_dict(raw)
            sender = row["sender_username"]
            role = "self" if sender == info["username"] else "other" if sender == user else "unknown"
            if row["type"] == "文本" and isinstance(row["content"], str):
                by_text.setdefault(row["content"].strip(), set()).add(role)
    matched = [(role, by_text[text.strip()]) for role, text in saved if text.strip() in by_text]
    # Exercise the incremental API against known recent rows without sending
    # a test message. An additional live poll distinguishes that from new arrivals.
    cursor = first[min(4, len(first) - 1)]["sort_seq"]
    expected = [row for row in reversed(combined) if row["sort_seq"] > cursor]
    replay = db.get_new_messages(user, since_seq=cursor, limit=20)
    watermark = max(row["sort_seq"] for row in first)
    initial_tail = db.get_new_messages(user, since_seq=watermark, limit=20)
    time.sleep(args.poll_seconds)
    live_tail = db.get_new_messages(user, since_seq=watermark, limit=20)
    checks = {
        "descending_history_order": all(combined[i]["sort_seq"] >= combined[i+1]["sort_seq"]
                                        for i in range(len(combined)-1)),
        "pagination_matches_combined_query": list(map(row_identity, first + older)) == list(map(row_identity, combined)),
        "unique_sample_ids": len({row_identity(row) for row in combined}) == len(combined),
        "both_senders_resolved": {"self", "other"} <= set(roles.values()),
        "known_text_roles_match": bool(matched) and all(role in candidates for role, candidates in matched),
        "incremental_replay_matches": list(map(row_identity, replay)) == list(map(row_identity, expected)),
        "incremental_replay_ascending": all(replay[i]["sort_seq"] <= replay[i+1]["sort_seq"]
                                            for i in range(len(replay)-1)),
        "cursor_excludes_existing_rows": not initial_tail,
    }
    stamps = list(workdir.glob("*.stamp"))
    checks["wal_snapshots_not_degraded"] = all(
        int(path.read_text().split(",")[-1]) >= 0 for path in stamps)
    return {
        "ok": all(checks.values()), **metadata, "checks": checks,
        "diagnostic": diagnostic,
        "recent_count": len(first), "older_page_count": len(older),
        "bounded_total": len(combined), "role_counts": dict(Counter(roles.values())),
        "type_counts": dict(Counter(row["type"] for row in combined)),
        "previous_visual_text_matches": len(matched),
        "incremental_replay_count": len(replay), "live_poll_seconds": args.poll_seconds,
        "live_new_message_count": len(live_tail),
        "live_delivery_observed": bool(live_tail),
        "required_db_key_count": len(db._keys),
        "key_files_created": list(workdir.glob("*keys*.json")) != [],
        "decrypted_cache_bytes": sum(p.stat().st_size for p in workdir.glob("*.db")),
        "duration_seconds": round(time.monotonic() - started, 2),
        "cache_location": "data/readonly-db-probe/cache",
        "ui_modules_loaded": any(name in sys.modules for name in
                                 ["wechatauto.wx", "wechatauto.guia", "wechatauto.uia_driver"]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--contact")
    parser.add_argument("--account-name")
    parser.add_argument("--poll-seconds", type=float, default=3)
    args = parser.parse_args()
    if not args.metadata_only and (not args.contact or not args.account_name):
        parser.error("A confirmed contact and account-name are required")
    if not 0 <= args.poll_seconds <= 30:
        parser.error("poll-seconds must be within 0..30")
    output = ROOT / "data" / "readonly-db-probe" / "result.json"
    suppressed = io.StringIO()
    try:
        with redirect_stdout(suppressed), redirect_stderr(suppressed):
            result = validate(args)
    except Exception as error:
        # No raw exception/traceback or captured upstream output: they can
        # include account paths, message content, or secret material.
        result = {"ok": False, "error_type": type(error).__name__,
                  "stage_error": str(error) if isinstance(error, RuntimeError)
                  and not str(error).startswith("数据库") else "Probe stopped; inspect the failing stage locally"}
        result["error_frames"] = [{"file": Path(frame.filename).name,
                                   "function": frame.name, "line": frame.lineno}
                                  for frame in traceback.extract_tb(error.__traceback__)]
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
