#!/bin/bash
# Push one line to the phone. Never fails the caller.
#
#   notify.sh <key> <message>
#
# <key> makes the send idempotent: the same key is delivered once per pipeline
# directory, so a STOP that is written and then re-read, or a stage that is
# polled twice, does not buzz twice.
#
# The topic is a secret -- anyone who knows it can read and post. It is read
# from PRISM_NTFY_TOPIC in the environment, or from ${WORKSPACE}/.env, and is
# never written into this repository. Without a topic this exits 0 quietly:
# notification is a convenience, and its absence must never stop an experiment.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="${PRISM_EVAL_DIR:-$ROOT/exp/results/final-evaluation}"
LOG="$EVAL/notification.log"
SENT="$EVAL/.notified"

KEY=${1:-event}
MSG=${2:-}
[ -z "$MSG" ] && exit 0

mkdir -p "$EVAL" "$SENT" 2>/dev/null || true
log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG" 2>/dev/null || true; }

# Topic: environment first, then the workspace .env, which is outside the repo.
if [ -z "${PRISM_NTFY_TOPIC:-}" ]; then
  for f in "${WORKSPACE:-/workspace}/.env" "$ROOT/.env.local"; do
    if [ -r "$f" ]; then
      t=$(grep -E '^[[:space:]]*PRISM_NTFY_TOPIC=' "$f" 2>/dev/null | tail -1 \
          | sed -E 's/^[^=]*=//; s/^"//; s/"$//; s/^'"'"'//; s/'"'"'$//')
      [ -n "$t" ] && { PRISM_NTFY_TOPIC="$t"; break; }
    fi
  done
fi
if [ -z "${PRISM_NTFY_TOPIC:-}" ]; then
  log "SKIP $KEY: no PRISM_NTFY_TOPIC configured -- $MSG"
  exit 0
fi

# Idempotent: one delivery per key.
marker="$SENT/$(echo -n "$KEY" | tr -c 'A-Za-z0-9_.-' '_')"
if [ -e "$marker" ]; then
  log "DUP  $KEY (already sent) -- $MSG"
  exit 0
fi

SERVER=${PRISM_NTFY_SERVER:-https://ntfy.sh}
PRIO=${PRISM_NTFY_PRIORITY:-default}
# The verdict has to be the thing a phone shows on the lock screen, so the
# first line of the message becomes the notification title and anything after
# it becomes the body. A single-line message reads the same as before.
TITLE=$(printf '%s' "$MSG" | head -1)
BODY=$(printf '%s' "$MSG" | tail -n +2)
[ -z "$BODY" ] && BODY="$TITLE"
TITLE=${PRISM_NTFY_TITLE:-$TITLE}

# Short timeouts so an ntfy outage cannot hold up a run, and one retry only.
if curl -fsS --max-time "${PRISM_NTFY_TIMEOUT:-6}" --connect-timeout 3 --retry 1 \
     -H "Title: $TITLE" -H "Priority: $PRIO" \
     -d "$BODY" "$SERVER/$PRISM_NTFY_TOPIC" >/dev/null 2>>"$LOG"; then
  : > "$marker" 2>/dev/null || true
  log "SENT $KEY -- $MSG"
else
  # Deliberately no marker: a failed send may be retried by a later caller.
  log "FAIL $KEY (send failed, continuing) -- $MSG"
fi
exit 0
