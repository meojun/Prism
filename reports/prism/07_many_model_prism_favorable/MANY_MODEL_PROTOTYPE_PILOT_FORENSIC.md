# Many-model Prototype pilot correctness forensic

Forensic 대상은 다음 실행 하나뿐이다.

`/workspace/prism-exp/exp/results/many-model-pilot/raw/prototype/bursty/rate_16/seed_9`

이 조사는 기존 산출물과 소스의 읽기 및 `/tmp`를 이용한 오프라인 게이트 재생만 수행했다. STOP은 그대로 두었고, pilot을 재실행하지 않았으며, GPU 작업도 수행하지 않았다.

## 최종 판정

`VERDICT = VALIDITY_GATE_FALSE_POSITIVE`

`FIRST_UNEXPECTED_EVENT = 2026-08-25T21:47:57.589952708Z: final_stage가 label mm-pilot-prototype-bursty-r16-s9를 prism arm으로 오분류해 check_alg2_interaction.py를 --arm prism으로 실행하고 algorithm2_ran=false를 기록함`

`LAST_CONFIRMED_CORRECT_EVENT = 2026-08-25T21:47:42Z: released-prototype_bursty_rate16_seed9 stage complete rc=0; 8764/8764 completed, aborted=0, monitor COMPLETE`

`PROTOTYPE_CONFIG_MATCH = YES`

`PRISM_MIGRATION_OCCURRED_IN_PROTOTYPE = NO`

`PILOT_DATA_USABLE = YES`

`PILOT_DATA_USABLE`은 완료된 Prototype arm의 진단용 pilot 데이터에 한정한다. 동결 프로토콜 자체가 pilot을 final statistics에서 제외하며, Prism pilot arm은 시작되지 않았으므로 완성된 paired pilot으로 사용할 수 있다는 뜻은 아니다.

핵심 결론은 다음과 같다.

1. 실행된 서버는 정확히 `released-prototype`/`simple-global`이었다. Prism Alg1, Alg2, overlap migration 및 KV migration 플래그가 모두 없었다.
2. `algorithm2_ran`은 런타임 이벤트가 아니다. post-run 검증기가 잘못 전달받은 `arm: prism`에 따라 만든 실패 check 이름이다. 실제 Alg2 runtime event와 Alg2 log line은 모두 0이다.
3. Prototype은 원래도 `simple-global` 정책에 따라 모델을 재배치한다. pilot의 model_4 GPU 0→1 이동은 그 정상 Prototype migration이며, Prism의 KVPR/Alg2/overlap/KV migration은 아니다.
4. 538.1초는 실제 대기 시간이 아니다. Prototype의 fire-and-forget 경로에는 설계상 생성되지 않는 `[V5-HOP]` 표식을 lifecycle 게이트가 모든 arm에서 acknowledgement로 요구하고, 파일의 마지막 timestamp에서 요청 timestamp를 뺀 값이다. 실제 요청은 즉시 수신·처리되었다.

## 1. 정확한 Prototype launch 복원

### Provenance

run directory 안에는 이름이 `RUN_MANIFEST`인 파일은 없다. 따라서 다음의 서로 일치하는 증거로 launch를 복원했다.

- `STAGE_CMD.sh`: outer runner가 실제로 저장한 stage 명령
- `SERVER_COMMAND.txt`: `run_v4_case.sh`가 실행 직전에 저장한 resolved server 명령
- `monitor/heartbeat.jsonl`: 실행 중 PID와 전체 command line snapshot
- `server_process.json`: 소유권 wrapper PID/PGID/port
- `exp/results/many-model-pilot/pipeline.log`: arm/system, runtime 및 trace hash 검증
- `exp/state/PRISM_FINAL_PIPELINE_STATE.json`: 동결 run/runtime identity
- `MANY_MODEL_PROTOCOL_FROZEN.json` 및 `MANY_MODEL_PILOT_TRACE_MANIFEST.json`: pilot 조건과 trace freeze

### Exact outer command

