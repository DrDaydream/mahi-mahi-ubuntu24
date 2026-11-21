# Upstream restoration validation — 2026-11-21

Base: `db58361c6d500f3206b1c0ca0266292b0354a65a`.
Upstream reference: `7c928c06bd436c96b94065119e0e8c2d4ac1bcb6`.

- Full Python AWS/local suite: **283 passed**. Includes cumulative versus window
  statistics, startup observations, mixed/all zero output, malformed/reset
  counters, live and offline summaries, native A0/crash/A3/A5 launch integration,
  source provenance and toolkit preparation/conflict handling.
- Toolkit tests validate all **96 Mahi cases** and **480 total plan rows**.
  24 result fixtures cover four modes, three committee sizes and zero/nonzero
  output. Stale source/basis, missing logs, incorrect launch flags and counter
  resets are rejected. Dry-run requires no AWS connection.
- Rust missing-parent fetch regressions: **2 passed** on Rust 1.85. Both first
  failed on the previous condition, then passed on the restored upstream flag.
- Existing Rust adversary regressions: **14 passed** on Rust 1.85. The strategy
  source, normal proposal integration, commit observation and transaction
  timestamp code remain byte-identical to `db58361`.
- `cargo check --locked -p mysticeti-core -p mysticeti --tests`: **passed** on
  Rust **1.78.0**, matching the installer, with native CPU flags and two jobs.
  The same check also passed on Rust 1.85. Existing warnings remain.
- Original toolkit `lifecycle.py` SHA-256 remains
  `1b5664d2ef967d3db898ab5281962f54f92a46e8055be3ef2b0d6c2a518035ee`.
  The isolated toolkit retains the original serial pool lifecycle and cleanup.
- CSV/PNG/PDF export checked with explicitly synthetic A0/A1/A3/A5 fixtures.
  These fixtures are not measured performance results. Zero-observation
  arithmetic results are labeled separately.
- Bash syntax and Git whitespace validation pass.

No EC2, SSH, AWS provisioning or distributed performance experiment was executed.
This validation type-checks the node but does not build an EC2 release binary.
The full Rust core suite was not rerun; the historical six baseline failures
are documented in [the earlier validation record](adversary-validation.md).
