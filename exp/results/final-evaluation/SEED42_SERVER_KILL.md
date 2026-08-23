# τ=0.07: seed_0 passed cleanly; seed_42's server was killed from outside

Runtime `16fa9b2`. **No code changed. No patch proposed.**

## 1. seed_0 passed — the best run of this campaign

| | |
|---|---|
| completed | **8,227 / 8,227** |
| aborted | **0** |
| throughput | 14.2 req/s |
| client descriptor failures | 0 |
| client connection failures | 0 |
| `pipeline.rc` | 0 |

## 2. The response path is clean, and the instrumentation says so

Boundary counts for seed_0:

| boundary | events |
|---|---|
| engine_output | 8,234 |
| stream_output_send | 8,234 |
| detokenizer_send | 8,234 |
| handler_recv | 8,233 |
| http_final_yield | 8,233 |

Every request that the engine finished crossed all five boundaries. **No rid is
lost between engine output and HTTP yield in a run that completes.** The
question the instrumentation was added to answer is answered for the healthy
case; 41,168 events cost 13 MB, and it did not perturb the run.

## 3. seed_42: the whole server process group was killed

```
14:02:28  200 OK responses still being served normally
14:02:26-28  model_service, gpu_scheduler, global_controller all stop within 2 s
14:02:51  bash: line 1: 1085134 Killed   python3 -m sglang.launch_multi_model_server
          resource_tracker: process died unexpectedly
          CudaIPCTypes: Producer process has been terminated before all shared
                        CUDA tensors released
```

`Killed` is SIGKILL. What it was **not**:

- **not out of memory** — cgroup `memory.events` reports `oom_kill 0`, and the
  container is using 61 GB of a 714 GB limit with 1.3 TB free on the host
- **not a crash** — no traceback, no CUDA error, no assertion; the last thing in
  the log is a normal 200 OK
- **not the client** — 0 descriptor failures. The 5,113 connection failures
  (1,126 `connection_refused`, 2,438 `server_disconnected`, 1,267
  `ClientOSError`) all follow 14:02:51 and are the client meeting a server that
  no longer exists
- **not the instrumentation** — 19,535 events, 6.6 MB, disk at 13 %

The monitor recorded it accurately: `benchmark: inner server session exited
without result`.

## 4. Second occurrence

| run | outcome |
|---|---|
| `tau_0p07/seed_0.startupfail1` (08:21) | killed during startup |
| `tau_0p07/seed_42` (14:02) | killed 9 minutes into serving |

The first I attributed to my own concurrent test-suite run, and that attribution
may well have been wrong: nothing of mine was running during the second. Both
are τ=0.07, which may be coincidence — τ=0.07 is simply where the chain has spent
the most time.

I cannot identify the source of the signal from inside the container: `dmesg` is
not readable here, and no in-container mechanism accounts for it. The
harness's own `pkill -f "sglang.launch_multi_model_server"`
(`final_stage.sh:142` and `:189`) is unqualified by port and would kill any
server — but both call sites run in teardown, after a run's `pipeline.rc` exists,
and the calibration stages are sequential. That is a hazard worth noting; it is
not evidence that it fired here.

## 5. This is a new failure class, and that changes the approach

Per the standing instruction, another distinct failure class ends the
individual-patch approach. This is one: an external SIGKILL of the server
process group, with no OOM, no crash and no in-container explanation. It is
categorically unlike the four already fixed, all of which were request-lifecycle
defects inside Prism.

**STOP.** No patch, no retry, no timeout. Awaiting direction.

## 6. State

- Pipeline STOPPED at stage 02. Runtime `16fa9b2`, untouched.
- Valid calibration inputs: `tau_0p00035/seed_0`, `tau_0p00035/seed_42`,
  **`tau_0p07/seed_0`** — three of twelve.
- Phone alerts fired correctly for both the run and the stage.
