"""Parse cumulative Mahi-Mahi shared-workload metrics and measure their deltas."""

import math
import re
from statistics import fmean


_METRICS = {"latency_s_count": "count", "latency_s_sum": "sum"}
_LEADERS = "committed_leaders_total"
_BLOCKS = "committed_blocks_total"
_DURATION = "benchmark_duration"
WINDOW_BASIS = 'window-delta-v1'
UPSTREAM_BASIS = 'upstream-terminal-cumulative-v1'
_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_SAMPLE = re.compile(
    r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?[ \t]+"
    r"(\S+)(?:[ \t]+[+-]?[0-9]+)?[ \t]*"
)
_LABEL = re.compile(
    r'\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:\\[\\n"]|[^"\\])*)"\s*(?:,|$)'
)
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _labels(text: str) -> dict[str, str]:
    labels = {}
    position = 0
    while text[position:].strip():
        match = _LABEL.match(text, position)
        if match is None:
            raise ValueError(f"Malformed metric labels: {text!r}")
        name, value = match.groups()
        if name in labels:
            raise ValueError(f"Duplicate metric label: {name}")
        labels[name] = re.sub(
            r'\\([\\n"])',
            lambda escaped: {"n": "\n", "\\": "\\", '"': '"'}[escaped.group(1)],
            value,
        )
        position = match.end()
    return labels


def _nonnegative(value, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{context} must be a finite nonnegative number") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{context} must be a finite nonnegative number")
    return number


def parse_snapshot(text: str) -> dict:
    """Read one validator's cumulative counters from Prometheus text.

    ``count`` and ``sum`` (seconds) must describe one unambiguous histogram
    series with ``workload="shared"``. Their complete label sets must match;
    label order and extra labels are accepted. Owned workload rows, buckets,
    and unrelated metric families are ignored. Missing shared metrics raise
    ValueError, including during startup; the caller decides when to retry.

    ``committed_leaders`` sums the direct-commit and indirect-commit series
    across authorities in this validator's scrape. Skip statuses are excluded,
    and an absent committed-leader metric defaults to zero.
    """
    result = {"committed_leaders": 0.0}
    shared_labels = {}
    leader_series = set()
    block_counts = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        name_match = _METRIC_NAME.match(line)
        if name_match is None:
            continue
        name = name_match.group()
        if name not in _METRICS and name not in (_LEADERS, _BLOCKS, _DURATION):
            continue
        sample = _SAMPLE.fullmatch(line)
        if sample is None:
            raise ValueError(f"Malformed {name} sample on line {line_number}")
        labels = _labels(sample.group(2) or "")
        if name in _METRICS and labels.get("workload") != "shared":
            continue
        if name == _LEADERS and labels.get("commit_type") not in {
            "direct-commit", "indirect-commit"
        }:
            continue
        raw_value = sample.group(3)
        if _NUMBER.fullmatch(raw_value) is None:
            raise ValueError(f"Malformed {name} value on line {line_number}")
        value = _nonnegative(raw_value, f"{name} on line {line_number}")
        if name == _DURATION:
            if labels or name in result or not value.is_integer():
                raise ValueError('Invalid or duplicate benchmark_duration counter')
            result[name] = value
        elif name == _BLOCKS:
            kind = labels.get('kind')
            if kind not in ('first', 'extra_fork') or kind in block_counts:
                raise ValueError('Invalid or duplicate committed_blocks_total kind')
            if not value.is_integer():
                raise ValueError('committed_blocks_total must be an integer')
            block_counts[kind] = value
        elif name == _LEADERS:
            series = tuple(sorted(labels.items()))
            if series in leader_series:
                raise ValueError("Duplicate committed_leaders_total series")
            leader_series.add(series)
            result["committed_leaders"] += value
        else:
            key = _METRICS[name]
            if key in result:
                raise ValueError(f"Duplicate or ambiguous shared {name} series")
            result[key] = value
            shared_labels[key] = labels
    for key in _METRICS.values():
        if key not in result:
            raise ValueError(f"Missing shared latency_s_{key}")
    if shared_labels["count"] != shared_labels["sum"]:
        raise ValueError("Shared latency_s_count and latency_s_sum labels differ")
    if block_counts:
        if set(block_counts) != {'first', 'extra_fork'}:
            raise ValueError('Incomplete committed block counters')
        result['committed_blocks'] = sum(block_counts.values())
        result['extra_fork_blocks'] = block_counts['extra_fork']
    return result