```bash
env PRISM_ROOT=/workspace/prism-exp \
  PRISM_REPO=/workspace/prism-exp/prism-research \
  PRISM_EXP=/workspace/prism-exp/exp \
  PRISM_EVAL_DIR=/workspace/prism-exp/exp/results/many-model-pilot \
  CFG=/workspace/prism-exp/exp/configs/v2/6model_2gpu.json \
  BENCHMARK_TIMEOUT=2000 \
  KVPR_TAU=0.012859417696566448 \
  KVPR_WINDOW=60 \
  KVPR_COOLDOWN=30 \
  PREFILL_SPEED_FILE=/workspace/prism-exp/exp/configs/v2/prefill_speed.json \
  SLO_BASE_FILE=/workspace/prism-exp/exp/configs/v2/slo_base.json \
  bash /workspace/prism-exp/exp/scripts/run_v4_case.sh \
    released-prototype bursty 16 9 \
    /workspace/prism-exp/exp/workloads/many_model_pf/bursty_r16_s9.pkl \
    /workspace/prism-exp/exp/results/many-model-pilot/raw/prototype/bursty/rate_16/seed_9
```

### Exact resolved server command

```bash
python3 -m sglang.launch_multi_model_server \
  --model-config-file /workspace/prism-exp/exp/configs/v2/6model_2gpu.json \
  --host 127.0.0.1 \
  --port 41200 \
  --disable-cuda-graph \
  --disable-radix-cache \
  --log-file /workspace/prism-exp/exp/results/many-model-pilot/raw/prototype/bursty/rate_16/seed_9/server-logs/server.log \
  --enable-elastic-memory \
  --use-kvcached-v0 \
  --enable-cpu-share-memory \
  --max-mem-usage 67.28 \
  --enable-gpu-scheduler \
  --enable-controller \
  --policy simple-global \
  --enable-model-service \
  --enable-worker-pool \
  --workers-per-gpu 4 \
  --num-model-service-workers 6 \
  --num-gpus 2
```

`env.sh`가 `/workspace/prism-exp/prism-venv/bin/activate`를 source한 뒤 위 `python3`를 실행하고, `PYTHONPATH=/workspace/prism-exp/prism-research/python`을 강제한다. 실행 중 heartbeat에 나타난 실제 module process는 PID `1727659`; 소유권을 기록한 launch shell은 PID/PGID `1727578`; `final_stage.sh`는 PID `1727469`였다.

### Arm, policy, runtime identity

| 항목 | 복원값 |
|---|---|
| SYSTEM / arm | `released-prototype` / `prototype` |
| global policy | `simple-global` |
| runtime executable | 활성화된 `prism-venv`의 `python3 -m sglang.launch_multi_model_server` |
| runtime source | `/workspace/prism-exp/prism-research/python` |
| RUN_CODE_COMMIT | `413f9ee44aa7563afd7570f06ca74100b738dad8` |
| runtime base commit | `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8` |
| RUNTIME_SOURCE_TREE_HASH | `7fbd431c6a636df0c72bb6a40324f851d002d204` |
| runtime worktree patch SHA256 | `49f47ebd7c75aecdac2d67efea1b3d121b9e599693df514cfd71bfe10724881d` |
| config SHA256 | `a70e906c6249c70de11567fdc7a26404929c0c8351eaf8631346fa3db644e51d` |
| trace SHA256 | `071d3861aa50c929579842ee3d4bae31382bff279886aa8af3bd42be510fa004` |

`pipeline.log`은 launch 전에 runtime patch `49f47e...`와 trace `071d38...`를 검증했다. config의 현재 SHA256은 이전 environment freeze에 기록된 `config_6model_2gpu` SHA256과 byte-for-byte 같다.

### Alg1/Alg2/migration/lifecycle flags

Prototype 서버에 실제로 존재한 관련 flag는 다음뿐이다.

- controller: `--enable-controller --policy simple-global`
- shared lifecycle infrastructure: `--enable-gpu-scheduler --enable-elastic-memory --enable-model-service --enable-worker-pool`
- workers/models: `--workers-per-gpu 4 --num-model-service-workers 6 --num-gpus 2`
- memory/cache: `--use-kvcached-v0 --enable-cpu-share-memory --max-mem-usage 67.28`

