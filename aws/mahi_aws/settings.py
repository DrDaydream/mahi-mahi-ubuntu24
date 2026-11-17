"""Validate experiment inputs before any AWS or SSH side effects."""
import copy
import ipaddress
import json
import re
from pathlib import Path, PurePosixPath

COMMIT = '7c928c06bd436c96b94065119e0e8c2d4ac1bcb6'
REPOSITORY = 'https://github.com/PasinduTennage/mahi-mahi-consensus.git'
RUST_TOOLCHAIN = '1.78.0'
RUSTFLAGS = '-C target-cpu=native'
DEFAULTS = {
    'testbed_id': 'mahi-ubuntu24',
    'key': {'name': 'orca-amazon', 'path': '/home/ubuntu/.ssh/orca-amazon.pem'},
    'repo': {'url': REPOSITORY, 'commit': COMMIT},
    'instances': {'type': 'm5.8xlarge', 'regions': [
        'us-east-1', 'eu-north-1', 'ap-southeast-2', 'us-west-1', 'ap-northeast-1'
    ], 'volume_size': 200},
    'remote': {'source_dir': '/home/ubuntu/experiments/mahi-mahi-source',
               'run_dir': '/home/ubuntu/experiments/mahi-mahi-aws-runs',
               'build_jobs': 2, 'parallelism': 5},
    'ssh_cidrs': [],
    'benchmark': {'nodes': 10, 'faults': 0, 'rate': 5000, 'tx_size': 512,
                  'duration': 120, 'warmup': 20, 'runs': 1, 'leaders': 4, 'wave_length': 5,
                  'leader_timeout_ms': 250, 'scrape_interval': 5},
}


def integer(value, name, lower=1, upper=None):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be an integer')
    if not isinstance(value, int):
        if not isinstance(value, str) or not re.fullmatch(r'-?\d+', value):
            raise ValueError(f'{name} must be an integer')
        value = int(value)
    if value < lower or (upper is not None and value > upper):
        raise ValueError(f'{name} is outside the supported range [{lower}, {upper}]')
    return value


def validate_benchmark(parameters):
    result = copy.deepcopy(parameters)
    result['nodes'] = integer(result['nodes'], 'nodes', 4, 128)
    n = result['nodes']
    for key in ('rate', 'tx_size', 'duration', 'runs', 'leaders', 'leader_timeout_ms', 'scrape_interval'):
        result[key] = integer(result[key], key)
    result['warmup'] = integer(result['warmup'], 'warmup', 0)
    result['faults'] = integer(result['faults'], 'faults', 0, (n - 1) // 3)
    if result['rate'] < n or result['tx_size'] <= 16 or result['leaders'] > n:
        raise ValueError('Require rate >= nodes, tx_size > 16 and leaders <= nodes')
    result['wave_length'] = integer(result['wave_length'], 'wave_length', 4, 100)
    if result['rate'] % (10 * n):
        raise ValueError(f'rate must be a multiple of {10 * n} for {n} nodes')
    return result


def load_allocation(rate, nodes, faults):
    if rate % (10 * nodes):
        raise ValueError(f'rate must be a multiple of {10 * nodes}')
    per_node = rate // nodes
    return {'per_node_tps': per_node, 'effective_total_tps': per_node * (nodes - faults),
            'requested_total_tps': rate}


def _remote_path(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'/home/ubuntu/experiments/[A-Za-z0-9_./-]+', value):
        raise ValueError(f'{field} must be a dedicated path below /home/ubuntu/experiments/')
    if '..' in value.split('/') or str(PurePosixPath(value)) != value:
        raise ValueError(f'Unsafe or noncanonical {field}: {value}')
    return value


def load_settings(path='settings.json'):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    settings = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict) or set(data) - set(settings):
        raise ValueError('settings.json has unknown top-level fields')
    for key, value in data.items():
        if isinstance(settings[key], dict):
            if not isinstance(value, dict) or set(value) - set(settings[key]):
                raise ValueError(f'Invalid or unknown settings fields in {key}')
            settings[key].update(value)
        else:
            settings[key] = value
    if settings['repo'] != DEFAULTS['repo']:
        raise ValueError(f'This adapter requires repository {REPOSITORY} and commit {COMMIT}')
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,47}', settings['testbed_id']):
        raise ValueError('testbed_id must contain 3..48 lowercase letters, digits or hyphens')
    regions = settings['instances']['regions']
    if not isinstance(regions, list) or not regions or len(regions) != len(set(regions)):
        raise ValueError('regions must be a nonempty unique list')
    if any(not isinstance(r, str) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', r) for r in regions):
        raise ValueError('Invalid AWS region')
    settings['instances']['volume_size'] = integer(settings['instances']['volume_size'], 'volume_size', 30, 16384)
    if not isinstance(settings['key']['name'], str) or not settings['key']['name']:
        raise ValueError('key.name must be nonempty')
    settings['key']['path'] = str(Path(settings['key']['path']).expanduser())
    for field in ('source_dir', 'run_dir'):
        _remote_path(settings['remote'][field], field)
    source, runs = map(PurePosixPath, (settings['remote']['source_dir'], settings['remote']['run_dir']))
    if source == runs or source in runs.parents or runs in source.parents:
        raise ValueError('source_dir and run_dir must be separate directories')
    for field in ('build_jobs', 'parallelism'):
        settings['remote'][field] = integer(settings['remote'][field], field, 1, 32)
    if not isinstance(settings['ssh_cidrs'], list):
        raise ValueError('ssh_cidrs must be a list; [] detects the controller IPv4')
    settings['ssh_cidrs'] = [str(ipaddress.IPv4Network(c, strict=True)) for c in settings['ssh_cidrs']]
    settings['benchmark'] = validate_benchmark(settings['benchmark'])
    return settings
