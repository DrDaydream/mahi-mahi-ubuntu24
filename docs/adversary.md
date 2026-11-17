# Reproducible A0–A5 block scheduling experiments

The strategy implementation lives in `crates/mysticeti-core/src/adversary.rs`. Core calls it when creating a local proposal, the committer exposes its final local leader statuses for observation, and the synchronizer passes outgoing blocks through it. Quorum, signature verification, direct/indirect decision rules and leader election are unchanged.

Keep new adversary policy in that single Rust file: mode/config validation,
fork construction, target and recipient selection, finite attack windows,
bounded queues, release timers and strategy tests. The rest of Rust only wires
the module into proposals, leader observation and own/forward/fetch sends.
Storage handles atomic WAL persistence of the resulting blocks, and the commit
observer measures actual output independently of the adversary's decisions.

This is an experimental local-observation scheduler. It does not promise to prevent a chosen leader from committing: available DAG support, delivery races, honest forwarding and the original skip/indirect rules determine the outcome. An attempted split, a failed direct commit, an undecided slot and stalled global progress are different measurements.

| Mode | Behavior |
| --- | --- |
| A0 | Original proposal and block-send path. No Byzantine identities or delayed-send worker. |
| A1 | Fixed Byzantine authors withhold their proposals during a finite attack window. |
| A2 | Up to two valid versions of each Byzantine proposal, with seeded nondirected selective delivery. |
| A3 | Select and construct versions using locally observed DAG support for the scheduled target leader. |
| A4 | Continue from locally observed unresolved slots and relevant later Byzantine leaders; release alternate/critical evidence later. |
| A5 | A4 plus bounded scheduling of honest-to-honest block delivery, including forwarding and digest fetch responses. |

The sender signs only its own configured Byzantine author. Alternatives keep the same transaction statements, creation timestamp and epoch marker, while changing parent references/order. Every generated block must pass the regular signature and previous-round quorum verification; infeasible attempts fall back with an event. Multiple digests do not create additional voting identities.

Two versions are retained by digest in the WAL and published together to the in-memory index, so dissemination cannot pass a half-published pair. The ordinary own-round cursor still points to the primary proposal; the adversary module enumerates the versions for selective dissemination. Fetch responses always contain the requested digest, including after WAL replay/cache unloading. An additional WAL record type (`6`) stores local alternatives without replaying their identical payload as another local application proposal. Attack WALs therefore require the modified binary to reopen.

## Configuration

Fabric uses `fab remote --adversary A3 --byzantine 3` or an explicit comma-separated `--byzantine-ids`. See `aws/README.md` for controller setup and build provenance. `faults` continues to mean omitted crash processes; it is separate from Byzantine identities. The two sets must be disjoint and their total must stay within the fault threshold. An explicit target never changes the fixed Byzantine set.

The generated `public-config.yaml` contains the following object under `parameters.adversary`:

```yaml
adversary:
  mode: A3
  byzantine_authorities: [0, 1, 2]
  seed: 1
  start_after_ms: 20000
  duration_ms: 60000
  delay_ms: 200
  late_delay_ms: 1000
  max_pending_messages: 4096
  max_pending_bytes: 67108864
  max_extra_bytes_per_round: 8388608
  target_authority: null
  target_round: null
```

The Rust default is A0. Fabric resolves a missing attack start to the configured warmup and saves the effective configuration, exact source revision and binary checksum with the run. Keep a recovery interval after the attack. Timing is monotonic relative to each process's initialization; process startup skew must be considered when comparing replica traces. This is not a globally synchronized external controller.

The queue enforces message and serialized-block byte limits across a node's connections. Overflow releases traffic early and records the event, reducing attack strength instead of creating unbounded memory use or artificial permanent isolation. Timed releases do not require another proposal to arrive. Connection loss can abandon queued deliveries; normal reconnection/fetch applies. A0–A4 do not control honest forwarding, so honest dissemination can reveal an alternate version early. A5 controls outgoing honest block paths too. Finite scheduler delay is separate from ordinary network backpressure.

