"""End-to-end and regression tests against a synthetic chat.db.

Builds a minimal Messages-style SQLite database, then verifies message
reading (including attributedBody decoding, Apple-epoch conversion, and
reaction/tapback filtering), keyword extraction, Claude-response JSON parsing,
and TODO file dedup.

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
from parker_todo.extract import _render_items, _todos_from_response, extract_todos
from parker_todo.messages import Message, decode_attributed_body, fetch_messages
from parker_todo.todos import add_todos

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _apple_ns(dt: datetime) -> int:
    return int((dt - _APPLE_EPOCH).total_seconds() * 1_000_000_000)


def _length_prefix(raw: bytes) -> bytes:
    if len(raw) < 0x80:
        return bytes([len(raw)])
    return b"\x81" + len(raw).to_bytes(2, "little")


def _make_attributed_body(text: str) -> bytes:
    """Realistic NSAttributedString typedstream body: the string is introduced
    by a '+' (0x2b) type marker after the NSString class name."""
    raw = text.encode("utf-8")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
        b"NSString\x01\x94\x84\x01\x2b" + _length_prefix(raw) + raw + b"\x86\x84"
    )


def _make_attributed_body_legacy(text: str) -> bytes:
    """Older-style body without the '+' marker (exercises the fallback path)."""
    raw = text.encode("utf-8")
    return b"\x04\x0bstreamtyped..NSString\x01\x94" + _length_prefix(raw) + raw + b"\x86"


def build_fake_db(path: Path, legacy_schema: bool = False) -> None:
    conn = sqlite3.connect(path)
    if legacy_schema:
        # Older schema lacking associated_message_type / date_retracted.
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
    else:
        conn.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                text TEXT,
                attributedBody BLOB,
                date INTEGER,
                is_from_me INTEGER,
                handle_id INTEGER,
                associated_message_type INTEGER DEFAULT 0,
                date_retracted INTEGER DEFAULT 0
            );
            """
        )
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (2, '+19998887777')")

    now_ns = _apple_ns(datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc))
    if legacy_schema:
        cols = "ROWID, text, attributedBody, date, is_from_me, handle_id"
        rows = [
            (1, "Can you send me the report by Friday?", None, now_ns, 0, 1),
            (2, None, _make_attributed_body_legacy("Book the venue"), now_ns + 1, 0, 1),
        ]
    else:
        cols = (
            "ROWID, text, attributedBody, date, is_from_me, handle_id, "
            "associated_message_type, date_retracted"
        )
        rows = [
            # Plain text from Parker, actionable.
            (1, "Can you send me the report by Friday?", None, now_ns, 0, 1, 0, 0),
            # attributedBody-only message from Parker, actionable.
            (2, None, _make_attributed_body("Don't forget to book the venue"), now_ns + 1, 0, 1, 0, 0),
            # Chit-chat from Parker, not actionable.
            (3, "haha that was a great game last night", None, now_ns + 2, 0, 1, 0, 0),
            # From me (outgoing) - should be excluded.
            (4, "Sure, please remind me tomorrow", None, now_ns + 3, 1, 1, 0, 0),
            # From a different contact - should be excluded.
            (5, "Please water the plants", None, now_ns + 4, 0, 2, 0, 0),
            # A reaction/tapback from Parker - should be excluded.
            (6, 'Loved "Can you send me the report by Friday?"', None, now_ns + 5, 0, 1, 2000, 0),
            # A retracted/unsent message from Parker - should be excluded.
            (7, "please ignore this, sent by mistake", None, now_ns + 6, 0, 1, 0, now_ns + 6),
        ]
    placeholders = ",".join("?" for _ in cols.split(","))
    conn.executemany(
        f"INSERT INTO message ({cols}) VALUES ({placeholders})", rows
    )
    conn.commit()
    conn.close()


def _msg(text: str) -> Message:
    return Message(rowid=1, handle="+1", text=text,
                   date=datetime(2026, 7, 1, tzinfo=timezone.utc), is_from_me=False)


# --- attributedBody decoding -------------------------------------------------

def test_attributed_body_decode():
    assert decode_attributed_body(_make_attributed_body("Hello world task")) == "Hello world task"
    assert decode_attributed_body(_make_attributed_body_legacy("Legacy task")) == "Legacy task"
    # A long string uses the 0x81 + uint16 length prefix.
    long_text = "Remember to " + "x" * 200
    assert decode_attributed_body(_make_attributed_body(long_text)) == long_text
    assert decode_attributed_body(None) is None
    assert decode_attributed_body(b"garbage") is None


# --- message fetching --------------------------------------------------------

def test_fetch_filters_and_decodes(tmp_path: Path):
    db = tmp_path / "chat.db"
    build_fake_db(db)
    msgs = fetch_messages(db, ["+15551234567"], since_rowid=0)
    bodies = [m.text for m in msgs]
    assert "Can you send me the report by Friday?" in bodies
    assert "Don't forget to book the venue" in bodies  # attributedBody decoded
    assert "Please water the plants" not in bodies      # different contact
    assert not any(m.is_from_me for m in msgs)           # outgoing excluded
    assert not any(b.startswith("Loved") for b in bodies)  # reaction excluded
    assert not any("sent by mistake" in b for b in bodies)  # retracted excluded
    assert all(m.handle == "+15551234567" for m in msgs)
    assert msgs[0].date.year == 2026 and msgs[0].date.month == 7