다음 Prism Alg1 관련 flag는 모두 **없다**.

- `--policy kvpr-global` / `--policy kvpr-global-v4`
- `--kvpr-tau`
- `--kvpr-rate-window`
- `--slo-base-file`
- `--kvpr-migration-cooldown`
- `--kvpr-tpot-slo-scale`

다음 Prism Alg2 관련 flag도 모두 **없다**.

- `--enable-moore-hodgson`
- `--prefill-speed-file`

다음 Prism migration 관련 flag도 모두 **없다**.

- `--parallel-model-loading`
- `--overlap-migration`
- `--enable-kv-migration`

`KVPR_TAU`, `KVPR_WINDOW`, `KVPR_COOLDOWN`, `PREFILL_SPEED_FILE`, `SLO_BASE_FILE` 환경변수는 공통 stage command에 있었지만 `released-prototype` case는 이를 server args에 넣지 않는다. 또한 runner는 Prototype을 포함한 non-v4 arm에서 `PRISM_V4_PAGELOCK`과 `PRISM_V4_P2P_MIGRATION`을 unset하고, non-v6 arm에서 `PRISM_V6_KV_MIGRATION`과 `PRISM_V6_KV_TRACE`를 unset한다. `PRISM_V4_LOAD_TRACE`만 모든 arm의 weight-load provenance 수집용으로 설정된다.

### Expected many-model Prototype config

동결된 pilot은 bursty/r16/seed9, 6 models, 2 GPUs, 8 workers, 6 model-service workers이다. 초기 on placement는 다음과 같다.

- GPU 0: model_1 `meta-llama/Llama-3.2-1B`, model_4 `Qwen/Qwen2.5-3B-Instruct`, model_5 `meta-llama/Llama-3.1-8B`
- GPU 1: model_2 `Qwen/Qwen2.5-1.5B-Instruct`, model_3 `meta-llama/Llama-3.2-3B`, model_6 `Qwen/Qwen2.5-7B-Instruct`
- 각 모델은 `tp_size=1`, 초기 placement는 `on=true`, `max_memory_pool_size=20.0`

### Historical valid Prototype와의 config-for-config 비교

비교 대상은 기존 `VERIFICATION.json verdict=PASS`인 다음 세 실행이다.

- `4het-paired/raw/prototype/bursty/rate_8/seed_1`
- `4het-paired/raw/prototype/steady/rate_8/seed_1`
- `4het-paired/raw/prototype/bursty/rate_10/seed_2`

이전 `6model-pilot/PILOT_MANIFEST.json`은 이 서버에서 과거 6-model released Prototype이 실행된 적이 없다고 명시한다. 따라서 완전 동일한 6-model historical byte comparator는 없다. 대신 resolved commands를 직접 diff했다.

문자열 전체는 동등하지 않다. 의도된 many-model 차이인 config path(4→6 models), 동적 port, run-specific log path, workers-per-gpu(3→4), model-service workers(4→6)가 다르다. 이 변수들을 제외한 모든 mechanism flag는 동일하다.

- 모두 `released-prototype`
- 모두 `--policy simple-global`
- 모두 GPU scheduler/controller/model service/worker pool 사용
- 모두 Alg1 flag 없음
- 모두 Alg2 flag 없음
- 모두 overlap/KV migration flag 없음
- 모두 2 GPUs

6-model config와 trace는 각각 동결된 hash와 정확히 일치하므로, many-model에 필요한 크기 차이를 반영한 의미상의 config-for-config 결과는 `YES`이다.

따라서 이 절의 config match 판단은 **YES**이다.

## 2. `algorithm2_ran` 증거의 정확한 위치와 생산자

### Record

