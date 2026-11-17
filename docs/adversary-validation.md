# A0–A5 validation record

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
