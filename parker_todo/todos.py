"""Maintain the Markdown TODO checklist.

The file looks like:

    # Parker Bailey - TODO

    - [ ] Send the invoice to accounting  <!-- added 2026-07-01 -->
    - [x] Book the venue  <!-- added 2026-06-28 -->

New items are appended; existing items (open or done) are never duplicated. We
compare on a normalized form of the task text so trivially different phrasings
of the same task still dedupe.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Match a checklist item and capture its task text. The trailing-comment group
# is anchored to the specific "  <!-- added ... -->" suffix this module emits,
# so task text that legitimately contains HTML-comment-like syntax is preserved.
_ITEM_RE = re.compile(r"^- \[( |x|X)\]\s+(.*?)(?:\s+<!-- added [^>]*-->)?\s*$")


def _normalize(text: str) -> str:
    """Unicode-aware normalized form for dedup (keeps letters from any script)."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()


def _dedup_key(text: str) -> str:
    """Key used to detect duplicates. Falls back to the raw (whitespace-
    collapsed) text for todos that normalize to empty (punctuation/emoji-only),
    so distinct symbol-only tasks don't all collide on the empty string."""
    norm = _normalize(text)
    return norm if norm else " ".join(text.split()).casefold()


def _existing_normalized(content: str) -> set[str]:
    found: set[str] = set()
    for line in content.splitlines():
        m = _ITEM_RE.match(line.strip())
        if m:
            found.add(_dedup_key(m.group(2)))
    return found


def add_todos(todo_file: Path, new_todos: list[str], contact_name: str, today: str) -> list[str]:
    """Append genuinely-new todos to the file. Returns the ones actually added."""
    if todo_file.exists():
        content = todo_file.read_text()
    else:
        content = f"# {contact_name} - TODO\n\n"

    existing = _existing_normalized(content)
    added: list[str] = []
    seen_this_run: set[str] = set()

    lines_to_add: list[str] = []
    for todo in new_todos:
        # Collapse any internal newlines/whitespace so each todo is exactly one
        # line in the file (a stray newline would corrupt the checklist and
        # defeat dedup).
        todo = " ".join(todo.split())
        if not todo:
            continue
        key = _dedup_key(todo)
        if key in existing or key in seen_this_run:
            continue
        seen_this_run.add(key)
        added.append(todo)
        lines_to_add.append(f"- [ ] {todo}  <!-- added {today} -->")

    if lines_to_add:
        if not content.endswith("\n"):
            content += "\n"
        content += "\n".join(lines_to_add) + "\n"
        todo_file.write_text(content)

    return added
