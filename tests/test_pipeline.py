"""End-to-end test against a synthetic chat.db.

Builds a minimal Messages-style SQLite database, then verifies message
reading (including attributedBody decoding and Apple-epoch conversion),
keyword extraction, and TODO file dedup.

Run with:  python -m pytest tests/  (or) python tests/test_pipeline.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parker_todo.config import Config
from parker_todo.extract import extract_todos
from parker_todo.messages import decode_attributed_body, fetch_messages
from parker_todo.todos import add_todos

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _apple_ns(dt: datetime) -> int:
    return int((dt - _APPLE_EPOCH).total_seconds() * 1_000_000_000)


def _make_attributed_body(text: str) -> bytes:
    """Craft a blob shaped like an NSAttributedString typedstream body."""
    raw = text.encode("utf-8")
    if len(raw) < 0x80:
        length_prefix = bytes([len(raw)])
    else:
        length_prefix = b"\x81" + len(raw).to_bytes(2, "little")
    return b"\x04\x0bstreamtyped..NSString\x01\x94" + length_prefix + raw + b"\x86"


def build_fake_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            text TEXT,
            attributedBody BLOB,
            date INTEGER,
            is_from_me INTEGER,
            handle_id INTEGER
        );
        """
    )
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (2, '+19998887777')")

    now_ns = _apple_ns(datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc))
    rows = [
        # Plain text from Parker, actionable.
        (1, "Can you send me the report by Friday?", None, now_ns, 0, 1),
        # attributedBody-only message from Parker, actionable.
        (2, None, _make_attributed_body("Don't forget to book the venue"), now_ns + 1, 0, 1),
        # Chit-chat from Parker, not actionable.
        (3, "haha that was a great game last night", None, now_ns + 2, 0, 1),
        # From me (outgoing) - should be excluded.
        (4, "Sure, please remind me tomorrow", None, now_ns + 3, 1, 1),
        # From a different contact - should be excluded.
        (5, "Please water the plants", None, now_ns + 4, 0, 2),
    ]
    conn.executemany(
        "INSERT INTO message (ROWID, text, attributedBody, date, is_from_me, handle_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_attributed_body_decode():
    blob = _make_attributed_body("Hello world task")
    assert decode_attributed_body(blob) == "Hello world task"
    assert decode_attributed_body(None) is None
    assert decode_attributed_body(b"garbage") is None


def test_fetch_filters_and_decodes(tmp_path: Path):
    db = tmp_path / "chat.db"
    build_fake_db(db)
    msgs = fetch_messages(db, ["+15551234567"], since_rowid=0)
    texts = [m.handle for m in msgs]
    # Only Parker's inbound messages (rows 1, 2, 3), not outgoing or other contact.
    assert all(h == "+15551234567" for h in texts)
    bodies = [m.text for m in msgs]
    assert "Can you send me the report by Friday?" in bodies
    assert "Don't forget to book the venue" in bodies
    assert "Please water the plants" not in bodies  # different contact
    assert not any(m.is_from_me for m in msgs)
    # Date conversion sanity: July 2026.
    assert msgs[0].date.year == 2026 and msgs[0].date.month == 7


def test_since_rowid_watermark(tmp_path: Path):
    db = tmp_path / "chat.db"
    build_fake_db(db)
    msgs = fetch_messages(db, ["+15551234567"], since_rowid=2)
    assert all(m.rowid > 2 for m in msgs)


def test_keyword_extract_and_todo_dedup(tmp_path: Path):
    db = tmp_path / "chat.db"
    build_fake_db(db)
    cfg = Config(contact_identifiers=["+15551234567"], extractor="keywords")
    msgs = fetch_messages(db, cfg.contact_identifiers, since_rowid=0)
    todos = extract_todos(msgs, cfg)
    assert any("report" in t.lower() for t in todos)
    assert any("venue" in t.lower() for t in todos)
    assert not any("great game" in t.lower() for t in todos)

    todo_file = tmp_path / "PARKER_TODO.md"
    added1 = add_todos(todo_file, todos, "Parker Bailey", "2026-07-01")
    assert len(added1) == len(todos) and len(added1) > 0
    # Re-adding the same items must not duplicate.
    added2 = add_todos(todo_file, todos, "Parker Bailey", "2026-07-02")
    assert added2 == []
    content = todo_file.read_text()
    assert content.count("- [ ]") == len(added1)


def _run_all():
    """Minimal runner so the file works without pytest installed."""
    failures = 0
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name, fn in list(globals().items()):
            if not name.startswith("test_"):
                continue
            try:
                if "tmp_path" in fn.__code__.co_varnames:
                    sub = tmp / name
                    sub.mkdir()
                    fn(sub)
                else:
                    fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {e!r}")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
