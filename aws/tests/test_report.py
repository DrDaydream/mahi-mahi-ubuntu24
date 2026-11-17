import json

from mahi_aws import metrics


def test_report_includes_attack_and_honest_subset_with_stalled_replica(tmp_path):
    from mahi_aws.report import format_summary
    record = {'status': 'complete', 'parameters': {'nodes': 3, 'rate': 300},
              'adversary': {'mode': 'A3', 'byzantine_authorities': [0]},
              'hosts': [{'authority': 0}, {'authority': 1}, {'authority': 2}]}
    before = {str(i): {'count': 0, 'sum': 0} for i in range(3)}
    after = {'0': {'count': 40, 'sum': 20}, '1': {'count': 10, 'sum': 1},
             '2': {'count': 0, 'sum': 0}}
    summary = metrics.summarize(before, after, {'0': 2, '1': 2, '2': 2})
    text = format_summary(record, summary, tmp_path)
    assert 'MAHI-MAHI AWS' in text and '+ CONFIG' in text
    assert 'A3' in text and 'Byzantine' in text and 'Honest' in text
    honest = text.split('+ HONEST REPLICAS')[1].split('+ NODES')[0]
    assert '2.500 tx/s' in honest
    assert '100.000 ms' in honest
    assert 'Latency contributors: 1/2' in honest
    assert 'N/A' in text
    assert 'not deduplicated' in text


def test_offline_report_recovers_zero_progress_without_changing_run_status(tmp_path, capsys):
    from mahi_aws.report import report_run
    record = {'status': 'failed', 'error': 'old zero progress error', 'parameters': {}}
    (tmp_path / 'run.json').write_text(json.dumps(record))
    snapshots = [
        {'phase': 'before', 'nodes': {'0': {'metrics': {'count': 0, 'sum': 0}, 'monotonic': 1}}},
        {'phase': 'after', 'nodes': {'0': {'metrics': {'count': 0, 'sum': 0}, 'monotonic': 3}}},
    ]
    (tmp_path / 'samples.jsonl').write_text('\n'.join(map(json.dumps, snapshots)))
    report_run(tmp_path)
    output = capsys.readouterr().out
    assert '0.000 tx/s' in output and 'N/A' in output
    assert 'failed' in output and 'old zero progress error' in output
    assert json.loads((tmp_path / 'run.json').read_text()) == record
    assert (tmp_path / 'summary.txt').read_text(encoding='utf-8').strip() in output


def test_incomplete_run_prints_diagnostics_instead_of_partial_throughput(tmp_path, capsys):
    from mahi_aws.report import report_run
    (tmp_path / 'run.json').write_text(json.dumps({'status': 'failed', 'error': 'node died'}))
    (tmp_path / 'samples.jsonl').write_text(json.dumps({'phase': 'before', 'nodes': {}}))
    report_run(tmp_path)
    output = capsys.readouterr().out
    assert 'node died' in output and 'N/A' in output
    assert 'complete before/after' in output


def test_report_labels_fork_numerator_denominator_and_percent(tmp_path):
    from mahi_aws.report import format_summary
    before = {'0': dict(count=0, sum=0, committed_blocks=10, extra_fork_blocks=1)}
    after = {'0': dict(count=100, sum=10, committed_blocks=30, extra_fork_blocks=6)}
    summary = metrics.summarize(before, after, {'0': 2})
    text = format_summary({'adversary': {'mode': 'A2'}}, summary, tmp_path)
    assert 'Committed blocks: 20' in text
    assert 'Extra fork outputs: 5' in text
    assert '25.000%' in text
