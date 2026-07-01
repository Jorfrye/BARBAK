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
from pathlib import Path

_ITEM_RE = re.compile(r"^- \[( |x|X)\]\s+(.*?)(?:\s*<!--.*-->)?\s*$")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _existing_normalized(content: str) -> set[str]:
    found: set[str] = set()
    for line in content.splitlines():
        m = _ITEM_RE.match(line.strip())
        if m:
            found.add(_normalize(m.group(2)))
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
        norm = _normalize(todo)
        if not norm or norm in existing or norm in seen_this_run:
            continue
        seen_this_run.add(norm)
        added.append(todo)
        lines_to_add.append(f"- [ ] {todo}  <!-- added {today} -->")

    if lines_to_add:
        if not content.endswith("\n"):
            content += "\n"
        content += "\n".join(lines_to_add) + "\n"
        todo_file.write_text(content)

    return added
