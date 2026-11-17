import copy
import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from mahi_aws import remote
from mahi_aws.cloud import Cloud
from mahi_aws.settings import DEFAULTS, load_allocation, validate_benchmark
from mahi_aws.settings import ADVERSARY_DEFAULTS


def genesis_fixture(args, **kwargs):
    """The genesis file/CLI contract from the pinned upstream main.rs/config.rs."""
    assert args[1] == 'benchmark-genesis'
    assert '--number-of-leaders' not in args
    assert '--parameters-path' not in args
    output = Path(args[args.index('--working-directory') + 1])
    ips = args[args.index('--ips') + 1:args.index('--working-directory')]
    output.mkdir(parents=True, exist_ok=True)
    (output / 'committee.yaml').write_text('fixture: true\n')
    identifiers = [dict(public_key=f'key-{i}', network_address=f'{ip}:{1500+i}',
                        metrics_address=f'{ip}:{1500+len(ips)+i}') for i, ip in enumerate(ips)]
    (output / 'public-config.yaml').write_text(yaml.safe_dump({
        'identifiers': identifiers,
        'parameters': {'wave_length': 5, 'max_block_size': 4194304, 'number_of_leaders': 4,
                       'leader_timeout': {'secs': 0, 'nanos': 250000000},
                       'enable_pipelining': True, 'consensus_only': True,
                       'enable_synchronizer': True, 'rounds_in_epoch': 4294967295,
                       'shutdown_grace_period': {'secs': 2, 'nanos': 0}},
    }))
    config_path = output / 'public-config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['parameters']['adversary'] = ADVERSARY_DEFAULTS
    config_path.write_text(yaml.safe_dump(config))
    for i in range(len(ips)):
        (output / f'private-config-{i}.yaml').write_text(yaml.safe_dump({
            'authority': i, 'keypair': f'private-{i}', 'storage_path': str(output / f'storage-{i}'),
        }))


def hosts(n=10):
    return [SimpleNamespace(ip=f'203.0.113.{i+1}', instance_id=f'i-{i}', region='us-east-1')
            for i in range(n)]


def test_mahi_defaults_and_load_are_explicit():
    p = validate_benchmark(DEFAULTS['benchmark'])
    assert p['leaders'] == 4
    assert p['wave_length'] == 5
    assert p['leader_timeout_ms'] == 250
    assert load_allocation(50000, 10, 0) == {
        'per_node_tps': 5000, 'effective_total_tps': 50000, 'requested_total_tps': 50000}
    assert load_allocation(50000, 10, 3)['effective_total_tps'] == 35000


@pytest.mark.parametrize('bad_rate', [50001, 50009, 50010, 99])
def test_rejects_rates_that_would_round_silently(bad_rate):
    with pytest.raises(ValueError, match='multiple'):
        validate_benchmark(dict(DEFAULTS['benchmark'], rate=bad_rate))


@pytest.mark.parametrize('length', [0, 3, -1])
def test_invalid_wave_rejected(length):
    with pytest.raises(ValueError, match='wave_length'):
        validate_benchmark(dict(DEFAULTS['benchmark'], wave_length=length))


def test_mahi_timeout_is_configurable():
    assert validate_benchmark(dict(DEFAULTS['benchmark'], leader_timeout_ms=1501))['leader_timeout_ms'] == 1501


def test_configuration_uses_upstream_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(remote.subprocess, 'run', genesis_fixture)
    p = dict(DEFAULTS['benchmark'], leader_timeout_ms=1501, wave_length=4)
    directory = tmp_path / 'genesis'
    remote_dir = '/home/ubuntu/experiments/mahi-runs/run-123'
    remote.generate_config('/binary', [h.ip for h in hosts()], directory, remote_dir, p)
    public = yaml.safe_load((directory / 'public-config.yaml').read_text())
    assert public['parameters']['leader_timeout'] == {'secs': 1, 'nanos': 501000000}
    assert public['parameters']['wave_length'] == 4
    assert public['parameters']['number_of_leaders'] == 4
    assert public['parameters']['consensus_only'] is True
    assert public['parameters']['enable_synchronizer'] is True
    assert public['parameters']['enable_pipelining'] is True
    assert public['parameters']['max_block_size'] == 4194304
    assert [row['network_address'] for row in public['identifiers']] == [f'{h.ip}:{1500+i}' for i, h in enumerate(hosts())]
    client = yaml.safe_load((directory / 'client-parameters.yaml').read_text())
    assert client == {'load': 500, 'transaction_size': 512, 'initial_delay': {'secs': 0, 'nanos': 0}}
    for i in range(10):
        private = yaml.safe_load((directory / f'private-config-{i}.yaml').read_text())
        assert private['storage_path'] == f'{remote_dir}/storage-{i}'
        assert private['authority'] == i
        assert private['keypair'] == f'private-{i}'