| 항목 | 결과 |
|---|---|
| file | `.../seed_9/ALG2_INTERACTION.json` 및 표시용 `alg2_interaction.log` |
| file timestamp | `2026-08-25T21:47:57.589952708Z` |
| exact record type | validator check `{ "check": "algorithm2_ran", "pass": false }` |
| detail | `{ "runtime_events": 0, "alg2_log_lines": 0, "arm": "prism" }` |
| validation executable | `/workspace/prism-exp/prism-venv/bin/python exp/scripts/check_alg2_interaction.py` |
| validator PID | 별도 보존되지 않음; 호출 parent `final_stage.sh` PID는 `1727469` |
| pilot server process | Python PID `1727659`, ownership shell PID/PGID `1727578` |
| GPU/model | 해당 없음. 런타임 이벤트가 아닌 post-run check record임 |

`algorithm2_ran`이라는 이름의 런타임 log line/event는 없다. parser는 오히려 현재 run에서 Alg2 event와 line을 각각 0개 찾았다. 서버 cleanup은 `21:47:31.338Z` TERM, `21:47:39.567Z` KILL로 끝났고, `ALG2_INTERACTION.json`은 그 뒤 `21:47:57.589Z`에 생성되었다. 따라서 이 record를 pilot GPU process가 생산했다고 볼 수 없다.

### Attribution

생산자는 **validation parser**이다.

- pilot Prototype process 자체: 아님. Alg2 runtime events=0, Alg2 log lines=0.
- stale/shared logfile: 아님. parser는 전달된 `--run` 아래의 `server-logs/*gpu_scheduler*.log`, `server.log`, `stdout.log`만 연다.
- another concurrent process: 근거 없음. record는 runtime line이 아니라 parser output이며, parser input scope도 current run으로 한정된다.
- validation parser: 맞음. 잘못된 `--arm prism`에 의해 absence를 `algorithm2_ran=false`로 변환했다.

## 3. 538.1초 activate/deactivate 분석

### 실제 시간선

1. `21:38:28.383` GlobalController가 `model_4`를 GPU 0→1로 옮기면 unstable pairs가 1→0이 된다고 계산했다.
2. `21:38:28.384` source instance 0 deactivate HTTP request를 보냈다.
3. `21:38:28.385` destination instance 1 activate HTTP request를 보냈다.
4. `21:38:28.389` request handler가 GPU scheduler 1로 activate control request를 보냈다.
5. `21:38:28.391` request handler가 GPU scheduler 0으로 deactivate control request를 보냈다.
6. 같은 초에 `/activate`와 `/deactivate` HTTP 요청이 모두 `200 OK`를 반환했다.
7. `21:38:28.392` GPU 1 Worker 3이 activate request `rid=fd2d6942...`를 수신·처리했다.
8. `21:38:28.395` GPU 0 Worker 1(model_4)이 deactivate request `rid=3fbe1aa4...`를 수신·처리했다.
9. controller의 `PAPER-ACTION-V4` 두 record는 `success=true`; duration은 activate `0.00657s`, deactivate `0.00812s`였다.
10. `21:38:29.619` model service가 Qwen 3B를 destination GPU 1에 load 완료했고, `21:38:29.887` GPU 1 Worker 3의 activation이 완료되었다.
11. `21:38:30.907` destination GPU 1의 model_4가 새 generation request를 받았다.

### 요청의 정체

| 항목 | 결과 |
|---|---|
| 최초 의사결정/송신자 | pilot의 단일 `GlobalController`, `simple-global` policy |
| request type | `DeactivateAction` + `ActivateAction`; 내부 `DeactivateReqInput` + `ActivateReqInput` |
| model | `model_4` = `Qwen/Qwen2.5-3B-Instruct` |
| source/destination | GPU 0 / GPU 1 |
| mechanism | released Prototype의 정상 model placement migration |
| expected control consumer | GPU 0 Worker 1과 GPU 1 Worker 3을 소유한 GPU schedulers |
| 실제 결과 | 두 scheduler가 즉시 수신; destination activation 완료; controller action success |

weight trace의 destination load는 `engine=1_3`, `target_gpu=1`, `src=None`, `source=cpu`이다. Prism의 overlapping source→destination migration이나 KV migration이 아니다.

