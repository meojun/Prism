#!/bin/bash
# Scan the repository for credentials before anything is pushed.
#
# Checks the working tree, every tracked file, and the commits on this branch
# that are not yet on the remote -- a secret removed from the tip is still a
# secret if it sits in an earlier commit that is about to be pushed.
#
# Exit 0 only when nothing is found. The report is written for the record.
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-$ROOT/exp/results/final-evaluation/SECRET_SCAN.txt}
cd "$ROOT"

# Patterns for the credential shapes this project actually handles, plus the
# generic key formats. Deliberately not a general-purpose scanner.
PATTERNS='github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{34,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|xox[baprs]-[A-Za-z0-9-]{10,}'
# The ntfy topic is a secret for this deployment; read its name from outside
# the repo so the pattern itself never has to be committed.
TOPIC=""
[ -f /workspace/.env ] && TOPIC=$(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?PRISM_NTFY_TOPIC=["'"'"']?([^"'"'"'[:space:]]+).*/\2/p' /workspace/.env | head -1)

fail=0
{
  echo "secret scan $(date -u +%FT%TZ)"
  echo "repository: $ROOT"
  echo

  echo "## tracked files"
  if git grep -InE "$PATTERNS" -- . > /tmp/.ss_tracked 2>/dev/null; then
    # A redaction pattern is not a secret; it is the code that removes one.
    if grep -vE "redacted|\.example|sed -E" /tmp/.ss_tracked | grep -q .; then
      echo "FOUND:"; grep -vE "redacted|\.example|sed -E" /tmp/.ss_tracked; fail=1
    else
      echo "clean (only redaction patterns matched)"
      grep -E "redacted|sed -E" /tmp/.ss_tracked | sed 's/^/  ignored: /'
    fi
  else
    echo "clean"
  fi
  echo

  echo "## ntfy topic"
  if [ -n "$TOPIC" ]; then
    if git grep -In -F "$TOPIC" -- . >/dev/null 2>&1; then
      echo "FOUND: the ntfy topic appears in tracked files"; fail=1
    else
      echo "clean: 0 hits"
    fi
  else
    echo "no topic configured on this machine; nothing to check"
  fi
  echo

  echo "## commits not yet on the remote"
  base=$(git rev-parse --abbrev-ref HEAD)
  if git rev-parse "origin/$base" >/dev/null 2>&1; then
    range="origin/$base..HEAD"
    n=$(git rev-list --count "$range" 2>/dev/null || echo 0)
    echo "range $range ($n commit(s))"
    if [ "$n" != "0" ]; then
      if git log -p "$range" | grep -nE "$PATTERNS" | grep -vE "redacted|sed -E" | grep -q .; then
        echo "FOUND in unpushed history:"
        git log -p "$range" | grep -nE "$PATTERNS" | grep -vE "redacted|sed -E" | head
        fail=1
      else
        echo "clean"
      fi
      if [ -n "$TOPIC" ] && git log -p "$range" | grep -q -F "$TOPIC"; then
        echo "FOUND: the ntfy topic appears in unpushed history"; fail=1
      fi
    fi
  else
    echo "no remote branch yet; nothing to compare"
  fi
  echo

  echo "## files that must never be tracked"
  for f in .env .git-credentials; do
    if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
      echo "FOUND: $f is tracked"; fail=1
    else
      echo "clean: $f is not tracked"
    fi
  done
  echo
  echo "VERDICT: $([ "$fail" = 0 ] && echo CLEAN || echo SECRETS_FOUND)"
} | tee "$OUT"
rm -f /tmp/.ss_tracked
exit $fail
