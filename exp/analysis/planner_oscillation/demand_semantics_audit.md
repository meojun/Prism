# Demand-estimator semantics audit

## What Algorithm 1 consumes

`weighted_token_rate_j = (input_rate_j + decode_token_tput_j) × cell_size_j / tpot_slo_j`
(`kvpr_global.py:112-123`, divided by 2^30 in `kvpr_global_v3.py:34-36`)

| term | what it measures | window | placement-dependent? |
|---|---|---|---|
| `input_rate` | prompt tokens that **arrived** in the last 30 s ÷ 30 | 30 s sliding, recomputed per decision | **No** — arrivals are set by the workload |
| `decode_token_tput` | `decode_token_count / elapsed_time`, i.e. tokens the engine **actually produced** | ~1 s, counters reset each report (`scheduler.py:438-470`) | **Yes** |

## Why `decode_token_tput` is endogenous — three source facts

1. **It is achieved service, not offered demand.** `decode_token_count += decode_tokens`
   (`scheduler.py:1510`) counts tokens the engine emitted. Co-locating three
   models on one GPU slows every one of them, so the measured value falls
   *because of the placement Algorithm 1 is choosing*.
2. **It is not reported while a model is deactivated.** The reporting loop is
   gated on `self._activated` (`scheduler.py:436`), so during a migration the
   controller receives nothing and keeps the last value; the controller never
   ages or resets it (`controller_global.py:354` only overwrites on receipt).
3. **`last_tput_update_time` is only advanced inside that gate**
   (`scheduler.py:470`). After a deactivation the next report divides by an
   `elapsed_time` that spans the whole outage, so the first post-migration value
   is **artificially low**.

Together: a migration suppresses the migrated model's measured demand, then a
spike follows as it resumes. That is a closed loop through the scheduler.

## What the paper reference specifies

`docs/paper_faithful/design_analysis.md` §5 records the `token_rate` definition
as **"부분적"** — partial:

> | `token_rate` 정의 | 부분적 — "새로 admit 된 입력 토큰 + 실행 중인 디코드 토큰" |
> 창 내 도착 기준 `input_tok/s` **+** 창 내 디코딩 요청 기준 `output_tok/s` |

The description names the two components (input tokens + decode tokens) but
**does not state whether the decode component is offered or achieved**.

**PAPER_DEMAND_SEMANTICS = UNKNOWN (partial).** No inference is made here.

## A discrepancy between documented intent and code

The design note says our decode term is *"창 내 디코딩 요청 기준 output_tok/s"* —
an output rate over **the same 30 s window** as the input term. The code does not
do that: it takes `decode_token_tput`, an engine-reported achieved rate over
~1 s, gated on activation. The documented intent is window-symmetric with the
input term; the implementation is not.

This is recorded as an observation, not a defect claim: the note is a design
document, not a specification, and no requirement is being violated.

## Prior record of the same oscillation

§5a of the same document already observed this behaviour at project start:

> 90초 스모크 워크로드에서 `τ = 0.10` 으로 Algorithm 1 을 돌리면 38회 배치 패스 중
> 8회 마이그레이션이 발생했고, **그것들은 진동했다**: `model_4` 1→0, `model_1` 0→1,
> `model_5` 1→0, `model_4` 0→1, `model_4` 1→0, …

and diagnosed it structurally:

> 목적함수가 평평하므로 argmin 은 **추정 잡음**이 결정한다.
> 개선폭: 평균 +0.002 표준편차 0.175 … τ = 0.10 → 패스의 33 % 에서 마이그레이션,
> τ = 0.35 → 4 %

τ was the mitigation chosen at the time. The evaluation now runs **τ = 0.00035**,
three orders of magnitude below that value, selected later by a calibration on a
different (6-model, bursty r20) workload. Measured today, the line-8 delta at
each emitted migration is a median **45–47× τ**, i.e. the gate no longer filters
anything. The same oscillation, on the same model, is back.

This connection is reported as a finding. **No τ change is proposed here** — the
calibration evidence points the other way on goodput (Phase 2 §8), and τ changes
are out of scope for this phase.
