"""Terminal summaries and offline recovery from saved AWS measurement windows."""
import argparse
import json
from pathlib import Path
from statistics import fmean

from .metrics import summarize_run, UPSTREAM_BASIS, WINDOW_BASIS


def number(value, unit=''):
    return 'N/A' if value is None else f'{value:,.3f}{unit}'


def percent(value):
    return 'N/A' if value is None else f'{value * 100:.3f}%'


def count(value):
    return 'N/A' if value is None else f'{value:,.0f}'


def format_summary(record, summary, directory):
    params = record.get('parameters', {})
    attack = record.get('adversary', params.get('adversary', {}))
    hosts = {str(h['authority']): h for h in record.get('hosts', [])}
    byzantine = set(map(str, attack.get('byzantine_authorities', [])))
    rows = summary.get('nodes', []) if summary else []
    basis = (summary or record).get('measurement_basis', WINDOW_BASIS)
    cumulative = basis == UPSTREAM_BASIS
    lines = ['=' * 80, ' SUMMARY — MAHI-MAHI AWS', '=' * 80,
             f"Status: {record.get('status', 'unknown')}", f'Results: {directory}',
             f"Source commit: {record.get('commit', 'N/A')}", '', '+ CONFIG']
    for key, value in params.items():
        if key != 'adversary':
            lines.append(f'  {key}: {value}')
    lines.extend(['', '+ ADVERSARY'])
    for key, value in attack.items():
        lines.append(f'  {key}: {value}')
    lines.extend(['', '+ RESULTS (all measured active replicas)'])
    lines.append(f'  Measurement basis: {basis}')
    if summary:
        positive = [r for r in rows if r['committed_transactions'] > 0]
        zero = [str(r['authority']) for r in rows if r['committed_transactions'] == 0]
        lines.extend([
            f"  Replica mean TPS: {number(summary['replica_tps_mean'], ' tx/s')}",
            f"  Mean shared E2E latency: {number(summary['mean_latency_ms'], ' ms')}",
            f'  Latency contributors: {len(rows) if cumulative else len(positive)}/{len(rows)}',
            f'  Replicas with latency observations: {len(positive)}/{len(rows)}',
            f"  Zero-commit replicas: {', '.join(zero) or 'none'}",
            f"  {'All replicas have cumulative commits' if cumulative else 'All replicas progressed in window'}: {summary['all_nodes_progress']}",
            f"  Mean fork output ratio: {percent(summary.get('fork_output_ratio_mean'))}",
            f"  Fork ratio contributors: {summary.get('fork_ratio_contributors', 0)}/{len(rows)}",
        ])
        if cumulative:
            lines.append(f"  Upstream terminal TPS: {number(summary['upstream_tps'], ' tx/s')}")
        # Separate the honest subset explicitly; keep existing aggregate JSON semantics.
        honest = [r for r in rows if str(r['authority']) not in byzantine
                  and not hosts.get(str(r['authority']), {}).get('byzantine', False)]
        latency = [r['mean_latency_ms'] for r in honest if cumulative or r['committed_transactions'] > 0]
        ratios = [r['fork_output_ratio'] for r in honest if r.get('fork_output_ratio') is not None]
        lines.extend(['', '+ HONEST REPLICAS',
                      f"  Replica mean TPS: {number(fmean(r['tps'] for r in honest) if honest else None, ' tx/s')}",
                      f"  Mean shared E2E latency: {number(fmean(latency) if latency else None, ' ms')}",
                      f'  Latency contributors: {len(latency)}/{len(honest)}',
                      f"  Mean fork output ratio: {percent(fmean(ratios) if ratios else None)}",
                      f'  Fork ratio contributors: {len(ratios)}/{len(honest)}'])
    else:
        lines.append('  TPS / latency: N/A (no valid complete measurement window)')
    lines.extend(['', '+ NODES',
                  '  ID    Role          Commits        TPS       Latency(ms)     '
                  + ('Uptime(s)' if cumulative else 'Window(s)')])
    for row in rows:
        key = str(row['authority'])
        host = hosts.get(key, {})
        role = 'Byzantine' if key in byzantine or host.get('byzantine', False) else 'Honest'
        lines.append(f"  {key:<5} {role:<12} {row['committed_transactions']:>9.0f} "
                     f"{number(row['tps']):>12} {number(row['mean_latency_ms']):>15} "
                     f"{number(row['duration_s']):>13}")
        lines.append(f"        Committed blocks: {count(row.get('committed_blocks'))}  "
                     f"Extra fork outputs: {count(row.get('extra_fork_blocks'))}  "
                     f"Fork output ratio: {percent(row.get('fork_output_ratio'))}")
        if host:
            lines.append(f"        IP={host.get('ip', 'N/A')}  region={host.get('region', 'N/A')} "
                         f"instance={host.get('instance_id', 'N/A')}")
    for host in record.get('hosts', []):
        if host.get('started') is False:
            lines.append(f"  {host['authority']}: not started (configured crash)")
    lines.extend(['', '+ DIAGNOSTICS'])
    for key in ('error', 'report_error', 'cleanup_errors', 'log_errors'):
        if record.get(key):
            lines.append(f"  {key}: {record[key]}")
    if cumulative:
        lines.extend([
            '  Last cumulative shared sum/count includes startup commits; no before-snapshot subtraction.',
            '  Latency is the unweighted mean of all recorded active replica means.',
            '  Zero-count means 0 in upstream arithmetic; it is not a measured zero E2E.',
            '  Upstream terminal TPS = max(cumulative count) // max(benchmark_duration seconds).',
            '  Replica mean TPS is a separate diagnostic, not the upstream terminal TPS.',
        ])
    else:
        lines.extend([
            '  Zero commits contribute 0 TPS, but do not contribute to mean latency.',
            '  Latency is the unweighted mean of positive-commit replica means.',
            '  Replica TPS values are averaged, never summed across replicas.',
        ])
    lines.extend([
        f"  Launch spread: {number(record.get('launch_spread_seconds'), ' s')}",
        '  Committed transaction entries are not deduplicated across fork payloads.',
        '  A2-A5 TPS is raw commit-entry throughput, not unique-transaction goodput.',
        '  Fork output ratio = additional committed digests for an already-output (author, round) / all output blocks.',
        '  Genesis is excluded. Slot history crosses measurement windows; zero output or absent metrics gives N/A.',
        '  Fork ratio is averaged across replicas with measured block output, never by summing replica outputs.',
        '  E2E runs from the embedded transaction timestamp to the commit callback, including subsequent input queueing.',
        '  Scheduling before timestamp generation and the external client response path are excluded; network byte/s is not measured.',
        '  complete describes the test run; inspect zero-commit replicas for lack of progress.',
        '=' * 80,
    ])
    return '\n'.join(lines) + '\n'


