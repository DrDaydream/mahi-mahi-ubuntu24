#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
ExperimentRoot=/home/ubuntu/experiments
UpstreamToolkit="$ExperimentRoot/figure-tools-mahi-upstream"
UpstreamOutput="$ExperimentRoot/paper-results-v3.5-60s-mahi-upstream"
test -f "$UpstreamToolkit/source-lock.json"

# Recover only recorded Mahi pools from prior campaigns. The matrix lock
# rejects cleanup while another matrix still owns the controller.
for OldCampaign in \
  paper-results-v3.5-60s-zero-output-resume \
  paper-results-v3.5-60s-mahi-warmup10 \
  paper-results-v3.5-60s-mahi-warmup10-crash-a1 \
  paper-results-v3.5-60s-mahi-warmup0-crash-a1
do
  if [ -d "$ExperimentRoot/$OldCampaign/lifecycle/Mahi-Mahi" ]; then
    python3 "$UpstreamToolkit/run_matrix.py" \
      --protocol Mahi-Mahi --root "$ExperimentRoot" \
      --output "$ExperimentRoot/$OldCampaign" --cleanup-only
  fi
done

mkdir -p "$UpstreamOutput"
ControllerLog="$UpstreamOutput/controller-mahi-$(date -u +%Y%m%dT%H%M%SZ).log"
printf 'Controller log: %s\n' "$ControllerLog"
python3 -u "$UpstreamToolkit/run_matrix.py" \
  --protocol Mahi-Mahi --root "$ExperimentRoot" \
  --output "$UpstreamOutput" --retry-failed 2>&1 | tee "$ControllerLog"