def test_fetch_legacy_schema_still_works(tmp_path: Path):
    db = tmp_path / "chat.db"
    build_fake_db(db, legacy_schema=True)
    msgs = fetch_messages(db, ["+15551234567"], since_rowid=0)
    bodies = [m.text for m in msgs]
    assert "Can you send me the report by Friday?" in bodies
    assert "Book the venue" in bodies


def test_since_rowid_watermark(tmp_path: Path):
    db = tmp_path / "chat.db"
    build_fake_db(db)
    msgs = fetch_messages(db, ["+15551234567"], since_rowid=2)
    assert all(m.rowid > 2 for m in msgs)


# --- keyword extraction ------------------------------------------------------

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
    added2 = add_todos(todo_file, todos, "Parker Bailey", "2026-07-02")
    assert added2 == []  # re-adding must not duplicate
    assert todo_file.read_text().count("- [ ]") == len(added1)


def test_keyword_strips_trailing_question_mark():
    cfg = Config(extractor="keywords")
    todos = extract_todos([_msg("Can you send the report?")], cfg)
    assert todos == ["Can you send the report"]


def test_keyword_does_not_split_on_abbreviation():
    cfg = Config(extractor="keywords")
    todos = extract_todos([_msg("Can you e.g. send the blue folder")], cfg)
    assert todos == ["Can you e.g. send the blue folder"]


# --- Claude response parsing -------------------------------------------------

def test_json_parse_with_prose_braces():
    # Backward-compatible with plain-string items (type defaults to "").
    text = 'Sure! The format is {"key": value}. Here: {"todos": ["Pay invoice"]}'
    assert _todos_from_response(text) == [{"task": "Pay invoice", "type": ""}]


def test_json_parse_trailing_prose_braces():
    text = '{"todos": ["Call Bob"]}\n\nLet me know if you want me to adjust {these}.'
    assert _todos_from_response(text) == [{"task": "Call Bob", "type": ""}]


def test_json_parse_empty_todos_is_empty_list_not_none():
    assert _todos_from_response('{"todos": []}') == []


def test_json_parse_unparseable_returns_none():
    # No JSON object at all -> None so the caller falls back to keywords.
    assert _todos_from_response("I could not find any tasks, sorry.") is None
    assert _todos_from_response('{"todos": [') is None  # truncated


def test_json_parse_classifies_commitment_and_request():
    text = (
        '{"todos": ['
        '{"task": "Send the contract Monday", "type": "commitment"}, '
        '{"task": "Forward the vendor email", "type": "request"}]}'
    )
    assert _todos_from_response(text) == [
        {"task": "Send the contract Monday", "type": "commitment"},
        {"task": "Forward the vendor email", "type": "request"},
    ]


def test_render_items_puts_commitments_first_and_labels_requests():
    items = [
        {"task": "Forward the vendor email", "type": "request"},
        {"task": "Send the contract Monday", "type": "commitment"},
        {"task": "Call the office", "type": ""},
    ]
    rendered = _render_items(items)
    # Commitments/unknown first (plain), requests last (labeled).
    assert rendered == [
        "Send the contract Monday",
        "Call the office",
        "Forward the vendor email  — (Parker asked you)",
    ]


# --- TODO file robustness ----------------------------------------------------

def test_todo_with_newline_is_collapsed_and_dedups(tmp_path: Path):
    f = tmp_path / "T.md"
    added1 = add_todos(f, ["Call the vendor about\nthe overdue shipment"], "P", "2026-07-01")
    assert added1 == ["Call the vendor about the overdue shipment"]
    # File has exactly one checklist line and no stray lines.
    assert f.read_text().count("- [ ]") == 1
    # Re-adding the same multi-line todo must dedup.
    added2 = add_todos(f, ["Call the vendor about\nthe overdue shipment"], "P", "2026-07-02")
    assert added2 == []


def test_todo_with_embedded_comment_dedups(tmp_path: Path):
    f = tmp_path / "T.md"
    todo = "Fix the <!-- TODO --> marker in code"
    add_todos(f, [todo], "P", "2026-07-01")
    added2 = add_todos(f, [todo], "P", "2026-07-02")
    assert added2 == []
    assert f.read_text().count("- [ ]") == 1


def test_todo_unicode_preserved_and_distinct(tmp_path: Path):
    f = tmp_path / "T.md"
    added = add_todos(f, ["买牛奶", "买鸡蛋"], "P", "2026-07-01")  # buy milk / buy eggs
    assert added == ["买牛奶", "买鸡蛋"]  # both distinct tasks kept
    assert f.read_text().count("- [ ]") == 2


def test_todo_symbol_only_preserved(tmp_path: Path):
    f = tmp_path / "T.md"
    added = add_todos(f, ["!!!", "Buy milk"], "P", "2026-07-01")
    assert "!!!" in added and "Buy milk" in added


def test_todo_distinct_punctuation_tasks_not_merged(tmp_path: Path):
    f = tmp_path / "T.md"
    added = add_todos(f, ["Call John @ 5", "Call John @ 6"], "P", "2026-07-01")
    assert len(added) == 2


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