@pytest.mark.parametrize('count', [4, 10, 128])
def test_ports_fit_source_port_encoding_and_security_group(monkeypatch, tmp_path, count):
    monkeypatch.setattr(remote.subprocess, 'run', genesis_fixture)
    p = dict(DEFAULTS['benchmark'], nodes=count, rate=count*100)
    remote.generate_config('/binary', [h.ip for h in hosts(count)], tmp_path, '/runs/r', p)
    public = yaml.safe_load((tmp_path / 'public-config.yaml').read_text())
    for i, row in enumerate(public['identifiers']):
        network_port = int(row['network_address'].rsplit(':', 1)[1])
        assert 1500 <= network_port <= 1627
        assert network_port * 10 <= 65535
        assert int(row['metrics_address'].rsplit(':', 1)[1]) == 1500 + count + i


def test_launch_uploads_client_and_public_files_and_isolates_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(remote.subprocess, 'run', genesis_fixture)
    runner = remote.Runner(copy.deepcopy(DEFAULTS), cloud=SimpleNamespace())
    uploads = {}
    commands = []

    class SSH:
        def __init__(self, host): self.ip = host.ip
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def run(self, command, **kwargs):
            commands.append(command)
            return SimpleNamespace(stdout='')
        def put(self, source, remote):
            uploads[(self.ip, remote)] = source.getvalue().decode() if hasattr(source, 'getvalue') else Path(source).read_text()

    monkeypatch.setattr(runner, '_ssh', SSH)
    monkeypatch.setattr(runner, '_parallel', lambda fn, items: [fn(i) for i in items])
    p = dict(DEFAULTS['benchmark'], nodes=4, rate=1000, faults=1)
    remote_dir = '/home/ubuntu/experiments/mahi-runs/run-test'
    runner._configure_and_start(hosts(4), p, tmp_path, remote_dir)
    for i, host in enumerate(hosts(3)):
        assert (host.ip, f'{remote_dir}/public-config.yaml') in uploads
        assert (host.ip, f'{remote_dir}/client-parameters.yaml') in uploads
        assert (host.ip, f'{remote_dir}/private-config-{i}.yaml') in uploads
        script = uploads[(host.ip, f'{remote_dir}/start.sh')]
        assert f'cd -- {remote_dir}' in script
        assert 'export CONSENSUS_ONLY=1' in script
        assert 'export TPS=' not in script
        assert '--parameters-path' not in script
        assert '--public-config-path' in script
        assert '--client-parameters-path' in script
        args = shlex.split(next(line.removeprefix('exec ') for line in script.splitlines() if line.startswith('exec ')))
        assert args[1] == 'run'
        assert args[args.index('--authority') + 1] == str(i)
    assert len(uploads) == 3 * 5
    assert all('cargo ' not in c and 'git ' not in c for c in commands)
    assert all('mysticeti-' not in c for c in commands)


def test_ready_waits_until_every_replica_has_committed(monkeypatch):
    runner = remote.Runner(copy.deepcopy(DEFAULTS), cloud=SimpleNamespace())
    def samples(values):
        return {str(i): {'metrics': {'count': n}} for i, n in enumerate(values)}
    values = iter([samples([3, 0]), samples([5, 7])])
    monkeypatch.setattr(runner, '_collect', lambda *args: next(values))
    monkeypatch.setattr(remote.time, 'sleep', lambda _: None)
    assert runner._wait_ready(list(enumerate(hosts(2))), 4)['1']['metrics']['count'] == 7


def test_ready_failure_names_stalled_authority(monkeypatch):
    runner = remote.Runner(copy.deepcopy(DEFAULTS), cloud=SimpleNamespace())
    monkeypatch.setattr(runner, '_collect', lambda *args: {'0': {'metrics': {'count': 10}}, '1': {'metrics': {'count': 0}}})
    ticks = iter([0, 121])
    monkeypatch.setattr(remote.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='1'):
        runner._wait_ready(list(enumerate(hosts(2))), 4)


