import pytest

from mahi_aws.metrics import parse_snapshot, summarize


BASE = 'latency_s_count{workload="shared"} 10\nlatency_s_sum{workload="shared"} 1\n'


def test_reads_output_fork_counters_independently_of_generated_forks():
    result = parse_snapshot(BASE + 'committed_blocks_total{kind="first"} 15\n'
                            'committed_blocks_total{kind="extra_fork"} 5\n'
                            'adversary_generated_forks_total 999\n')
    assert result['committed_blocks'] == 20
    assert result['extra_fork_blocks'] == 5


@pytest.mark.parametrize('rows', [
    'committed_blocks_total{kind="first"} 20\n',
    'committed_blocks_total{kind="extra_fork"} 5\n',
    'committed_blocks_total{kind="first"} 20\ncommitted_blocks_total{kind="first"} 21\ncommitted_blocks_total{kind="extra_fork"} 5\n',
    'committed_blocks_total{kind="first"} 20\ncommitted_blocks_total{kind="extra_fork"} NaN\n',
])
def test_rejects_incomplete_ambiguous_or_invalid_output_counters(rows):
    with pytest.raises(ValueError):
        parse_snapshot(BASE + rows)


def test_window_fork_ratio_uses_output_deltas_and_averages_replica_ratios():
    before = {'a': dict(count=0, sum=0, committed_blocks=100, extra_fork_blocks=20),
              'b': dict(count=0, sum=0, committed_blocks=10, extra_fork_blocks=2)}
    after = {'a': dict(count=10, sum=1, committed_blocks=120, extra_fork_blocks=25),
             'b': dict(count=10, sum=1, committed_blocks=20, extra_fork_blocks=2)}
    result = summarize(before, after, {'a': 2, 'b': 2})
    assert result['nodes'][0]['committed_blocks'] == 20
    assert result['nodes'][0]['extra_fork_blocks'] == 5
    assert result['nodes'][0]['fork_output_ratio'] == .25
    assert result['nodes'][1]['fork_output_ratio'] == 0
    assert result['fork_output_ratio_mean'] == .125
    assert result['fork_ratio_contributors'] == 2


def test_no_output_and_legacy_metrics_have_unknown_fork_ratio():
    before = {'a': dict(count=0, sum=0, committed_blocks=0, extra_fork_blocks=0),
              'legacy': dict(count=0, sum=0)}
    result = summarize(before, before, {'a': 2, 'legacy': 2})
    assert all(row['fork_output_ratio'] is None for row in result['nodes'])
    assert result['fork_output_ratio_mean'] is None
    assert result['fork_ratio_contributors'] == 0
    assert result['nodes'][0]['committed_blocks'] == 0
    assert result['nodes'][1]['committed_blocks'] is None


@pytest.mark.parametrize('last', [
    dict(committed_blocks=9, extra_fork_blocks=2),
    dict(committed_blocks=12, extra_fork_blocks=1),
    dict(committed_blocks=12, extra_fork_blocks=5),
    dict(committed_blocks=12),
])
def test_rejects_reset_or_inconsistent_fork_window(last):
    with pytest.raises(ValueError):
        summarize({'a': dict(count=0, sum=0, committed_blocks=10, extra_fork_blocks=2)},
                  {'a': dict(count=10, sum=1, **last)}, {'a': 2})
