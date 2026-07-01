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
   a **keyword heuristic** (no dependencies, no network).
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
   - `"extractor": "claude"` — set `ANTHROPIC_API_KEY` in your environment and
     `pip install -r requirements.txt`. Uses `claude_model` (default
     `claude-sonnet-5`). Falls back to keywords automatically if the key or
     package is missing.
   - `"extractor": "keywords"` — pure standard library, nothing to install.

### Run it

```sh
python -m parker_todo                 # process new messages, update the list
python -m parker_todo --dry-run       # show what it would add, write nothing
python -m parker_todo --reset         # re-scan full history (ignore watermark)
python -m parker_todo -c config.json  # explicit config path
```

### Run it automatically (every 15 minutes)

Use a `launchd` agent on the Mac. Save this as
`~/Library/LaunchAgents/com.barbak.parkertodo.plist` (edit the paths), then
`launchctl load ~/Library/LaunchAgents/com.barbak.parkertodo.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.barbak.parkertodo</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-m</string>
    <string>parker_todo</string>
  </array>
  <key>WorkingDirectory</key><string>/path/to/BARBAK</string>
  <key>EnvironmentVariables</key>
  <dict><key>ANTHROPIC_API_KEY</key><string>sk-ant-...</string></dict>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardErrorPath</key><string>/tmp/parkertodo.err</string>
  <key>StandardOutPath</key><string>/tmp/parkertodo.out</string>
</dict>
</plist>
```

(Note: `launchd`/`cron` jobs also need Full Disk Access — grant it to
`/usr/bin/python3`, or run under a wrapper that has it.)

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

`config.json`, the state file, and `PARKER_TODO.md` are gitignored so your
personal data stays local.

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
