# BARBAK

barbak company account

## Parker Bailey TODO tracker

A small program that reads text messages from a specific contact
("Parker Bailey") and keeps a TODO checklist up to date so nothing he
mentions gets forgotten.

### How it can (and can't) read your texts

iOS does **not** let any program read the Messages app directly — there is no
API on the iPhone itself (short of jailbreaking). The reliable, supported way
to read your iPhone's texts on a schedule is from a **Mac** that is signed into
the same Apple ID:

- Turn on **Messages in iCloud** (or **Text Message Forwarding** for green-bubble
  SMS) so Parker's messages appear in Messages.app on the Mac.
- macOS stores those messages in a local SQLite database at
  `~/Library/Messages/chat.db`. This program reads that database **read-only**.

If you don't have a Mac, full automation isn't possible; the practical
alternative is an iOS Shortcut you tap to forward a message into a tracker.
This project implements the Mac path.

### What it does

1. Reads new inbound messages from Parker (matched by phone number / email).
2. Extracts actionable TODO items from them — via the **Claude API** (smart) or
   a **keyword heuristic** (no dependencies, no network). The Claude extractor
   prioritizes things **Parker himself committed to do** (the stuff he forgets)
   and lists them first; things he asks *you* to do are added afterward, labeled
   `— (Parker asked you)`.
3. Appends new items to a Markdown checklist (`PARKER_TODO.md`), de-duplicating
   against what's already there. You tick items off with `[x]` yourself.
4. Remembers the last message it processed so it never double-counts.

### Setup (on the Mac)

1. **Grant Full Disk Access** to whatever runs the script (Terminal, or your
   Python binary): System Settings → Privacy & Security → Full Disk Access.
   Without this, macOS blocks reading `chat.db`.

2. **Configure it.** Copy the example config and fill in Parker's number(s):

   ```sh
   cp config.example.json config.json
   ```

   Set `contact_identifiers` to the exact phone number(s) in E.164 form
   (e.g. `+15551234567`) and/or email(s) Parker texts from. `chat.db` keys
   messages by handle, not by contact name, so the number must match. To see
   the handles in your database:

   ```sh
   sqlite3 ~/Library/Messages/chat.db "SELECT DISTINCT id FROM handle;"
   ```

3. **Pick an extractor** in `config.json`:
   - `"extractor": "claude"` — the smart mode; see "AI mode" below.
   - `"extractor": "keywords"` — pure standard library, nothing to install. A
     good way to try things out with zero setup.

### AI mode (smarter extraction)

The Claude extractor is better at spotting Parker's actual commitments and
classifying commitments vs. requests. To enable it:

1. Get an API key from <https://console.anthropic.com> (Settings → API Keys).
2. Install the client and set the key, then run:

   ```sh
   pip3 install -r requirements.txt
   export ANTHROPIC_API_KEY="sk-ant-...your key..."
   python3 -m parker_todo
   ```

Make sure `config.json` has `"extractor": "claude"` (it does by default). If the
key or package is ever missing, it falls back to keyword mode automatically
rather than failing. The model is set by `claude_model` (default
`claude-sonnet-5`).

### Run it

```sh
python -m parker_todo                 # process new messages, update the list
python -m parker_todo --dry-run       # show what it would add, write nothing
python -m parker_todo --reset         # re-scan full history (ignore watermark)
python -m parker_todo -c config.json  # explicit config path
```

### Run it automatically (every 15 minutes)

Use the bundled installer — it detects all paths for you and sets up a
`launchd` job on the Mac:

```sh
scripts/schedule.sh on            # run every 15 minutes (default)
scripts/schedule.sh on 3600       # or pick an interval in seconds (hourly here)
scripts/schedule.sh status        # see whether it's installed + the python path
scripts/schedule.sh off           # stop running automatically
```

If `ANTHROPIC_API_KEY` is exported in your shell when you run `on`, the
scheduled runs use AI mode; otherwise they use keyword mode. Output is logged to
`schedule.log` in the repo.

**Full Disk Access for scheduled runs:** the job runs as your `python3`, so
grant Full Disk Access to *that binary* (System Settings → Privacy & Security →
Full Disk Access → ➕), not just to Terminal. `scripts/schedule.sh status`
prints its exact path.

### Configuration reference (`config.json`)

| Key | Meaning |
| --- | --- |
| `contact_name` | Display name used in headings/prompts. |
| `contact_identifiers` | List of phone numbers / emails Parker texts from. |
| `chat_db_path` | Path to the Messages DB (default `~/Library/Messages/chat.db`). |
| `todo_file` | Output Markdown checklist. |
| `state_file` | Where the last-processed message id is stored. |
| `extractor` | `"claude"` or `"keywords"`. |
| `claude_model` | Model for the Claude extractor. |
| `keyword_triggers` | Phrases that flag a sentence as a task (keyword mode). |
| `action_verbs` | Verbs that qualify a weak trigger as a real task (keyword mode). |
| `noise_patterns` | Regexes for lines to drop as junk (both modes). |
| `dedup_threshold` | 0–1 similarity for treating two todos as the same repeat. |

`config.json`, the state file, and `PARKER_TODO.md` are gitignored so your
personal data stays local.

### Cutting noise and catching repeats

- **Junk filtering.** In keyword mode, "weak" triggers like *have to* / *need to*
  only count as a task when the sentence also contains an **action verb** (send,
  call, take, …), so banter like *"I have to be a Virgo man"* is ignored. Direct
  asks (*can you*, *please*, *don't forget*, …) always count.
- **`noise_patterns`.** For anything specific that still slips through, add a
  regex (case-insensitive) to drop it, e.g. `["\\bhoroscope\\b", "just kidding"]`.
- **Repeats.** Items already on the list — including the same thing said in
  slightly different words (*"take the van back"* vs *"take van back"*) — are not
  added again. `dedup_threshold` controls how similar counts as a repeat (higher
  = stricter). **AI mode** is even better at both, since it understands intent.

### Tests

```sh
python tests/test_pipeline.py     # no dependencies
# or, if you have pytest:  python -m pytest tests/
```

The tests build a synthetic `chat.db` and verify message filtering,
`attributedBody` decoding, Apple-epoch timestamp conversion, the watermark, and
TODO de-duplication.

### Privacy note

This reads your personal message database. Everything runs locally; the only
data that leaves your machine is the message text sent to the Claude API **if**
you choose the `claude` extractor. Use `keywords` for a fully offline setup.