def test_attack_readiness_can_measure_replicas_that_have_never_committed(monkeypatch):
    runner = remote.Runner(copy.deepcopy(DEFAULTS), cloud=SimpleNamespace())
    samples = {'0': {'metrics': {'count': 0, 'sum': 0}}, '1': {'metrics': {'count': 0, 'sum': 0}}}
    monkeypatch.setattr(runner, '_collect', lambda *args: samples)
    assert runner._wait_ready(list(enumerate(hosts(2))), 4, require_commits=False) == samples


def test_resource_ownership_excludes_mysticeti_and_bullshark():
    cloud = Cloud(copy.deepcopy(DEFAULTS), clients={})
    assert cloud.tags == {'ManagedBy': 'mahi-fabric', 'Testbed': 'mahi-ubuntu24'}
    for other in ['mysticeti-fabric', 'bullshark-fabric']:
        assert not cloud._owned({'Tags': [{'Key': 'ManagedBy', 'Value': other},
                                       {'Key': 'Testbed', 'Value': 'mahi-ubuntu24'}]})


def test_run_failure_retains_logs_and_status(monkeypatch, tmp_path, capsys):
    runner = remote.Runner(copy.deepcopy(DEFAULTS), cloud=SimpleNamespace(), results_dir=tmp_path)
    stopped = []
    monkeypatch.setattr(runner, '_stop', lambda _: stopped.append(True) or [])
    monkeypatch.setattr(runner, '_configure_and_start', lambda *args: None)
    monkeypatch.setattr(runner, '_wait_ready', lambda *args: (_ for _ in ()).throw(RuntimeError('stalled authority 2')))
    def download_after_diagnostics(*args):
        output = capsys.readouterr().out
        assert 'SUMMARY' in output and 'stalled authority 2' in output
        return []
    monkeypatch.setattr(runner, '_download_logs', download_after_diagnostics)
    with pytest.raises(RuntimeError, match='stalled authority 2'):
        runner._run_once(hosts(), DEFAULTS['benchmark'])
    record = json.loads(next(tmp_path.glob('*/run.json')).read_text())
    assert record['status'] == 'failed'
    assert 'stalled authority 2' in record['error']
    assert len(stopped) == 2


def test_report_write_failure_still_stops_nodes_and_collects_logs(monkeypatch, tmp_path):
    runner = remote.Runner(copy.deepcopy(DEFAULTS), cloud=SimpleNamespace(), results_dir=tmp_path)
    stopped, downloaded = [], []
    monkeypatch.setattr(runner, '_stop', lambda _: stopped.append(True) or [])
    monkeypatch.setattr(runner, '_configure_and_start', lambda *args: None)
    monkeypatch.setattr(runner, '_wait_ready', lambda *args: (_ for _ in ()).throw(RuntimeError('node died')))
    monkeypatch.setattr(runner, '_download_logs', lambda *args: downloaded.append(True) or [])
    write_text = Path.write_text
    def write(path, *args, **kwargs):
        if path.name == 'summary.txt':
            raise OSError('disk full')
        return write_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write)
    with pytest.raises(OSError, match='disk full'):
        runner._run_once(hosts(), DEFAULTS['benchmark'])
    assert len(stopped) == 2
    assert downloaded == [True]


@pytest.mark.parametrize('error', [BrokenPipeError('pipe closed'), KeyboardInterrupt()])
def test_report_output_interruption_still_stops_nodes_and_collects_logs(monkeypatch, tmp_path, error):
    runner = remote.Runner(copy.deepcopy(DEFAULTS), cloud=SimpleNamespace(), results_dir=tmp_path)
    stopped, downloaded = [], []
    monkeypatch.setattr(runner, '_stop', lambda _: stopped.append(True) or [])
    monkeypatch.setattr(runner, '_configure_and_start', lambda *args: None)
    monkeypatch.setattr(runner, '_wait_ready', lambda *args: (_ for _ in ()).throw(RuntimeError('node died')))
    monkeypatch.setattr(runner, '_download_logs', lambda *args: downloaded.append(True) or [])
    def interrupted_print(text, **kwargs):
        if 'SUMMARY' in text:
            raise error
    monkeypatch.setattr('builtins.print', interrupted_print)
    with pytest.raises(type(error)):
        runner._run_once(hosts(), DEFAULTS['benchmark'])
    assert len(stopped) == 2
    assert downloaded == [True]
    record = json.loads(next(tmp_path.glob('*/run.json')).read_text())
    assert record['status'] == 'failed'
    assert record['report_error'] == (str(error) or type(error).__name__)