This Mahi-Mahi implementation uses deterministic round-robin leaders, not a hidden threshold common coin. Attack modes support wave length 4 (Propose, Boost, Vote, Certify) or 5 (Propose, Boost, Boost, Vote, Certify); the last voting round is the target round plus wave length minus 2. Attack runs enable the existing synchronizer. Results describe this implementation and must not be presented as attacks on an implemented randomized leader protocol.

The preexisting Mahi-Mahi skip rule can skip a leader when a quorum fails to support one of its known versions, even if another version has stronger support. Direct skip is checked before direct commit. This change preserves those rules; splitting delivery can produce a skip rather than keep the slot unresolved. Record the actual per-replica outcome.

## Interpreting results

Retain per-replica scheduling and local decision events. For each chosen slot, distinguish actual support for each digest, direct/indirect commit, skip and the duration of being unresolved. Measure recovery after the configured attack window. Report queue overflow, failed/infeasible fork generation, additional fork bytes and configuration alongside throughput and latency.

Use identical Byzantine identities, seed, load, duration, hardware and recovery windows across modes. A2–A5 repeat transaction statements in alternate proposals: report unique committed transaction identifiers where available, and do not interpret extra DAG blocks as additional useful client throughput. A5 belongs to a stronger network-adversary model than A0–A4 and should be labeled separately in paper figures.

### Fork block output proportion

The benchmark exports `committed_blocks_total{kind="first"}` and
`committed_blocks_total{kind="extra_fork"}`. These count **non-genesis blocks
actually emitted in committed sub-DAGs**, after the linearizer's digest
deduplication. The first output digest for an `(author, round)` slot increments
`first`; each later distinct digest for that slot increments `extra_fork`.
Merely generating, receiving, forwarding or storing a fork increments neither.
This is an additional-version ratio, not the fraction of blocks authored by
Byzantine nodes or the fraction of every block belonging to an equivocated slot.

For each replica and measurement window:

```
committed_blocks = delta(first) + delta(extra_fork)
extra_fork_blocks = delta(extra_fork)
fork_output_ratio = extra_fork_blocks / committed_blocks
```

For example, outputting 20 non-genesis blocks including 5 additional versions
gives `5 / 20 = 25%`. If two digests of a slot are both output, only one is an
additional version. If only one is output, its contribution to the numerator is
zero. Slot history persists across warmup and measurement windows, and is rebuilt
from recovered committed references after restart. A later digest is therefore
still classified correctly if the first digest was output before the window.
Process counters reset on restart; windows that observe a reset are invalid.

AWS JSON/CSV includes the two window counts and the ratio (a fraction in `[0,1]`)
per replica. Terminal `SUMMARY` and `summary.txt` display counts and percentages,
plus an arithmetic mean over replicas with observed block output and a separate
honest-replica mean. Replica outputs are never summed to claim global output.
Zero block output or old binaries without these counters gives `N/A` / JSON
`null`, not zero percent. Old result files cannot reconstruct this measurement.

Zero-transaction-output replicas retain zero TPS and undefined latency. Mean
latency uses only replicas with positive transaction output; an entirely stalled
measurement still produces a report. TPS remains the raw shared transaction-entry
rate, without deduplication of transaction payloads across forks. The fork-block
ratio does not mathematically correct this TPS into unique-transaction goodput.

`enable_synchronizer=true` now enables actual missing-parent requests. The
previous inverted Rust check disabled fetching when this setting was true. The
fix requires rebuilding through `fab install`; A0 comparisons should use the
same corrected binary as the attack runs.

See [validation record](adversary-validation.md) for the executed tests, node/test
compilation checks and six core-test failures also reproduced on the unchanged
baseline. Rust regressions include an opt-in real-crypto scope because legacy
DAG unit fixtures use zero digests. Local checks do not demonstrate distributed
attack outcomes. No cloud experiment or deployment was performed.