def report_run(directory):
    directory = Path(directory)
    record = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
    summary = None
    try:
        samples = directory / 'samples.jsonl'
        if samples.is_file():
            windows = {}
            for line in samples.read_text(encoding='utf-8').splitlines():
                sample = json.loads(line)
                if sample['phase'] in ('before', 'after'):
                    windows[sample['phase']] = sample['nodes']
            if set(windows) != {'before', 'after'}:
                raise ValueError('No complete before/after window; partial samples are not a full-run result')
            before, after = windows['before'], windows['after']
            summary = summarize_run({i: s['metrics'] for i, s in before.items()},
                                    {i: s['metrics'] for i, s in after.items()},
                                    {i: after[i]['monotonic'] - s['monotonic'] for i, s in before.items()},
                                    record.get('measurement_basis', WINDOW_BASIS))
        elif (directory / 'summary.json').is_file():
            summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    except (ValueError, KeyError, TypeError) as error:
        record['report_error'] = str(error)
    text = format_summary(record, summary, directory)
    print(text, flush=True)
    (directory / 'summary.txt').write_text(text, encoding='utf-8')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, help='Saved run directory')
    parser.add_argument('--latest', action='store_true', help='Report the latest AWS run')
    args = parser.parse_args()
    if args.directory and args.latest:
        parser.error('Use a directory or --latest, not both')
    directory = args.directory
    if directory is None:
        root = Path(__file__).resolve().parents[1] / 'results'
        runs = sorted(p for p in root.glob('run-*') if (p / 'run.json').is_file())
        if not runs:
            parser.error(f'No saved runs under {root}')
        directory = runs[-1]
    report_run(directory)


if __name__ == '__main__':
    main()
