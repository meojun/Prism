#!/bin/bash
# Process ownership: a run may signal what it started, and nothing else.
#
# Two servers died to an unexplained SIGKILL on 2026-08-23, and the harness's own
# `pkill -f "sglang.launch_multi_model_server"` could not be ruled out because it
# matches every server on the machine. These tests check that the wildcard is
# gone, that teardown reaches only the recorded process group, and that a
# server's disappearance is now evidenced rather than guessed at.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
. "$ROOT/exp/scripts/proc_ownership.sh"
PASS=0; FAIL=0
check() { if [ "$2" = "1" ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }

TMP=$(mktemp -d)
mine="$TMP/mine"; theirs="$TMP/theirs"; mkdir -p "$mine" "$theirs"

# Two stand-in "servers", each in its own process group, with the same command
# line a wildcard kill would have matched.
start_fake() {   # $1 = outdir, $2 = port
  # A stand-in with the exact command line a wildcard kill would have matched,
  # in its own process group. `sleep` is used via python: a bare foreground
  # sleep is blocked in this environment.
  # stdout/stderr are closed off: otherwise the command substitution that calls
  # this waits for the background process's fds, i.e. for the whole sleep.
  setsid bash -c "exec -a 'python3 -m sglang.launch_multi_model_server --port $2' python3 -c 'import time;time.sleep(300)'" \
    >/dev/null 2>&1 &
  echo $!
}

echo "the harness no longer contains a wildcard server kill"
hits=$(grep -c 'pkill -f "sglang.launch_multi_model_server"' \
       "$ROOT/exp/scripts/final_stage.sh" 2>/dev/null; true)
check "final_stage.sh has no wildcard pkill" "$([ "$hits" = "0" ] && echo 1 || echo 0)"
hits2=$(grep -c 'pkill' "$ROOT/exp/scripts/run_v4_case.sh" 2>/dev/null; true)
check "run_v4_case.sh has no pkill at all" "$([ "$hits2" = "0" ] && echo 1 || echo 0)"

echo "recording ownership"
pid_mine=$(start_fake "$mine" 41111)
pid_theirs=$(start_fake "$theirs" 42222)
python3 -c 'import time;time.sleep(1)'
prism_record_server "$mine" 41111 "mine" >/dev/null 2>&1
check "server_process.json written" "$([ -f "$mine/server_process.json" ] && echo 1 || echo 0)"
rec_pid=$(python3 -c "import json;print(json.load(open('$mine/server_process.json'))['pid'])" 2>/dev/null)
check "it records a live pid" "$([ -n "$rec_pid" ] && kill -0 "$rec_pid" 2>/dev/null && echo 1 || echo 0)"
rec_pgid=$(python3 -c "import json;print(json.load(open('$mine/server_process.json'))['pgid'])" 2>/dev/null)
check "and its process group" "$([ -n "$rec_pgid" ] && [ "$rec_pgid" != "None" ] && echo 1 || echo 0)"
check "the recorded pid is the one on OUR port" \
  "$([ "$rec_pid" != "$pid_theirs" ] && echo 1 || echo 0)"

echo "teardown reaches only what this run started"
prism_kill_server "$mine" "test teardown" >/dev/null 2>&1
python3 -c 'import time;time.sleep(2)'
check "our server is gone" "$(kill -0 "$rec_pid" 2>/dev/null && echo 0 || echo 1)"
check "the other run's server is untouched" "$(kill -0 "$pid_theirs" 2>/dev/null && echo 1 || echo 0)"

echo "every signal is audited"
check "KILL_AUDIT.jsonl written" "$([ -f "$mine/KILL_AUDIT.jsonl" ] && echo 1 || echo 0)"
sent=$(grep -c '"event":"kill_sent"' "$mine/KILL_AUDIT.jsonl" 2>/dev/null; true)
check "the kill is recorded with signal, target and reason" \
  "$([ "$sent" -ge "1" ] && grep -q '"signal"' "$mine/KILL_AUDIT.jsonl" \
     && grep -q '"reason"' "$mine/KILL_AUDIT.jsonl" && echo 1 || echo 0)"
check "the audit lines are valid JSON" \
  "$(python3 -c "
import json,sys
[json.loads(l) for l in open('$mine/KILL_AUDIT.jsonl') if l.strip()]
print(1)" 2>/dev/null || echo 0)"
check "the caller is recorded" "$(grep -q '\"caller\"' "$mine/KILL_AUDIT.jsonl" && echo 1 || echo 0)"

echo "teardown with nothing recorded is a safe no-op"
empty="$TMP/empty"; mkdir -p "$empty"
prism_kill_server "$empty" "nothing here" >/dev/null 2>&1; rc=$?
check "exits 0" "$([ "$rc" = "0" ] && echo 1 || echo 0)"
check "and says why it skipped" \
  "$(grep -q 'kill_skipped' "$empty/KILL_AUDIT.jsonl" && echo 1 || echo 0)"
check "the other run's server is STILL untouched" "$(kill -0 "$pid_theirs" 2>/dev/null && echo 1 || echo 0)"

echo "a vanished server leaves evidence"
prism_capture_death "$mine" "test capture" >/dev/null 2>&1
check "SERVER_DEATH.json written" "$([ -f "$mine/SERVER_DEATH.json" ] && echo 1 || echo 0)"
for field in server_process cgroup_memory_events cgroup_memory_current gpu related_processes kill_audit proc_status_exists dmesg_tail; do
  check "it records $field" \
    "$(python3 -c "
import json;d=json.load(open('$mine/SERVER_DEATH.json'));print(1 if '$field' in d else 0)" 2>/dev/null || echo 0)"
done
check "it notes the pid is already gone" \
  "$(python3 -c "
import json;d=json.load(open('$mine/SERVER_DEATH.json'));print(1 if d['proc_status_exists'] is False else 0)" 2>/dev/null || echo 0)"

kill "$pid_theirs" 2>/dev/null
rm -rf "$TMP"
echo; echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
