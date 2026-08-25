# Token-rate estimator: paper vs design doc vs code

## Source of paper text

The paper was **not** present in this repo. Prior phases worked from a
second-hand summary — `docs/handoff/TP_FINAL_HANDOFF_PROMPT.md:24` records that
this had already caused two corrections. For this audit the OSDI'26
camera-ready was retrieved and text-extracted:

- `https://www.usenix.org/system/files/osdi26-yu-shan.pdf`
- sha256 `b2af6f908a07adbb037c0dc14ccebfa51b7635c2ec614f1a0d39d8eea4017347`
- extracted text kept at `paper_osdi26_extracted.txt`

Note the arXiv version (2505.04021v2) has **no Appendix A.4** — its appendix
stops at A.2. Statements below are from the camera-ready only.

### Verbatim paper text

§4 (KVPR definition):

> KVPR ... calculated as `w_token_rate / shared_kv`, where
> `w_token_rate = token_rate * token_size / SLO` represents the SLO-weighted
> token memory usage rate of a model ... **By counting both input tokens from
> newly admitted requests and decode tokens produced by running requests per
> unit time, token_rate captures the full KV-cache growth rate** and helps KVPR
> accurately reflect GPU memory pressure. We use the TPOT SLO for SLO ...

Appendix A.4, *Load Monitoring Window Size*:

> Figure 15(b) examines the impact of **the sliding window size used to
> calculate the moving average of token rates for the KVPR**. This parameter
> controls the sensitivity of the placement algorithm: a small window makes the
> scheduler sensitive to transient bursts, while a large window focuses on
> long-term trends. The results demonstrate that Prism is generally robust to
> variations in window size. We observe that **a window size of approximately
> 60 seconds provides a stable estimation of memory pressure**, effectively
> smoothing out short-term noise while remaining responsive enough to shift
> resources during sustained workload changes.

Appendix A.4 also gives the idle-eviction optimum as **~45 s**, which is what
`design_analysis.md` already cited.

τ: the paper states only "proceeds only if the improvement exceeds a threshold
τ". **No numeric value** — our documentation is correct on this point.

## Correction to our own documentation

`docs/paper_faithful/design_analysis.md:154` records the token-rate measurement
window as **"명시 없음"** (not specified) and line 155 says the same of the
smoothing method. Against the camera-ready both are **wrong**: A.4 specifies a
sliding-window moving average and reports ~60 s as the stable choice. The design
doc was evidently written against a version without A.4. This is a documentation
error, not an implementation one — the 30 s window was chosen for a defensible
reason (matching the prototype's own tracker so window length is not a confound
between arms), it was simply believed to be unconstrained when it was not.

## Audit table

| field | paper semantics | design doc | current code (before) | proposed code (after) |
|---|---|---|---|---|
| input token rate | input tokens of newly admitted requests, per unit time | 창 내 도착 기준 `input_tok/s` | `kvpr_global.py:112` — prompt tokens with `arrival_time` inside `rate_window`, ÷ `rate_window` | unchanged |
| decode token rate | decode tokens produced by running requests, per unit time | 창 내 디코딩 요청 기준 `output_tok/s` | `kvpr_global.py:113` — cached scalar `queue.decode_token_tput`, an achieved throughput over a ~1 s engine interval | tokens reported within `rate_window`, ÷ `rate_window` |
| sliding-window behaviour | A.4: moving average of token rates over a sliding window | believed unspecified; 30 s chosen | **input only**; decode has no window | both components share one window |
| window length | A.4: robust; ~60 s stable | 30 s | 30 s (`kvpr_rate_window`) | **30 s, deliberately unchanged** (see below) |
| update cadence | not stated | not stated | input: recomputed each 5 s cycle; decode: overwritten whenever an engine report arrives (~1 s) | both evaluated at cycle time over the window |
| behaviour during migration / deactivation | not stated | not stated | engine gated on `self._activated` (`scheduler.py:436`) → no report sent → controller keeps the last value | no reports → tokens age out of the window → rate decays to 0 |
| stale-value behaviour | not stated | not stated | value persists indefinitely; controller never ages it (`controller_global.py:354`) | impossible by construction; events expire by wall clock |
| window expiration | implied by "sliding window" | 창 내 산술평균 | none for decode | `deque` pruned at `now - window` |
| elapsed-time accounting | not stated | not stated | `last_tput_update_time` advanced only inside the `_activated` branch (`scheduler.py:470`) → first post-reactivation report divides ~1 s of tokens by the whole inactive gap | timer advances every second regardless of activation |

Everything marked "not stated" is genuinely absent from the paper and is
recorded as an implementation choice, not a fidelity claim.

## Why the window stays at 30 s in this test

A.4's ~60 s is a *hyperparameter* result. Changing the window at the same time
as the estimator semantics would confound the two. The window remains 30 s here
so the only difference is what the decode component means; window size is left
to a later calibration stage where A.4 is the prior.
