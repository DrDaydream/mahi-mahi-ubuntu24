# A0–A5 validation record

## Runtime metrics and experiment reporting — 2026-11-18

Baseline: `79b71d2794a6049e604e421a67a94e4ae9891f42`. Validation was performed
in a temporary Ubuntu 24.04 amd64 WSL2 environment with Rust 1.78.0, and on
the Windows controller for offline Python checks. No EC2 connection, cloud
experiment or deployment was performed.

The A0–A5 strategy remains in `crates/mysticeti-core/src/adversary.rs`; its only
change in this revision documents the module boundary. This revision adds
committed fork-output counters, zero-progress-aware AWS reports and corrects
the inverted missing-parent fetch enable flag. It keeps the existing Fabric
create/install/remote interface and source-directory arrangement.

Completed checks:

- Full offline Python suite: **229 passed**. This includes zero output from
  startup, all-zero windows, fork counter deltas and invalid resets, offline
  reporting, prebuilt-only execution, and cleanup after report disk/pipe errors
  or Ctrl-C during output.
- Rust adversary tests: **14 passed** using actual cryptographic digests and
  signatures where required.
- New committed-output regression: **1 passed**. Covers genesis exclusion,
  uncommitted forks, distinct committed versions, initialized zero metrics and
  slot-history recovery.
- New synchronizer behavior regressions: **2 passed**. The tests exercise the
  real missing-parent fetch loop with enabled/disabled settings; both failed
  on the old inverted condition before the fix.
- Complete Rust core library suite: **65 passed, 6 failed, 1 ignored**. The
  network synchronization and validator commit/crash/synchronization smoke
  tests passed. This is not an all-green core suite.
- `cargo check --locked -p mysticeti-core -p mysticeti --tests`: **passed**.
  Rust 1.78.0, native CPU flag, two build jobs, no incremental compilation and
  dev debug info disabled were used. Existing compiler warnings remain.
- Independent source review and cleanup re-review: completed with no remaining
  material findings. Git whitespace validation passed.

All six core failures were reproduced on a clean checkout of the baseline,
after cleaning the core package's shared build artifacts to force recompilation:

| Test | Baseline result |
| --- | --- |
| `consensus::tests::base_committer_tests::direct_skip` | assertion failure |
| `consensus::tests::base_committer_tests::no_leader` | assertion failure |
| `consensus::tests::multi_committer_tests::direct_skip` | assertion failure |
| `consensus::tests::multi_committer_tests::no_leader` | assertion failure |
| `consensus::tests::pipelined_committer_tests::no_leader` | assertion failure |
| `syncer::tests::test_syncer` | no Tokio reactor when constructing the commit handler |

The baseline consensus selection finished with 27 passed / 5 failed; its exact
syncer selection failed with the same missing-runtime error. No consensus rule
or unrelated test fixture was changed to make these failures disappear.

These checks execute local tests and type-check the node and test targets. They
do not measure a distributed attack's effectiveness, build a release binary,
run the simulator feature, or establish unique-transaction throughput. Actual
EC2 experiment results still need to be collected with `fab install` and
`fab remote` using this revision.

## Initial A0–A5 implementation — 2026-11-17

Validation date: 2026-11-17. Source baseline: `e425c72d25398bee5c60fde7f8db75f513a0dde6`.

The user authorized a compilation check after implementation. In a temporary Ubuntu 24.04 amd64 WSL2 environment, using the repository's Rust 1.78.0 toolchain, the following check passed with exit code 0:

```bash
RUSTFLAGS='-C target-cpu=native' \
CARGO_BUILD_JOBS=2 CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=0 \
cargo +1.78.0 check --locked -p mysticeti-core -p mysticeti --tests
```

The first invocation found two preexisting `E0061` errors in `test_util.rs`: default-feature test helpers called `TestCommitHandler::new` without its required authority argument. Both call sites now pass `core.authority()`. The incremental recheck completed in 3.45 seconds with exit code 0. Existing unused-import/variable and ignored-I/O-result warnings remain; no new adversary-module warning was reported. `Cargo.lock` was preserved.

This check includes normal library/binary code and test-target type checking. It does not execute Rust tests, link a release executable, run the simulator feature or validate multi-node attack results.

Other completed checks:

- Fabric adapter Python suite: **147 passed**.
- Shell syntax checks for install, source sync and build scripts: passed.
- Python syntax and public example settings validation: passed.
- Direct Rust parser checks, adversary module formatting and Git whitespace checks: passed.
- Independent source review and scoped re-review: completed. Corrected WAL tag-6 lazy loading, atomic fork-pair publication, active vote-target priority and the earliest eligible future-anchor round.

Rust regressions cover baseline preservation, valid distinct forks with identical payload, distinct-author quorum accounting, DAG-directed parent/recipient choices, consecutive leaders, rolling/fixed targets, exact-digest fetching, finite alternate release, A5 own/forward/fetch delivery, queue bounds and cancellation cleanup. A dedicated Mahi test covers both four- and five-round waves through the actual last voting round. Test keys match the deterministic per-authority benchmark committee. A storage regression covers fork persistence, the primary cursor and WAL replay.

No cloud instance, distributed experiment or deployment was started. The existing Mahi implementation uses deterministic round-robin leaders and its existing skip rule; runtime results must be interpreted for that implementation. Compilation does not establish that a selected target will fail to commit or remain undecided.
