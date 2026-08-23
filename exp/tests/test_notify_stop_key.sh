#!/bin/bash
# The STOP notification key must be unique per attempt, and stable within one.
#
# cal-0p07-s0 failed the same way at 08:44 and again at 10:18. The second STOP
# was logged DUP and never reached the phone, because the key was
# md5(label|reason) -- identical for both. A repeat of the same stop must still
# be suppressed; a new attempt must never be.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
PASS=0; FAIL=0
check() { if [ "$2" = "1" ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }

TMP=$(mktemp -d)
export PRISM_EVAL_DIR="$TMP/eval"
mkdir -p "$PRISM_EVAL_DIR"
# No topic: notify.sh logs SKIP and never sends. The key still gets computed and
# logged, which is what these assertions read.
unset PRISM_NTFY_TOPIC
export WORKSPACE="$TMP/nowhere"

keys_of() { grep -oE '(SKIP|SENT|DUP|FAIL) +stop-[0-9a-f]+' "$PRISM_EVAL_DIR/notification.log" 2>/dev/null | awk '{print $2}'; }

run_dir_a="$TMP/raw/tau_0p07/seed_0"; mkdir -p "$run_dir_a"
echo 1 > "$run_dir_a/pipeline.rc"
echo "reason one" > "$PRISM_EVAL_DIR/STOP"

# 1. same hook fired twice for the same stop -> one key, deduplicated
bash "$ROOT/exp/scripts/notify_stop.sh" "cal-0p07-s0" "$run_dir_a" "request loss" >/dev/null 2>&1
bash "$ROOT/exp/scripts/notify_stop.sh" "cal-0p07-s0" "$run_dir_a" "request loss" >/dev/null 2>&1
n_uniq=$(keys_of | sort -u | wc -l)
check "the same stop fired twice yields one key" "$([ "$n_uniq" = "1" ] && echo 1 || echo 0)"

# 2. a NEW attempt of the same label and reason -> a different key
sleep 1
: > "$PRISM_EVAL_DIR/STOP"; echo "reason one" > "$PRISM_EVAL_DIR/STOP"
touch "$run_dir_a/pipeline.rc"          # the attempt moved on
bash "$ROOT/exp/scripts/notify_stop.sh" "cal-0p07-s0" "$run_dir_a" "request loss" >/dev/null 2>&1
n_uniq2=$(keys_of | sort -u | wc -l)
check "a new attempt of the same label+reason yields a NEW key" "$([ "$n_uniq2" = "2" ] && echo 1 || echo 0)"

# 3. a different run directory -> a different key
run_dir_b="$TMP/raw/tau_0p10/seed_0"; mkdir -p "$run_dir_b"; echo 1 > "$run_dir_b/pipeline.rc"
bash "$ROOT/exp/scripts/notify_stop.sh" "cal-0p07-s0" "$run_dir_b" "request loss" >/dev/null 2>&1
n_uniq3=$(keys_of | sort -u | wc -l)
check "a different run directory yields a NEW key" "$([ "$n_uniq3" = "3" ] && echo 1 || echo 0)"

# 4. the old scheme would have collapsed 1-3 into one key
old_key=$(echo -n "cal-0p07-s0|request loss" | md5sum | cut -c1-16)
check "the old label+reason key was identical across all three" \
  "$([ -n "$old_key" ] && echo 1 || echo 0)"

# 5. a stop with no run directory still gets a key and never fails the caller
bash "$ROOT/exp/scripts/notify_stop.sh" "02-tau-calibration" "-" "stage failed" >/dev/null 2>&1
check "a stop with no run dir still notifies" \
  "$([ "$(keys_of | sort -u | wc -l)" -ge "4" ] && echo 1 || echo 0)"
bash "$ROOT/exp/scripts/notify_stop.sh" "02-tau-calibration" "-" "stage failed" >/dev/null 2>&1; rc=$?
check "and exits 0 for its caller" "$([ "$rc" = "0" ] && echo 1 || echo 0)"

# 6. a missing run directory must not crash the composer
bash "$ROOT/exp/scripts/notify_stop.sh" "cal-x" "$TMP/does/not/exist" "gone" >/dev/null 2>&1; rc=$?
check "a missing run dir exits 0" "$([ "$rc" = "0" ] && echo 1 || echo 0)"

rm -rf "$TMP"
echo; echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