### 왜 gate에는 acknowledgement가 없었는가

`lifecycle_validity_gate.py`는 `Sending activate/deactivate request to GPU scheduler`를 send로 기록한 뒤, 오직 `[V5-HOP] {"action":"ActivateReqInput|DeactivateReqInput", ...}`만 ack로 인정한다.

그러나 runtime의 `request_handler_worker_pool.py`는 다음과 같이 동작한다.

- `--overlap-migration`이 켜진 경우: `_send_req_and_wait_for_response()`로 engine response를 기다리고 `[V5-HOP]`을 기록한다.
- flag가 꺼진 경우: `_send_req_to_gpu_scheduler()` 후 즉시 `(True, None)`을 반환한다. `[V5-HOP]`을 기록하지 않는다.

Prototype command에는 `--overlap-migration`이 없으므로 후자가 정확히 기대된 경로다. 따라서 “ack가 오지 않았다”가 아니라 “이 arm/mode에는 게이트가 요구하는 ack 표식의 producer가 호출되지 않았다”가 정확하다. 538.1초는 마지막 server log timestamp와 send timestamp의 차이일 뿐 control-call latency가 아니다.

따라서 이 절의 Prism migration 판단은 **NO**이다.

위 값은 **Prism migration**에 대한 답이다. Prototype 자체의 `simple-global` model migration은 1회 발생했고 정상 완료됐다.

## 4. Validity gate audit 및 오프라인 replay

### Alg2 rule의 실제 의도

`check_alg2_interaction.py`의 규칙은 다음과 같다.

- Prism arm: `[PAPER-ALG2-RUNTIME]` event 또는 `[PAPER-ALG2]` line이 하나 이상이어야 `algorithm2_ran` PASS.
- Prototype arm: 두 종류 모두 0이어야 `algorithm2_absent_as_expected` PASS.

즉 의도는 명확히 **“Prototype must never execute Algorithm 2”**이다. Prototype에서 Alg2가 없다는 사실은 실패 조건이 아니라 정상 증거다.

문제는 `final_stage.sh`의 arm 선택이다. 다음 label만 Prototype으로 인정한다.

```bash
case "$LABEL" in
  protofresh-*|proto-*|p4het-proto-*) arm=prototype ;;
  *) arm=prism ;;
esac
```

pilot label `mm-pilot-prototype-bursty-r16-s9`는 어느 Prototype pattern에도 맞지 않아 default `prism`으로 들어갔다. 같은 문제가 normal path와 ALREADY_COMPLETE path 양쪽에 있다.

### Log scope

Alg2 parser의 log scope는 current run에 한정돼 있다. `--run`에서 다음만 읽는다.

- `<run>/server-logs/*gpu_scheduler*.log`
- `<run>/server-logs/server.log`
- `<run>/server-logs/stdout.log`

다른 run directory, shared aggregate log 또는 orchestrator log를 읽는 코드는 없다. 따라서 case C인 log mixing은 배제된다.

### Offline replay 결과

게이트 코드는 수정하지 않았다. 출력 JSON은 repo 밖의 임시 directory에만 썼다.

| run | supplied arm | Alg2 replay | 핵심 결과 |
|---|---:|---:|---|
| failed many-model Prototype pilot | `prototype` | PASS/rc=0 | `algorithm2_absent_as_expected`, events=0, lines=0 |
| 같은 pilot | `prism` | FAIL/rc=1 | 원래와 동일한 `algorithm2_ran`, events=0, lines=0 |
| historical Prototype bursty r8 s1 | `prototype` | PASS/rc=0 | events=0, lines=0 |
| historical Prototype steady r8 s1 | `prototype` | PASS/rc=0 | events=0, lines=0 |
| historical Prototype bursty r10 s2 | `prototype` | PASS/rc=0 | events=0, lines=0 |

이 replay는 원래 FAIL이 log content가 아니라 잘못된 arm 전달 하나로 완전히 재현됨을 입증한다.

