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

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _keyword_extract(messages: list[Message], cfg: Config) -> list[str]:
    triggers = [t.lower() for t in cfg.keyword_triggers]
    todos: list[str] = []
    for msg in messages:
        for sentence in _SENTENCE_SPLIT.split(msg.text):
            s = sentence.strip()
            if not s:
                continue
            low = s.lower()
            if any(t in low for t in triggers):
                todos.append(s.rstrip(".!"))
    return todos


_SYSTEM_PROMPT = (
    "You extract concrete, actionable TODO items from text messages a person "
    "named {name} sends. Only include things that represent a task, commitment, "
    "reminder, or request that someone needs to act on. Ignore greetings, "
    "chit-chat, questions that aren't asking for an action, and anything already "
    "clearly completed. Rewrite each item as a short imperative task (e.g. "
    "'Send the invoice to accounting'). Return STRICT JSON: an object with a "
    "single key \"todos\" whose value is an array of strings. Return an empty "
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
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()
    # The model may wrap JSON in prose or fences; pull out the JSON object.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    todos = parsed.get("todos", [])
    return [str(t).strip() for t in todos if str(t).strip()]


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
