#!/usr/bin/env bash
set -euo pipefail
source_dir=$1
revision=$2
jobs=$3
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$jobs" =~ ^[1-9][0-9]*$ ]] || exit 2
cd -- "$source_dir"
rm -f -- .mahi-build.json .mahi-build.json.tmp
[[ "$(git rev-parse HEAD)" == "$revision" ]] || { echo 'Source revision mismatch' >&2; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked source files are modified' >&2; exit 1; }
export RUSTFLAGS='-C target-cpu=native'
export CARGO_BUILD_JOBS="$jobs"
echo "Building Mahi-Mahi commit $revision with Rust 1.78.0 ($jobs parallel jobs)..."
cargo +1.78.0 build --locked --release -p mysticeti --bin mysticeti --target-dir target
test -x target/release/mysticeti
target/release/mysticeti --help >/dev/null
binary_sha=$(sha256sum target/release/mysticeti)
binary_sha=${binary_sha%% *}
printf '{"commit":"%s","rust_toolchain":"1.78.0","rustflags":"-C target-cpu=native","sha256":"%s"}\n' \
    "$revision" "$binary_sha" > .mahi-build.json.tmp
mv -- .mahi-build.json.tmp .mahi-build.json
echo "Build verified: $binary_sha"