@pytest.mark.parametrize('stalled', [[], [0], [0, 1, 2, 3]])
def test_remote_prebuilt_integration_and_replica_summary(monkeypatch, tmp_path, capsys, stalled):
    import hashlib
    import threading
    from mahi_aws.settings import REPOSITORY, RUSTFLAGS, RUST_TOOLCHAIN
    provenance={'commit':'a'*40,'repository':REPOSITORY,'source_ref':'HEAD'}

    settings = copy.deepcopy(DEFAULTS)
    source = tmp_path / 'source'
    settings['remote']['source_dir'] = str(source)
    binary = source / 'target/release/mysticeti'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'prebuilt-fixture')
    binary.chmod(0o755)
    build = {**provenance, 'adversary_schema': 1, 'rustflags': RUSTFLAGS, 'rust_toolchain': RUST_TOOLCHAIN,
             'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    (source / '.mahi-build.json').write_text(json.dumps(build))
    commands, counters = [], {}
    lock = threading.Lock()

    class SSH:
        def __init__(self, host): self.host = host
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def open(self): pass
        def put(self, *args, **kwargs): pass
        def get(self, remote, local): Path(local).write_text('node log')
        def run(self, command, **kwargs):
            with lock:
                commands.append(command)
                if '/metrics' in command:
                    i = counters.get(self.host.ip, 0) + 1
                    counters[self.host.ip] = i
                    authority = hosts(4).index(self.host)
                    count = 0 if authority in stalled else i
                    return SimpleNamespace(stdout=(
                        f'latency_s_count{{workload="shared"}} {count*100}\n'
                        f'latency_s_sum{{workload="shared"}} {count*20}\n'
                        f'committed_blocks_total{{kind="first"}} {count*10}\n'
                        f'committed_blocks_total{{kind="extra_fork"}} {count*2}\n'))
            return SimpleNamespace(stdout=json.dumps(build))

    def genesis(args, **kwargs):
        assert args[0] == str(binary)
        return genesis_fixture(args, **kwargs)

    cloud = SimpleNamespace(hosts=lambda: hosts(4), sync_network=lambda: None)
    runner = remote.Runner(settings, cloud=cloud, results_dir=tmp_path/'results')
    monkeypatch.setattr(runner, '_ssh', SSH)
    monkeypatch.setattr(remote.subprocess, 'run', genesis)
    monkeypatch.setattr(runner, 'install', lambda: pytest.fail('remote tried to install'))
    monkeypatch.setattr(runner, '_resolve_source', lambda: provenance)
    download_logs = runner._download_logs
    def download_with_visible_report(*args):
        assert 'SUMMARY' in capsys.readouterr().out
        return download_logs(*args)
    monkeypatch.setattr(runner, '_download_logs', download_with_visible_report)
    attack = {'mode': 'A3', 'duration_ms': 100, 'start_after_ms': 0, 'delay_ms': 1, 'late_delay_ms': 1}
    runner.remote(dict(settings['benchmark'], nodes=4, rate=1000, duration=1, warmup=0,
                       scrape_interval=1, adversary=attack))
    summary = json.loads(next((tmp_path/'results').glob('*/summary.json')).read_text())
    assert summary['mean_latency_ms'] == (None if len(stalled) == 4 else pytest.approx(200))
    assert len(summary['nodes']) == 4
    assert summary['replica_tps_mean'] == pytest.approx(sum(n['tps'] for n in summary['nodes'])/4)
    assert summary['load']['effective_total_tps'] == 1000
    assert summary['all_nodes_progress'] is (not stalled)
    assert summary['fork_output_ratio_mean'] == (None if len(stalled) == 4 else pytest.approx(1/6))
    assert summary['fork_ratio_contributors'] == 4 - len(stalled)
    saved_text = next((tmp_path/'results').glob('*/summary.txt')).read_text(encoding='utf-8')
    assert 'MAHI-MAHI AWS' in saved_text and 'Status: complete' in saved_text
    assert len(list((tmp_path/'results').glob('*/logs/node-*.log'))) == 4
    assert all(not any(word in command.split() for word in ('cargo', 'git', 'apt-get', 'rustup')) for command in commands)


def test_unbuilt_remote_does_not_attempt_install(tmp_path, monkeypatch):
    settings = copy.deepcopy(DEFAULTS)
    settings['remote']['source_dir'] = str(tmp_path / 'no-build')
    runner = remote.Runner(settings, cloud=SimpleNamespace())
    monkeypatch.setattr(runner, 'install', lambda: pytest.fail('remote tried to install'))
    with pytest.raises(RuntimeError, match='fab install'):
        runner.remote(DEFAULTS['benchmark'])
