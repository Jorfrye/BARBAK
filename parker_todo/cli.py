"""Command-line entrypoint: read new messages -> extract -> update TODO file."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .config import Config, load_config
from .extract import extract_todos
from .messages import fetch_messages
from .todos import add_todos


def _load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state_file: Path, state: dict) -> None:
    state_file.write_text(json.dumps(state, indent=2))


def run(cfg: Config, dry_run: bool = False, reset: bool = False) -> int:
    state_file = Path(cfg.state_file)
    state = {} if reset else _load_state(state_file)
    since_rowid = int(state.get("last_rowid", 0))

    messages = fetch_messages(
        cfg.resolved_db_path, cfg.contact_identifiers, since_rowid=since_rowid
    )
    if not messages:
        print("No new messages from", cfg.contact_name)
        return 0

    print(f"Found {len(messages)} new message(s) from {cfg.contact_name}.")
    todos = extract_todos(messages, cfg)

    if dry_run:
        print("\n--- DRY RUN (nothing written) ---")
        print("Would consider these TODO items:")
        for t in todos:
            print("  •", t)
        return 0

    added = add_todos(
        Path(cfg.todo_file), todos, cfg.contact_name, date.today().isoformat()
    )
    if added:
        print(f"Added {len(added)} new TODO item(s) to {cfg.todo_file}:")
        for t in added:
            print("  •", t)
    else:
        print("No new TODO items to add.")

    # Advance the watermark to the newest message we saw.
    state["last_rowid"] = max(m.rowid for m in messages)
    _save_state(state_file, state)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read a contact's iMessages/SMS and maintain a TODO list."
    )
    parser.add_argument(
        "-c", "--config", help="Path to config.json (defaults to ./config.json)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without writing the TODO file or state.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Ignore saved state and re-scan the full message history.",
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        print(
            "Copy config.example.json to config.json and fill it in.",
            file=sys.stderr,
        )
        return 2

    try:
        return run(cfg, dry_run=args.dry_run, reset=args.reset)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
