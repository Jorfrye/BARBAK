"""Turn incoming messages into actionable TODO items.

Two backends:
- ``keyword``: pure-Python heuristic, no dependencies or network.
- ``claude``:  uses the Claude API for smarter extraction, falling back to the
  keyword heuristic if the ``anthropic`` package or an API key is unavailable.
"""

from __future__ import annotations

import json
import re

from .config import Config
from .messages import Message

# Split on sentence terminators, but not after common abbreviations, and only
# when the next sentence starts with a capital/quote so "e.g. send it" stays
# whole. Newlines always split.
_ABBREV = r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bvs)"
_SENTENCE_SPLIT = re.compile(_ABBREV + r"(?<=[.!?])\s+(?=[\"'A-Z0-9])|\n+")


def _keyword_extract(messages: list[Message], cfg: Config) -> list[str]:
    triggers = [t.lower() for t in cfg.keyword_triggers]
    todos: list[str] = []
    for msg in messages:
        for sentence in _SENTENCE_SPLIT.split(msg.text):
            if sentence is None:
                continue
            s = sentence.strip()
            if not s:
                continue
            low = s.lower()
            if any(t in low for t in triggers):
                todos.append(s.rstrip(".!? "))
    return todos


def _todos_from_response(text: str) -> list[dict] | None:
    """Parse the ``{"todos": [...]}`` object out of a model response.

    Scans for the first balanced-brace JSON object (ignoring braces inside
    strings) that parses to a dict with a ``todos`` key, so stray braces in the
    model's prose don't break parsing. Each item is normalized to a dict with
    ``task`` and ``type`` ("commitment", "request", or "" when unknown). Plain
    strings are accepted too (type ""). Returns the list (possibly empty) on
    success, or ``None`` if no object could be parsed — the ``None`` signals the
    caller to fall back to keyword extraction.
    """
    for candidate in _iter_json_objects(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "todos" in parsed:
            items: list[dict] = []
            for entry in parsed["todos"]:
                if isinstance(entry, dict):
                    task = str(entry.get("task", "")).strip()
                    kind = str(entry.get("type", "")).strip().lower()
                else:
                    task, kind = str(entry).strip(), ""
                if task:
                    items.append(
                        {"task": task, "type": kind if kind in ("commitment", "request") else ""}
                    )
            return items
    return None


def _render_items(items: list[dict]) -> list[str]:
    """Format classified todos into display strings: Parker's own commitments
    first (plain), then things he asked the reader to do (labeled)."""
    commitments = [i["task"] for i in items if i["type"] != "request"]
    requests = [i["task"] for i in items if i["type"] == "request"]
    return commitments + [f"{task}  — (Parker asked you)" for task in requests]


def _iter_json_objects(text: str):
    """Yield each top-level ``{...}`` substring, respecting strings/escapes."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, in_str, esc, j = 0, False, False, i
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[i : j + 1]
                    break
            j += 1
        i = j + 1


_SYSTEM_PROMPT = (
    "You maintain a to-do list FOR {name}, who tends to forget his own "
    "commitments. Read text messages that {name} sends and extract concrete, "
    "actionable items he needs to remember. PRIORITIZE things {name} himself "
    "said he would do — his promises and commitments (e.g. 'I'll send the "
    "contract Monday', 'I can drop it off tomorrow', 'let me check and get back "
    "to you'). Also capture direct requests {name} makes of the reader. Ignore "
    "greetings, chit-chat, and anything already clearly completed. Rewrite each "
    "item as a short imperative task (e.g. 'Send the contract by Monday'). "
    "Return STRICT JSON: an object with a single key \"todos\" whose value is an "
    "array of objects, each with \"task\" (the imperative task string) and "
    "\"type\" — \"commitment\" if {name} is the one who will do it, or "
    "\"request\" if {name} is asking the reader to do something. Return an empty "
    "array if there are no tasks."
)


def _claude_extract(messages: list[Message], cfg: Config) -> list[str] | None:
    """Return extracted todos, or None if the Claude backend is unavailable."""
    if not cfg.anthropic_api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    transcript = "\n".join(
        f"[{m.date:%Y-%m-%d %H:%M}] {cfg.contact_name}: {m.text}" for m in messages
    )
    try:
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        resp = client.messages.create(
            model=cfg.claude_model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT.format(name=cfg.contact_name),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract the TODO items from these messages:\n\n"
                        + transcript
                    ),
                }
            ],
        )
        text = "".join(
            block.text
            for block in resp.content
            if getattr(block, "type", None) == "text"
        ).strip()
    except Exception:
        # Network/auth/rate-limit/etc.: treat Claude as unavailable so the
        # caller falls back to keyword extraction rather than crashing.
        return None
    # None if the response wasn't parseable (so extract_todos falls back to
    # keywords instead of dropping the batch as "no todos found"); otherwise the
    # classified items rendered commitments-first.
    items = _todos_from_response(text)
    if items is None:
        return None
    return _render_items(items)


def extract_todos(messages: list[Message], cfg: Config) -> list[str]:
    """Extract TODO strings from messages using the configured backend."""
    if not messages:
        return []
    if cfg.extractor == "claude":
        result = _claude_extract(messages, cfg)
        if result is not None:
            return result
        # Fall back silently to keywords if Claude isn't usable.
    return _keyword_extract(messages, cfg)
