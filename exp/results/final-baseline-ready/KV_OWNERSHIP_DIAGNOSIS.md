# KV allocator ownership: where model_2's 930,401 tokens came from

Diagnosis only. Nothing about the scheduler or the planner was changed, and no
token skip, timeout, relocation, tau or c_i was touched. The instrumentation is
off unless `PRISM_KV_OWN_TRACE=1` and is read by nothing but the log.

## Answer

**There is no orphaned KV. The pools were nearly empty.**

`token usage: 1.00` and `#token: 930401` in D3 run 7 did not mean model_2's pool
was full of KV nobody had returned. Both numbers are derived from

```python
num_used = max_total_num_tokens - (available_size() + evictable_size())
```

and on the elastic `use_kvcached_v0` path `available_size()` is

```python
min(_physical_free_size(0.5), kv_allocator.available_size())
```

The first term is **device-wide**: free memory on the whole GPU. So when a GPU
tightens, every pool on it reports a jump in "used" at the same instant,
whatever each one actually holds -- and `token usage` reports each of them as
nearly full.

## The measurement

One D3 run with the trace on, same workload and settings. 1,695 ownership
records. For each, the pool's own occupancy is reconciled against who can
account for it:

```text
pool_used = owned_running + owned_waiting + owned_pending_adoption
          + owned_inflight + migration_owned + tree_evictable + unexplained
```

Terminal state, per pool:

| model | engine | pool total | available | pool_used | live-owned | migration | unexplained | running |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| model_1 | 0_0 | 819,200 | 611,908 | 207,292 | 4,559 | 0 | 202,733 | 19 |
| model_1 | 1_3 | 819,200 | 516,951 | 302,249 | 1,195 | 0 | 301,054 | 2 |
| model_2 | 1_0 | 933,888 | 573,041 | 360,847 | **400** | 0 | 360,447 | 1 |
| model_3 | 1_1 | 233,472 | 148,631 | 84,841 | 875 | 0 | 83,966 | 2 |
| model_4 | 0_1 | 724,992 | 572,698 | 152,294 | 744 | 0 | 151,550 | 2 |
| model_5 | 0_2 | 204,800 | 179,890 | 24,910 | 10,689 | 0 | 14,221 | 20 |
| model_6 | 0_3 | 466,944 | 362,091 | 104,853 | 406 | 0 | 104,447 | 1 |
| model_6 | 1_2 | 466,944 | 294,595 | 172,349 | 318 | 0 | 172,031 | 1 |

model_2 is the case from run 7: it reports 360,847 tokens "used" while its live
requests hold **400**, and it has 573,041 tokens available. It is not full and
nothing has leaked.

## Why "unexplained" is an artifact and not a leak

The decisive observation is that the pools move **together**. In one 0.84-second
step:

```text
model_2   available 896,785 -> 597,737   (-299,048)   live-owned 240 -> 280
model_1   used       20,470 -> 293,269   (+272,799)   live-owned 9,592 -> 4,523
model_3   used        9,601 ->  84,050   ( +74,449)   live-owned   387 ->   485
model_6   used       15,448 -> 180,224   (+164,776)   live-owned 5,224 ->     0
```

Four independent pools on GPU1 all report a large jump in occupancy inside the
same second, while the KV their live requests hold goes *down*. Four
simultaneous leaks is not a hypothesis worth entertaining; one shared quantity
falling is. That quantity is the device-wide physical free memory that
`available_size()` takes the minimum against.

Across the run, 321 of 1,695 records show live ownership below 5% of reported
`pool_used`. The largest "unexplained" is model_2's 360,447 -- with 573,041
tokens still available.

## What this means for run 7

The missing link in the run 7 chain is filled in, and it is not a leak:

> model_2 reported `token usage: 1.00` because `available_size()` had collapsed
> for a **device-wide** reason, not because its own pool was full. `PrefillAdder`
> is constructed with `available_size() + evictable_size()` as its token budget,
> so once GPU1's physical free memory is tight, *no* pool on that GPU can admit a
> prefill however empty it is -- and sequence 5472, already backend-admitted,
> could never start, so the per-GPU admission token never advanced.

This traces to the elastic-memory admission bound added in `ad8a76c` and
reinstated in `824caa7`. Before it, the `use_kvcached_v0` path returned the
allocator's own pool-local availability, so one model's prefill budget was not
coupled to what every other model on the GPU was holding. The bound was added
for a real failure -- run 4's activation OOM -- and this is its cost, now
measured.

## What this diagnosis does not establish

The traced run stalled in a *different* terminal state than run 7: at the stop,
GPU1's pools reported healthy availability (286k, 505k, 294k tokens) and every
waiting queue was empty, so its engines were not blocked on KV at all -- nothing
was reaching them. That is a dispatch-side question, not an ownership one, and
it is not answered here. Run 7's terminal state was not reproduced; what was
answered is the question that was asked -- whose KV the pool was holding.

`prefill_refused` never fired in this run, for the same reason: no engine ever
had a waiting request it could not place.