def summarize(
    before: dict[str, dict], after: dict[str, dict], elapsed: dict[str, float]
) -> dict:
    """Summarize one measurement window using each replica's counter deltas.

    The three mappings must contain identical, nonempty authority key sets.
    ``elapsed`` contains per-node seconds between scrape midpoint timestamps
    measured by the caller's monotonic clock. Returned ``nodes`` preserve the
    input order and contain ``authority``, ``committed_transactions``, ``tps``,
    ``mean_latency_ms``, and ``duration_s``.

    ``replica_tps_mean`` is the arithmetic mean of replica TPS, never their
    sum. ``mean_latency_ms`` is the arithmetic mean of per-replica latency
    means, not a count-weighted mean. All counters are finite and nonnegative;
    any reset or missing counter raises ValueError. Replicas with no new
    transactions contribute zero TPS and have undefined (None) latency. The
    aggregate latency averages only replicas with new commits, or is None
    when none progress. Optional ``committed_leaders`` defaults to zero and is checked for
    resets; it does not substitute for committed transaction progress.
    """
    if not before or set(before) != set(after) or set(before) != set(elapsed):
        raise ValueError("Node sets in before, after, and elapsed must be identical and nonempty")
    nodes = []
    for authority, start in before.items():
        duration = _nonnegative(elapsed[authority], f"Node {authority} duration_s")
        if duration == 0:
            raise ValueError(f"Node {authority} duration_s must be positive")
        end = after[authority]
        deltas = {}
        for key in ("count", "sum", "committed_leaders"):
            if key != "committed_leaders" and (key not in start or key not in end):
                raise ValueError(f"Node {authority} is missing {key}")
            first = _nonnegative(start.get(key, 0), f"Node {authority} before {key}")
            last = _nonnegative(end.get(key, 0), f"Node {authority} after {key}")
            if last < first:
                raise ValueError(f"Node {authority} counter reset: {key}")
            deltas[key] = last - first
        count = deltas["count"]
        if count == 0 and deltas["sum"] != 0:
            raise ValueError(f"Node {authority} latency sum increased without new commits")
        block_fields = ('committed_blocks', 'extra_fork_blocks')
        blocks = extras = ratio = None
        if any(key in snapshot for snapshot in (start, end) for key in block_fields):
            if not all(key in snapshot for snapshot in (start, end) for key in block_fields):
                raise ValueError(f'Node {authority} has incomplete committed block counters')
            values = []
            for snapshot in (start, end):
                total, extra = (_nonnegative(snapshot[key], key) for key in block_fields)
                if not total.is_integer() or not extra.is_integer() or extra > total:
                    raise ValueError(f'Node {authority} has inconsistent committed block counters')
                values.append((total, extra))
            blocks, extras = (last - first for first, last in zip(*values))
            if blocks < 0 or extras < 0 or extras > blocks:
                raise ValueError(f'Node {authority} has reset or inconsistent committed block counters')
            ratio = extras / blocks if blocks else None
        nodes.append(
            {
                "authority": authority,
                "committed_transactions": count,
                "tps": count / duration,
                "mean_latency_ms": deltas["sum"] / count * 1000 if count else None,
                "duration_s": duration,
                "committed_blocks": blocks,
                "extra_fork_blocks": extras,
                "fork_output_ratio": ratio,
            }
        )
    latencies = [node["mean_latency_ms"] for node in nodes if node["committed_transactions"] > 0]
    ratios = [node['fork_output_ratio'] for node in nodes if node['fork_output_ratio'] is not None]
    return {
        "nodes": nodes,
        "replica_tps_mean": fmean(node["tps"] for node in nodes),
        "mean_latency_ms": fmean(latencies) if latencies else None,
        "all_nodes_progress": all(node["committed_transactions"] > 0 for node in nodes),
        "fork_output_ratio_mean": fmean(ratios) if ratios else None,
        "fork_ratio_contributors": len(ratios),
    }


def summarize_run(before, after, elapsed, basis=WINDOW_BASIS):
    """Reproduce the selected, recorded statistics basis.

    The upstream terminal averages each replica's last cumulative sum/count,
    including startup. A recorded zero-count replica contributes zero. Its TPS
    is max(last count) // max(last benchmark_duration), not a replica TPS mean.
    Keep the before/after validation even for cumulative reports so resets or
    incomplete runs cannot be silently accepted. Old archives use window deltas.
    """
    if basis not in (WINDOW_BASIS, UPSTREAM_BASIS):
        raise ValueError(f'Unknown measurement basis: {basis}')
    result = summarize(before, after, elapsed)
    if basis == UPSTREAM_BASIS:
        durations = {}
        zero = {}
        for authority, end in after.items():
            for snapshot in (before[authority], end):
                for key in ('count', _DURATION):
                    if key not in snapshot:
                        raise ValueError(f'Node {authority} is missing {key}')
                    value = _nonnegative(snapshot[key], f'Node {authority} {key}')
                    if not value.is_integer():
                        raise ValueError(f'Node {authority} {key} must be an integer')
                if snapshot['count'] == 0 and snapshot['sum'] != 0:
                    raise ValueError(f'Node {authority} has latency sum without commits')
            if end[_DURATION] < before[authority][_DURATION]:
                raise ValueError(f'Node {authority} counter reset: {_DURATION}')
            durations[authority] = int(end[_DURATION])
            zero[authority] = {key: 0 for key in end}
        # Reuse counter/fork validation with an explicit zero origin.
        result = summarize(zero, after, {i: max(1, d) for i, d in durations.items()})
        latency_ns = []
        for row in result['nodes']:
            authority = row['authority']
            count = int(row['committed_transactions'])
            duration = durations[authority]
            mean_ns = round(after[authority]['sum'] * 1_000_000_000) // count if count else 0
            latency_ns.append(mean_ns)
            row.update(mean_latency_ms=mean_ns / 1_000_000,
                       duration_s=duration, tps=count / duration if duration else 0,
                       has_latency_observations=bool(count))
        duration = max(durations.values())
        result.update(
            mean_latency_ms=(sum(latency_ns) // len(latency_ns)) / 1_000_000,
            replica_tps_mean=fmean(row['tps'] for row in result['nodes']),
            upstream_tps=max(int(end['count']) for end in after.values()) // duration if duration else 0,
            latency_contributors=len(after),
            positive_observation_replicas=sum(end['count'] > 0 for end in after.values()),
        )
    result['measurement_basis'] = basis
    return result
