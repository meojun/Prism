# Many-model rate-grid amendment

## Protocol amendment — final rate grid

```
OLD_RATE_GRID = [4, 8, 12, 16, 20]
NEW_RATE_GRID = [2, 4, 6, 8, 10]
amended       = 2026-08-26T02:11:54.379969+00:00
stage         = 34_many_model_runs
```

**Reason.** Existing experiments already showed the serving stack entering
saturation around r6–r8, so r12–r20 were largely drowned regimes with little
diagnostic value. The new grid also matches the final 4-HET grid exactly, which
enables direct cross-regime comparison at identical offered request rates.

**Timing.** The amendment was made while the Prototype arm was partially
complete and **before any final Prism many-model result had been produced or
inspected**. No final Prism result was used to choose between parameter values.

**Scope.** The final many-model bursty request-rate grid only. Unchanged:
FINAL_TAU (0.012859417696566448), KVPR_WINDOW (60), cooldown (30), runtime
source and commit, Algorithm 1, Algorithm 2, migration semantics, model set,
hot-pair construction, 90/10 skew, 180 s phases, A→B→C order, burst generator,
SLO definitions, seeds [7,8], and the entire final 4-HET protocol.

**Existing Prototype runs.** r4 s7/s8 and r8 s7/s8 were audited against the
amended protocol — verification PASS, lifecycle gate PASS, and trace SHA256
identical to the re-frozen canonical manifest — and are **reused, not re-run**.

**r12.** Preserved, never deleted, never overwritten, and **excluded from all
final statistics**, marked `EXCLUDED_FROM_FINAL_GRID_PROTOCOL_CHANGE`. r12 s7 is
a complete valid run; r12 s8 was in flight and was stopped, and is retained as
incomplete. Neither appears in the final aggregate, rate sweep, hypothesis
statistics or headline metrics.

**Pilot.** The r16 seed 9 pilot is unchanged and remains a diagnostic artifact
only. It was not re-run or redesigned because of this amendment.
