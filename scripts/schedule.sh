#!/bin/bash
#
# Install or remove a macOS launchd job that runs the Parker TODO tracker
# automatically on a schedule. All paths are detected automatically, so there
# is nothing to hand-edit.
#
# Usage:
#   scripts/schedule.sh on [interval_seconds]   # default 900 (15 minutes)
#   scripts/schedule.sh off
#   scripts/schedule.sh status
#
# If ANTHROPIC_API_KEY is set in your shell when you run "on", it is baked into
# the job so the scheduled runs use the smarter AI mode.
#
# Note: the scheduled job runs as your python3, so grant Full Disk Access to
# that binary (System Settings > Privacy & Security > Full Disk Access), not
# just to Terminal. `scripts/schedule.sh status` prints its path.

set -euo pipefail

LABEL="com.barbak.parkertodo"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# Absolute path to the repo (parent of this script's dir), resolved robustly.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

action="${1:-}"
interval="${2:-900}"

find_python() {
    # Prefer python3 on PATH; fall back to common locations.
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi
    for p in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
        [ -x "$p" ] && { echo "$p"; return; }
    done
    echo "python3 not found. Install it (e.g. from python.org) and retry." >&2
    exit 1
}

write_plist() {
    local py="$1"
    local env_block=""
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        env_block="  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key>
    <string>${ANTHROPIC_API_KEY}</string>
  </dict>"
    fi

    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${py}</string>
    <string>-m</string>
    <string>parker_todo</string>
  </array>
  <key>WorkingDirectory</key><string>${REPO_DIR}</string>
${env_block}
  <key>StartInterval</key><integer>${interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${REPO_DIR}/schedule.log</string>
  <key>StandardErrorPath</key><string>${REPO_DIR}/schedule.log</string>
</dict>
</plist>
PLIST_EOF
}

case "$action" in
    on)
        PY="$(find_python)"
        write_plist "$PY"
        if [ "${PARKER_DRYRUN:-}" = "1" ]; then
            echo "(dry run) wrote plist only; skipping launchctl."
            cat "$PLIST"
            exit 0
        fi
        launchctl unload "$PLIST" 2>/dev/null || true
        launchctl load "$PLIST"
        echo "Scheduled: runs every ${interval}s using ${PY}."
        echo "Logs: ${REPO_DIR}/schedule.log"
        if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
            echo "Mode: keyword (no ANTHROPIC_API_KEY was set). Re-run with the key exported to use AI mode."
        else
            echo "Mode: AI (Claude), key baked into the job."
        fi
        echo "IMPORTANT: grant Full Disk Access to ${PY} so it can read Messages."
        ;;
    off)
        launchctl unload "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
        echo "Unscheduled. The tracker will no longer run automatically."
        ;;
    status)
        PY="$(find_python)"
        echo "python3: ${PY}"
        echo "repo:    ${REPO_DIR}"
        if [ -f "$PLIST" ]; then
            echo "job:     installed at ${PLIST}"
            launchctl list | grep "$LABEL" || echo "         (loaded state unknown; try: launchctl list | grep ${LABEL})"
        else
            echo "job:     not installed"
        fi
        ;;
    *)
        echo "Usage: scripts/schedule.sh on [interval_seconds] | off | status" >&2
        exit 2
        ;;
esac
