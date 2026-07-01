"""Configuration loading for the Parker TODO tracker.

Configuration is read from a JSON file (default: ``config.json`` next to the
project root). Any value may also be overridden by an environment variable so
secrets like the Claude API key never have to live on disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Default location of the macOS Messages database.
DEFAULT_CHAT_DB = "~/Library/Messages/chat.db"


@dataclass
class Config:
    # One or more identifiers (phone numbers / emails) that Parker texts from.
    # chat.db keys messages by handle, not by contact name, so you must list the
    # actual number(s)/email(s). Phone numbers should be in E.164 form, e.g.
    # "+15551234567".
    contact_identifiers: list[str] = field(default_factory=list)

    # Friendly name used in the TODO file heading and prompts.
    contact_name: str = "Parker Bailey"

    # Path to the Messages SQLite database.
    chat_db_path: str = DEFAULT_CHAT_DB

    # Where the Markdown TODO checklist is written.
    todo_file: str = "PARKER_TODO.md"

    # Where we remember the last processed message so we never double-count.
    state_file: str = ".parker_todo_state.json"

    # Extraction backend: "claude" (falls back to keywords if no API key) or
    # "keywords".
    extractor: str = "claude"

    # Claude model used when extractor == "claude".
    claude_model: str = "claude-sonnet-5"

    # API key for the Claude API. Prefer the ANTHROPIC_API_KEY env var.
    anthropic_api_key: str | None = None

    # Trigger phrases for the keyword extractor (case-insensitive substring).
    keyword_triggers: list[str] = field(
        default_factory=lambda: [
            "need to",
            "needs to",
            "don't forget",
            "dont forget",
            "remember to",
            "remind me",
            "can you",
            "could you",
            "please",
            "make sure",
            "have to",
            "gotta",
            "todo",
            "to do",
            "follow up",
            "get back to",
        ]
    )

    # Action verbs the keyword extractor looks for. A "weak" trigger (like
    # "have to") only counts as a task when the sentence also contains one of
    # these, which filters out banter such as "I have to be a Virgo man".
    action_verbs: list[str] = field(
        default_factory=lambda: [
            "send", "call", "text", "email", "message", "bring", "take", "get",
            "grab", "pick", "drop", "pay", "book", "buy", "order", "return",
            "check", "remind", "schedule", "fix", "finish", "sign", "submit",
            "forward", "share", "confirm", "cancel", "deliver", "mail", "ship",
            "meet", "add", "update", "review", "print", "deposit", "renew",
            "download", "upload", "reply", "respond", "watch", "make", "give",
            "set up", "reach out", "look into", "sort out", "wrap up",
        ]
    )

    # Regexes (case-insensitive) for lines to drop as noise even if a trigger
    # matched. Empty by default; add patterns for junk that keeps slipping in.
    noise_patterns: list[str] = field(default_factory=list)

    # Similarity threshold (0-1) for treating two todos as the same thing.
    # Higher = stricter (fewer merges). Used to catch repeats/rephrasings.
    dedup_threshold: float = 0.7

    @property
    def resolved_db_path(self) -> Path:
        return Path(os.path.expanduser(self.chat_db_path))


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load configuration from ``path`` (JSON), applying env overrides."""
    data: dict = {}
    if path is None:
        # Look for config.json in CWD; fall back to bundled defaults.
        candidate = Path("config.json")
        if candidate.exists():
            path = candidate
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        data = json.loads(p.read_text())

    cfg = Config(**{k: v for k, v in data.items() if k in Config.__annotations__})

    # Environment overrides (take precedence over the file).
    cfg.anthropic_api_key = (
        os.environ.get("ANTHROPIC_API_KEY") or cfg.anthropic_api_key
    )
    if os.environ.get("PARKER_CHAT_DB"):
        cfg.chat_db_path = os.environ["PARKER_CHAT_DB"]
    if os.environ.get("PARKER_CONTACT_IDS"):
        cfg.contact_identifiers = [
            s.strip()
            for s in os.environ["PARKER_CONTACT_IDS"].split(",")
            if s.strip()
        ]

    return cfg
