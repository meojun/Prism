#!/bin/bash
# Own the processes a run starts, and never signal anything else.
#
# The old teardown used `pkill -f "sglang.launch_multi_model_server"`, which
# matches EVERY server on the machine, not just the one this run launched. Two
# servers died to an unexplained SIGKILL on 2026-08-23 and that wildcard could
# not be ruled out, so it is gone: a run records the pid and process group it
# started, and teardown signals only that group.
#
#   prism_record_server <outdir> <port> <label>   after the server is ready
#   prism_kill_server   <outdir> <reason>         instead of any pkill
#   prism_kill_audit    <outdir> <event> <json>   every signal the harness sends
#   prism_capture_death <outdir> <reason>         forensics when a server vanishes

prism_kill_audit() {
  local outdir=$1 event=$2 payload=${3:-\{\}}
  local caller="${FUNCNAME[1]:-shell}:${BASH_SOURCE[1]:-?}:${BASH_LINENO[0]:-?}"
  printf '{"time":%s,"event":"%s","caller":"%s","payload":%s}\n' \
    "$(date +%s.%N)" "$event" "$caller" "$payload" \
    >> "$outdir/KILL_AUDIT.jsonl" 2>/dev/null || true
}

prism_record_server() {
  local outdir=$1 port=$2 label=${3:-}
  local pid pgid ppid
  pid=$(ps -eo pid,args | awk -v p="--port $port" '$0 ~ /launch_multi_model_server/ && index($0,p) {print $1; exit}')
  [ -z "$pid" ] && { echo "[proc] no server pid found for port $port" >&2; return 1; }
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
  python3 - "$outdir" "$pid" "$pgid" "$ppid" "$port" "$label" <<'PY'
import json, os, sys
out, pid, pgid, ppid, port, label = sys.argv[1:7]
json.dump({"pid": int(pid), "pgid": int(pgid) if pgid else None,
           "ppid": int(ppid) if ppid else None, "port": int(port),
           "label": label, "recorded_at": __import__("time").time()},
          open(os.path.join(out, "server_process.json"), "w"), indent=2)
PY
  echo "[proc] server pid=$pid pgid=$pgid ppid=$ppid port=$port"
  prism_kill_audit "$outdir" "server_recorded" \
    "{\"pid\":$pid,\"pgid\":${pgid:-null},\"port\":$port}"
}

prism_kill_server() {
  local outdir=$1 reason=${2:-teardown}
  local f="$outdir/server_process.json"
  if [ ! -r "$f" ]; then
    prism_kill_audit "$outdir" "kill_skipped" "{\"reason\":\"no server_process.json\"}"
    return 0
  fi
  local pid pgid
  pid=$(python3 -c "import json;print(json.load(open('$f')).get('pid') or '')" 2>/dev/null)
  pgid=$(python3 -c "import json;print(json.load(open('$f')).get('pgid') or '')" 2>/dev/null)
  # Signal the process GROUP this run created, and nothing else. No pattern
  # matching, so another run's server can never be caught by it.
  if [ -n "$pgid" ] && kill -0 "-$pgid" 2>/dev/null; then
    prism_kill_audit "$outdir" "kill_sent" \
      "{\"signal\":\"TERM\",\"pgid\":$pgid,\"reason\":\"$reason\"}"
    kill -TERM "-$pgid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8; do
      kill -0 "-$pgid" 2>/dev/null || break
      python3 -c "import time;time.sleep(1)"
    done
    if kill -0 "-$pgid" 2>/dev/null; then
      prism_kill_audit "$outdir" "kill_sent" \
        "{\"signal\":\"KILL\",\"pgid\":$pgid,\"reason\":\"$reason after TERM\"}"
      kill -KILL "-$pgid" 2>/dev/null || true
    fi
  elif [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    prism_kill_audit "$outdir" "kill_sent" \
      "{\"signal\":\"TERM\",\"pid\":$pid,\"reason\":\"$reason (no pgid)\"}"
    kill -TERM "$pid" 2>/dev/null || true
  else
    prism_kill_audit "$outdir" "kill_skipped" \
      "{\"reason\":\"already gone\",\"pid\":${pid:-null},\"pgid\":${pgid:-null}}"
  fi
}

prism_capture_death() {
  # Everything worth knowing about a server that vanished, gathered at the
  # moment it is noticed. Best effort throughout.
  local outdir=$1 reason=${2:-server disappeared}
  python3 - "$outdir" "$reason" <<'PY' 2>/dev/null || true
import json, os, subprocess, sys, time
out, reason = sys.argv[1], sys.argv[2]

def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception as e:
        return f"<{e}>"

rec = {"reason": reason, "captured_at": time.time(),
       "captured_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
f = os.path.join(out, "server_process.json")
if os.path.exists(f):
    rec["server_process"] = json.load(open(f))
    pid = rec["server_process"].get("pid")
    st = f"/proc/{pid}/status"
    rec["proc_status_exists"] = os.path.exists(st)
    if os.path.exists(st):
        rec["proc_status"] = open(st).read()
rec["cgroup_memory_events"] = sh("cat /sys/fs/cgroup/memory.events")
rec["cgroup_memory_current"] = sh("cat /sys/fs/cgroup/memory.current")
rec["free"] = sh("free -m | head -2")
rec["gpu"] = sh("nvidia-smi --query-gpu=index,memory.used,memory.total,"
                "utilization.gpu --format=csv,noheader")
rec["gpu_procs"] = sh("nvidia-smi --query-compute-apps=pid,used_memory "
                      "--format=csv,noheader")
rec["related_processes"] = sh(
    "ps -eo pid,ppid,pgid,stat,etime,args | "
    "grep -E 'launch_multi_model_server|model_service|gpu_scheduler|controller' "
    "| grep -v grep")
rec["dmesg_tail"] = sh("dmesg 2>/dev/null | tail -20") or "<not readable>"
audit = os.path.join(out, "KILL_AUDIT.jsonl")
if os.path.exists(audit):
    rec["kill_audit"] = [json.loads(l) for l in open(audit) if l.strip()]
json.dump(rec, open(os.path.join(out, "SERVER_DEATH.json"), "w"),
          indent=2, default=str)
print(f"[proc] wrote SERVER_DEATH.json ({reason})")
PY
}
