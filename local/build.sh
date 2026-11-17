#!/usr/bin/env bash
set -euo pipefail

repo=${1:-"$HOME/experiments/mahi-mahi-source"}
expected=7c928c06bd436c96b94065119e0e8c2d4ac1bcb6
jobs=${MAHI_BUILD_JOBS:-2}

if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    printf 'MAHI_BUILD_JOBS must be a positive integer.\n' >&2
    exit 1
fi
cd -- "$repo"
repo=$(pwd -P)
if [[ "$(git rev-parse HEAD)" != "$expected" ]]; then
    printf 'Expected Mahi-Mahi commit %s; check the repository and checkout.\n' "$expected" >&2
    exit 1
fi
if ! git diff --quiet HEAD --; then
    printf 'Tracked source has changes. Preserve them and use a clean checkout of the pinned commit.\n' >&2
    exit 1
fi
if [[ -f "$HOME/.cargo/env" ]]; then
    source "$HOME/.cargo/env"
fi
command -v cargo >/dev/null
command -v python3 >/dev/null
rustc +1.78.0 --version
cargo +1.78.0 --version
mkdir -p -- "$repo/build-logs"
build_log="$repo/build-logs/build-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
printf 'Building Mahi-Mahi; compiler output is also saved to %s\n' "$build_log"
cargo +1.78.0 build --locked --release --package mysticeti --bin mysticeti \
    --jobs "$jobs" --target-dir "$repo/target" 2>&1 | tee "$build_log"

python3 - "$repo" "$expected" "$build_log" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys

repo = Path(sys.argv[1])
binary = repo / 'target/release/mysticeti'
record = {
    'repository': 'https://github.com/PasinduTennage/mahi-mahi-consensus.git',
    'commit': sys.argv[2],
    'rustc': subprocess.check_output(['rustc', '+1.78.0', '--version'], text=True).strip(),
    'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    'cargo_lock_sha256': hashlib.sha256((repo / 'Cargo.lock').read_bytes()).hexdigest(),
    'build_log': sys.argv[3],
}
(repo / 'target/mahi-build.json').write_text(json.dumps(record, indent=2) + '\n')
print('Built:', binary)
print('Build record:', repo / 'target/mahi-build.json')
PY
"$repo/target/release/mysticeti" --help