동일 네 run에 `lifecycle_validity_gate.py`도 오프라인 실행했다.

- failed pilot: 2개의 538.1초 false unmatched record
- historical PASS Prototype bursty r8 s1: 동일 논리로 24개 false unmatched record
- historical PASS Prototype steady r8 s1: control send가 없어 PASS
- historical PASS Prototype bursty r10 s2: 동일 논리로 17개 false unmatched record

즉 lifecycle gate도 current Prototype의 fire-and-forget control protocol을 arm/mode와 무관하게 overlap-mode acknowledgement protocol로 해석한다.

## 5. Historical Prototype behavior 비교

| run | Alg2 runtime/log | Prototype migrations | control sends | `[V5-HOP]` | controller/GPU schedulers | mechanism flags |
|---|---:|---:|---:|---:|---|---|
| failed many-model bursty r16 s9 | 0/0 | 1 | 2 | 0 | 1 GlobalController, GPU 0/1 scheduler | `simple-global`, no Prism flags |
| valid 4-HET bursty r8 s1 | 0/0 | 6 | 24 | 0 | same topology | same Prototype flags |
| valid 4-HET steady r8 s1 | 0/0 | 0 | 0 | 0 | same topology | same Prototype flags |
| valid 4-HET bursty r10 s2 | 0/0 | 4 | 17 | 0 | same topology | same Prototype flags |

과거 valid Prototype도 bursty workload에서 model migration과 activate/deactivate를 정상적으로 수행했고 `[V5-HOP]`은 만들지 않았다. failed pilot은 이 역사적 동작 범위 안에 있다. many-model pilot이 Prism Alg2나 Prism migration을 켰다는 증거, scheduler/controller가 추가로 떠 있었다는 증거, 다른 run의 log가 섞였다는 증거는 없다.

`PAPER-ALG2-HANDOFF` 및 `PAPER-OVERLAP-V6` 문자열이 common patched runtime의 engine instrumentation에 일부 나타나지만, Alg2 scheduler runtime marker `[PAPER-ALG2-RUNTIME]`은 0이고 실제 Alg2 flag도 없다. marker 이름만으로 Alg2 실행을 추론하면 안 된다.

## Causal boundary

### LAST_CONFIRMED_CORRECT_EVENT

`2026-08-25T21:47:42Z`: stage command가 rc=0으로 끝났다. result는 completed 8764, aborted 0이고 `ALG2_INTERACTION.json`의 request accounting도 offered=8764, completed=8764, accounted=8764로 PASS했다. monitor는 `COMPLETE`, 마지막 progress는 `pipeline rc=0`이다.

### FIRST_UNEXPECTED_EVENT

`2026-08-25T21:47:57.589952708Z`: `final_stage.sh`의 label pattern이 `mm-pilot-prototype-*`를 인식하지 못하고 `arm=prism`을 선택했다. 그 결과 post-run parser가 Alg2의 정상 부재를 `algorithm2_ran=false`로 기록했다. 예상 밖 사건은 runtime에서 발생한 migration/lifecycle event가 아니라 post-run gate invocation이다.

## 최소 gate-scope correction 제안 — 구현하지 않음

1. `final_stage.sh`가 자유형 label pattern으로 arm을 재추론하지 않게 한다. `prism_final_stage_runner.sh`가 이미 보유한 구조화된 `arm` 값을 명시적으로 전달하고, `check_alg2_interaction.py --arm`에 그대로 넘기는 것이 최소이자 안전한 correction이다. label pattern 확장은 차선책이며 `mm-pilot-prototype-*`뿐 아니라 이후 새 stage label에서도 재발할 수 있다.
2. lifecycle control-ack check는 `--overlap-migration`이 활성화되어 `[V5-HOP]` producer가 존재하는 run에만 적용한다. Prototype/non-overlap run에서는 해당 check를 비활성화하거나, 실제 consumer processing + HTTP 200 + action success를 별도의 fire-and-forget completion evidence로 사용한다.

어떤 correction도 이 forensic에서 구현하지 않았다.
