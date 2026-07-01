"""Read incoming messages from a macOS Messages (chat.db) database.

Only reads are performed, and the database is opened read-only so it is safe to
run while Messages.app is open.

Notes on chat.db quirks handled here:
- Timestamps (``message.date``) are stored as an offset from the Cocoa epoch
  (2001-01-01 UTC). Recent macOS versions store nanoseconds; older ones store
  seconds. We detect which by magnitude.
- On recent macOS the plain ``message.text`` column is often NULL and the body
  lives in ``message.attributedBody`` as an NSAttributedString typedstream
  blob. We best-effort decode the readable text out of it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 2001-01-01 00:00:00 UTC, the Cocoa/Apple reference date.
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


@dataclass
class Message:
    rowid: int
    handle: str
    text: str
    date: datetime
    is_from_me: bool


def _apple_time_to_datetime(value: int | float | None) -> datetime:
    if not value:
        return _APPLE_EPOCH
    # Nanoseconds since 2001 are ~1e18 today; seconds are ~7e8. Anything past
    # ~1e12 is safely nanoseconds.
    seconds = value / 1_000_000_000 if abs(value) > 1_000_000_000_000 else value
    return _APPLE_EPOCH + timedelta(seconds=seconds)


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Best-effort extraction of the readable text from an attributedBody blob.

    The blob is an NSKeyedArchiver/typedstream serialization of an
    NSAttributedString. After the ``NSString`` class marker the typedstream
    emits class/version bytes and a ``+`` (0x2b) type-encoding char that
    introduces the inline string's byte field, followed by a length prefix and
    then the UTF-8 bytes, e.g. ``NSString \x01\x94 \x84\x01 + <len> <utf8>``.

    We locate that ``+`` marker rather than assuming a fixed offset (real blobs
    vary), then read the length. The length prefix is a single byte for strings
    shorter than 128 bytes; otherwise a 0x81 marker means a uint16 (2-byte)
    little-endian length follows and 0x82 means a uint32 (4-byte) length.
    """
    if not blob:
        return None

    def _read(start: int) -> str | None:
        pos = start
        if pos >= len(blob):
            return None
        length_byte = blob[pos]
        pos += 1
        if length_byte == 0x81:
            if pos + 2 > len(blob):
                return None
            length = int.from_bytes(blob[pos : pos + 2], "little")
            pos += 2
        elif length_byte == 0x82:
            if pos + 4 > len(blob):
                return None
            length = int.from_bytes(blob[pos : pos + 4], "little")
            pos += 4
        else:
            length = length_byte
        raw = blob[pos : pos + length]
        text = raw.decode("utf-8", errors="replace").strip()
        return text or None

    try:
        marker = b"NSString"
        idx = blob.find(marker)
        if idx == -1:
            return None
        # Preferred: read the length immediately after the '+' (0x2b) type char.
        plus = blob.find(b"\x2b", idx + len(marker))
        if plus != -1:
            text = _read(plus + 1)
            if text:
                return text
        # Fallback for blobs that don't carry the '+' marker: skip the class
        # name plus a version byte and an optional 0x94 filler, then read.
        pos = idx + len(marker) + 1
        if pos < len(blob) and blob[pos] == 0x94:
            pos += 1
        return _read(pos)
    except Exception:
        return None


def _message_text(text: str | None, attributed: bytes | None) -> str | None:
    if text and text.strip():
        return text.strip()
    return decode_attributed_body(attributed)


def fetch_messages(
    db_path: Path,
    contact_identifiers: list[str],
    since_rowid: int = 0,
    include_from_me: bool = False,
) -> list[Message]:
    """Return messages from the given contact identifiers with ROWID > since_rowid.

    Results are ordered oldest-first so callers can advance ``since_rowid``
    monotonically.
    """
    if not contact_identifiers:
        raise ValueError(
            "No contact_identifiers configured; set the phone number(s)/email(s) "
            "that the contact texts from."
        )
    if not db_path.exists():
        raise FileNotFoundError(
            f"Messages database not found at {db_path}. On macOS this is "
            "~/Library/Messages/chat.db and the running process needs Full Disk "
            "Access."
        )

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(message)")
        }
        placeholders = ",".join("?" for _ in contact_identifiers)
        from_me_clause = "" if include_from_me else "AND m.is_from_me = 0"

        # Exclude tapbacks/reactions (Loved/Liked/Emphasized/...), which are
        # stored as real message rows with associated_message_type != 0 and a
        # synthetic text like 'Loved "..."'. Also drop unsent/retracted
        # messages. Both columns are probed for so older schemas still work.
        extra_clauses = ""
        if "associated_message_type" in columns:
            extra_clauses += (
                "\n              AND (m.associated_message_type = 0 "
                "OR m.associated_message_type IS NULL)"
            )
        if "date_retracted" in columns:
            # Non-retracted messages have date_retracted = 0 (the schema
            # default); a retracted/unsent message gets a nonzero timestamp.
            extra_clauses += (
                "\n              AND (m.date_retracted = 0 "
                "OR m.date_retracted IS NULL)"
            )

        sql = f"""
            SELECT m.ROWID       AS rowid,
                   m.text        AS text,
                   m.attributedBody AS attributed,
                   m.date        AS date,
                   m.is_from_me  AS is_from_me,
                   h.id          AS handle
            FROM message AS m
            JOIN handle AS h ON m.handle_id = h.ROWID
            WHERE h.id IN ({placeholders})
              AND m.ROWID > ?
              {from_me_clause}{extra_clauses}
            ORDER BY m.ROWID ASC
        """
        params = [*contact_identifiers, since_rowid]
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    messages: list[Message] = []
    for row in rows:
        body = _message_text(row["text"], row["attributed"])
        if not body:
            continue
        messages.append(
            Message(
                rowid=row["rowid"],
                handle=row["handle"],
                text=body,
                date=_apple_time_to_datetime(row["date"]),
                is_from_me=bool(row["is_from_me"]),
            )
        )
    return messages
