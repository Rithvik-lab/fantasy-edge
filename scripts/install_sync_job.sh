#!/usr/bin/env bash
# Install the weekly data pull as a macOS LaunchAgent.
#
# WHY launchd AND NOT cron
#
# This is a laptop. cron fires at a wall-clock time and if the machine is
# asleep at that moment the run is simply lost -- silently, with nothing in a
# log, which is the worst failure mode for a thing whose whole job is keeping
# data current. launchd runs a missed StartCalendarInterval job as soon as the
# machine wakes. Same schedule, but it actually happens.
#
# Tuesday 06:00: the earliest every result is final, Monday night included.
#
#   scripts/install_sync_job.sh            install and start
#   scripts/install_sync_job.sh --remove   uninstall
#
# The app also refreshes itself every 6 hours while it is open, so this is the
# belt to that pair of braces -- it keeps the data current in the weeks you do
# not launch the app at all.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

LABEL="com.fantasyedge.sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$ROOT/.venv/bin/python"
LOGDIR="$ROOT/data/logs"

if [ "${1:-}" = "--remove" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

if [ ! -x "$PYTHON" ]; then
  echo "no venv at $PYTHON — create it first" >&2
  exit 1
fi

# macOS will not let a background agent execute out of Desktop, Documents or
# Downloads. It does not ask and it does not log -- launchd returns EX_CONFIG
# (78), the process never starts, and the log file stays zero bytes forever.
# Verified here: an identical job pointed at /bin/date exits 0, and pointed at
# a venv under ~/Downloads exits 78.
#
# Refuse rather than install something that silently never runs.
case "$ROOT" in
  "$HOME"/Downloads/*|"$HOME"/Desktop/*|"$HOME"/Documents/*)
    PROTECTED="$(dirname "${ROOT#"$HOME"/}")"
    if [ "${1:-}" != "--force" ]; then
      cat >&2 <<MSG
This repo lives under ~/${PROTECTED%%/*}, which macOS protects from background
agents. A scheduled job here is accepted by launchctl and then never runs --
exit 78, empty log, no error anywhere.

Two ways forward:

  1. Move the repo somewhere unprotected, then re-run this:
       mv "$ROOT" ~/FantasyEdge && cd ~/FantasyEdge && scripts/install_sync_job.sh

  2. Grant Full Disk Access to $PYTHON in
     System Settings > Privacy & Security > Full Disk Access, then re-run with
     --force. Note this breaks whenever the venv is rebuilt.

Either way the app already refreshes itself every 6 hours while it is open, so
you are not stale in the meantime.
MSG
      exit 1
    fi
    echo "warning: ~/${PROTECTED%%/*} is TCC-protected; installing anyway (--force)" >&2
    ;;
esac

mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$ROOT/scripts/sync.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>2</integer>
    <key>Hour</key><integer>6</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/sync.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/sync.err</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "installed $LABEL"
echo "  runs   Tuesdays 06:00, and on wake if that slot was missed"
echo "  logs   $LOGDIR/sync.log"
echo "  remove scripts/install_sync_job.sh --remove"
