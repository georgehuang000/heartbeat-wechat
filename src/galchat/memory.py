"""Contact-scoped, local conversation memory; never treats a choice as a sent message."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata


def _terms(text: str) -> set[str]:
    """Dependency-free retrieval tokens, including Chinese bigrams."""
    terms = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(part[i : i + 2] for i in range(len(part) - 1))
    return terms


def _timestamp_value(value: str) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (ValueError, OverflowError):
        return None


class MemoryStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('self','other')),
                text TEXT NOT NULL, timestamp TEXT NOT NULL, source TEXT NOT NULL,
                sort_time REAL NOT NULL,
                UNIQUE(contact_id, id)
            );
            CREATE INDEX IF NOT EXISTS messages_contact_time
                ON messages(contact_id, sort_time, seq);
            CREATE TABLE IF NOT EXISTS choices (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                text TEXT NOT NULL, label TEXT NOT NULL, selected_at TEXT NOT NULL
            );
        """)
        if 'metadata' not in {r[1] for r in self._conn.execute('PRAGMA table_info(messages)')}:
            self._conn.execute("ALTER TABLE messages ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @staticmethod
    def _contact_id(contact_id: str) -> str:
        if not isinstance(contact_id, str) or not contact_id.strip():
            raise ValueError("联系人 ID 不能为空。")
        return contact_id.strip()

    def upsert_messages(self, contact_id: str, contact_name: str, messages: list[dict]) -> int:
        contact_id = self._contact_id(contact_id)
        if not isinstance(messages, list):
            raise ValueError("聊天记录需要是消息列表。")
        now = datetime.now(timezone.utc)
        normalized = []
        occurrences: Counter[str] = Counter()
        for index, item in enumerate(messages, 1):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index} 条消息不是对象。")
            role, text = item.get("role"), item.get("text")
            if role not in ("self", "other"):
                raise ValueError(f"第 {index} 条消息的 role 应为 self 或 other。")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"第 {index} 条消息缺少有效文字。")
            text = text.strip()
            if len(text) > 100_000:
                raise ValueError(f"第 {index} 条消息超过 10 万字，请拆分后导入。")
            timestamp = str(item.get("timestamp") or "")
            source = str(item.get("source") or "manual")
            message_id = str(item.get("id") or "")
            if not message_id:
                fingerprint = json.dumps([role, text, timestamp, source], ensure_ascii=False)
                occurrences[fingerprint] += 1
                # Preserve repeated messages within an import, while making re-import idempotent.
                message_id = hashlib.sha256(
                    f"{fingerprint}:{occurrences[fingerprint]}".encode("utf-8")
                ).hexdigest()
            sort_time = _timestamp_value(timestamp)
            normalized.append((contact_id, message_id, role, text, timestamp, source,
                               sort_time if sort_time is not None else now.timestamp(),
                               json.dumps({k:item[k] for k in ('kind','content_known','content_source','attachment','duration_seconds',
                                           'participant_id','sender_display_name','conversation_kind')
                                           if k in item}, ensure_ascii=False)))
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO contacts VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE "
                "SET name=excluded.name, updated_at=excluded.updated_at",
                (contact_id, contact_name.strip() or contact_id, now.isoformat()),
            )
            before = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE contact_id=?", (contact_id,)
            ).fetchone()[0]
            self._conn.executemany(
                "INSERT INTO messages (contact_id,id,role,text,timestamp,source,sort_time,metadata) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(contact_id,id) DO UPDATE SET "
                "role=excluded.role,text=excluded.text,timestamp=excluded.timestamp,"
                "source=excluded.source,metadata=excluded.metadata,sort_time=CASE WHEN excluded.timestamp<>'' "
                "THEN excluded.sort_time ELSE messages.sort_time END", normalized,
            )
            after = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE contact_id=?", (contact_id,)
            ).fetchone()[0]
        return after - before

    def list_contacts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id,c.name,COUNT(m.seq) AS message_count FROM contacts c "
                "LEFT JOIN messages m ON m.contact_id=c.id GROUP BY c.id "
                "ORDER BY c.updated_at DESC,c.name"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_context(self, contact_id: str, query: str = "", limit: int = 30) -> dict:
        contact_id = self._contact_id(contact_id)
        limit = max(1, min(int(limit), 5000))
        with self._lock:
            contact = self._conn.execute("SELECT name FROM contacts WHERE id=?", (contact_id,)).fetchone()
            # A bounded scan keeps large imports responsive. Counts still describe all stored data.
            rows = list(reversed(self._conn.execute(
                "SELECT id,role,text,timestamp,source,metadata FROM messages WHERE contact_id=? "
                "ORDER BY sort_time DESC,seq DESC LIMIT 5000", (contact_id,)
            ).fetchall()))
            role_counts = dict(self._conn.execute(
                "SELECT role,COUNT(*) FROM messages WHERE contact_id=? GROUP BY role", (contact_id,)
            ).fetchall())
            selected = self._conn.execute(
                "SELECT label,COUNT(*) AS n FROM choices WHERE contact_id=? "
                "GROUP BY label ORDER BY n DESC,label LIMIT 5", (contact_id,)
            ).fetchall()
            selected_count = self._conn.execute(
                "SELECT COUNT(*) FROM choices WHERE contact_id=?", (contact_id,)
            ).fetchone()[0]
            bounds = self._conn.execute(
                "SELECT MIN(CASE WHEN timestamp<>'' THEN timestamp END),"
                "MAX(CASE WHEN timestamp<>'' THEN timestamp END) FROM messages WHERE contact_id=?",
                (contact_id,),
            ).fetchone()
        messages = []
        for row in rows:
            item = dict(row)
            metadata = json.loads(item.pop('metadata', '{}'))
            item.update({k:metadata[k] for k in ('kind','content_known','content_source','attachment','duration_seconds',
                'participant_id','sender_display_name','conversation_kind') if k in metadata})
            if 'kind' not in item:
                legacy = {'[图片]':'image','[语音]':'voice','[表情]':'emoji','[其他消息]':'other'}
                item['kind'] = legacy.get(item['text'], 'text')
                item['content_known'] = item['kind'] == 'text'
            messages.append(item)
        recent, older = messages[-limit:], messages[:-limit]
        if not query:
            query = " ".join(m["text"] for m in recent if m["role"] == "other")[-300:]
        query_terms = _terms(query)
        doc_terms = [_terms(m["text"]) for m in older]
        frequency: Counter[str] = Counter(term for terms in doc_terms for term in terms)
        scored = []
        for index, (message, terms) in enumerate(zip(older, doc_terms)):
            matched = query_terms & terms
            if matched:
                # Rare words favor specific shared topics over common conversational phrasing.
                score = sum(1 / (1 + frequency[t]) for t in matched)
                scored.append((score, index, message))
        best = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[:8]
        relevant = [item[2] for item in sorted(best, key=lambda item: item[1])]
        self_texts = [m["text"] for m in messages if m["role"] == "self"]
        emoji = Counter(
            ch for text in self_texts for ch in text
            if unicodedata.category(ch) == "So" and ord(ch) > 255
        )
        habitual = ("哈哈", "哈哈哈", "笑死", "好耶", "嘿嘿", "确实", "好嘞", "好呀", "救命", "呜呜", "芜湖")
        phrases = Counter({p: sum(t.count(p) for t in self_texts) for p in habitual})
        return {
            "contact_id": contact_id,
            "contact_name": contact["name"] if contact else contact_id,
            "recent": recent,
            "relevant": relevant,
            "style_summary": {
                "self_sample_count": len(self_texts),
                "average_length": round(sum(map(len, self_texts)) / len(self_texts), 1) if self_texts else 0,
                "short_message_ratio": round(sum(len(t) <= 20 for t in self_texts) / len(self_texts), 2) if self_texts else 0,
                "common_phrases": [p for p, n in phrases.most_common(5) if n >= 2],
                "emoji_examples": [e for e, _n in emoji.most_common(5)],
                "preferred_branches": [{"label": r["label"], "count": r["n"]} for r in selected],
                "note": "习惯仅供参考；已选选项不代表已发送，不据此推断双方关系。",
            },
            "counts": {
                "messages": sum(role_counts.values()), "self": role_counts.get("self", 0),
                "other": role_counts.get("other", 0), "choices": selected_count,
                "retrieval_candidates": len(older),
            },
            "history_range": {
                "first_timestamp": bounds[0], "last_timestamp": bounds[1],
                "sources": sorted({m["source"] for m in messages}),
                "coverage": "仅表示已读取或导入的记录，不代表完整微信历史。",
            },
        }

    def import_text(self, contact_id: str, contact_name: str, text: str) -> int:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("请粘贴带有「我：」「对方：」的对话，或 JSON 消息数组。")
        clean = text.lstrip("\ufeff \r\n\t")
        if clean.startswith("[") or clean.startswith("{"):
            try:
                messages = json.loads(clean)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON 格式有误：第 {exc.lineno} 行，第 {exc.colno} 列。") from exc
            if not isinstance(messages, list):
                raise ValueError("JSON 顶层需要是消息数组，每条包含 role 和 text。")
            imported = [dict(item, source=item.get("source") or "import-json")
                        if isinstance(item, dict) else item for item in messages]
        else:
            imported = []
            for line_no, line in enumerate(clean.splitlines(), 1):
                if not line.strip():
                    continue
                match = re.match(r"^\s*(我|对方|self|other)\s*[:：]\s*(.*)$", line)
                if match:
                    imported.append({"role": "self" if match[1] in ("我", "self") else "other",
                                     "text": match[2], "source": "import-text"})
                elif imported and (line.startswith(" ") or line.startswith("\t")):
                    imported[-1]["text"] += "\n" + line.strip()
                else:
                    raise ValueError(f"第 {line_no} 行无法识别发言人，请用「我：」或「对方：」开头；续行请缩进。")
        return self.upsert_messages(contact_id, contact_name, imported)

    def record_choice(self, contact_id: str, text: str, choice_label: str) -> None:
        contact_id = self._contact_id(contact_id)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("不能记录空选项。")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO contacts VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
                (contact_id, contact_id, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.execute("INSERT INTO choices(contact_id,text,label,selected_at) VALUES (?,?,?,?)",
                               (contact_id, text.strip(), choice_label.strip(), datetime.now(timezone.utc).isoformat()))
