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
    NSAttributedString. The visible message text is stored as an NSString
    immediately after a ``NSString`` class marker, prefixed by a length byte
    (or a 0x81 + uint16 little-endian length for longer strings).
    """
    if not blob:
        return None
    try:
        marker = b"NSString"
        idx = blob.find(marker)
        if idx == -1:
            return None
        # Skip the class name and the following class/version bytes. The layout
        # is: 'NSString' + 0x01 0x94 (version markers) then the length prefix.
        pos = idx + len(marker) + 1
        if pos >= len(blob):
            return None
        # There is typically one filler byte (0x94) before the length.
        if blob[pos] == 0x94:
            pos += 1
        if pos >= len(blob):
            return None
        length_byte = blob[pos]
        pos += 1
        if length_byte == 0x81:
            # Next two bytes are a little-endian uint16 length.
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
        placeholders = ",".join("?" for _ in contact_identifiers)
        from_me_clause = "" if include_from_me else "AND m.is_from_me = 0"
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
              {from_me_clause}
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
