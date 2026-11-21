"""Install a resolved source commit; run Mahi-Mahi experiments without rebuilding."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import time
import uuid

from fabric import Connection
from paramiko import PKey
from paramiko.ssh_exception import AuthenticationException
import yaml

from .build import verification_script, verify_build
from .cloud import Cloud
from .metrics import parse_snapshot, summarize_run, UPSTREAM_BASIS, WINDOW_BASIS
from .report import format_summary
from .settings import ADVERSARY_DEFAULTS, load_allocation, validate_benchmark
from .source import resolve_source

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def generate_config(binary, ips, output, remote_directory, parameters):
    parameters = validate_benchmark(parameters)
    n = len(ips)
    if n != parameters['nodes'] or (1500 + n - 1) * 10 > 65535:
        raise ValueError('Invalid committee size or peer source ports')
    output = Path(output)
    subprocess.run([str(binary), 'benchmark-genesis', '--ips', *ips,
                    '--working-directory', str(output)], check=True)
    path = output / 'public-config.yaml'
    public = yaml.safe_load(path.read_text())
    if len(public['identifiers']) != n:
        raise ValueError('Generated committee does not match the host list')
    for i, row in enumerate(public['identifiers']):
        if row['network_address'] != f'{ips[i]}:{1500+i}' or row['metrics_address'] != f'{ips[i]}:{1500+n+i}':
            raise ValueError('Unexpected genesis ports; refusing to start')
    if parameters['adversary']['mode'] != 'A0' and not set(ADVERSARY_DEFAULTS).issubset(public['parameters'].get('adversary', {})):
        raise RuntimeError('Binary genesis lacks adversary support. Run fab install first.')
    public['parameters']['adversary'] = parameters['adversary']
    timeout = parameters['leader_timeout_ms']
    public['parameters'].update(
        wave_length=parameters['wave_length'], number_of_leaders=parameters['leaders'],
        leader_timeout={'secs': timeout // 1000, 'nanos': timeout % 1000 * 1000000},
        consensus_only=True, enable_synchronizer=parameters['enable_synchronizer'], enable_pipelining=True)
    path.write_text(yaml.safe_dump(public, sort_keys=False), encoding='utf-8')
    allocation = load_allocation(parameters['rate'], n, parameters['faults'])
    client = {'load': allocation['per_node_tps'], 'transaction_size': parameters['tx_size'],
              'initial_delay': {'secs': 0, 'nanos': 0}}
    (output / 'client-parameters.yaml').write_text(yaml.safe_dump(client), encoding='utf-8')
    for i in range(n):
        path = output / f'private-config-{i}.yaml'
        private = yaml.safe_load(path.read_text())
        private['storage_path'] = f'{remote_directory}/storage-{i}'
        path.write_text(yaml.safe_dump(private, sort_keys=False), encoding='utf-8')


class Runner:
    def __init__(self, settings, cloud=None, results_dir=None):
        self.settings = settings
        self._cloud = cloud
        self.provenance = {}
        self.source = Path(settings['remote']['source_dir'])
        self.binary = self.source / 'target/release/mysticeti'
        self.results_dir = Path(results_dir or PACKAGE_DIR / 'results')
        self.socket = 'mahi-' + hashlib.sha256(settings['testbed_id'].encode()).hexdigest()[:12]
        self._key = None

    @property
    def cloud(self):
        if self._cloud is None:
            self._cloud = Cloud(self.settings)
        return self._cloud

    def _resolve_source(self):
        checkout = PACKAGE_DIR.parent.resolve()
        source = self.source.resolve()
        if source == checkout or source in checkout.parents or checkout in source.parents:
            raise ValueError('remote.source_dir must be separate from the controller adapter checkout')
        return resolve_source(checkout, self.settings['repo'])

    def _ssh(self, host):
        if self._key is None:
            self._key = PKey.from_path(Path(self.settings['key']['path']).expanduser())
        return Connection(host.ip, user='ubuntu', connect_timeout=15, connect_kwargs={
            'pkey': self._key, 'allow_agent': False, 'look_for_keys': False,
            'banner_timeout': 30, 'auth_timeout': 30,
        })

    def _parallel(self, function, items):
        items = list(items)
        if not items:
            return []
        output, errors = {}, []
        with ThreadPoolExecutor(max_workers=min(len(items), self.settings['remote']['parallelism'])) as pool:
            futures = {pool.submit(function, item): index for index, item in enumerate(items)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    output[index] = future.result()
                except Exception as error:
                    errors.append(f'{items[index]}: {error}')
        if errors:
            raise RuntimeError('\n'.join(errors))
        return [output[index] for index in range(len(items))]

    def _wait_ssh(self, host):
        deadline = time.monotonic() + 240
        while True:
            try:
                with self._ssh(host) as connection:
                    connection.run('true', hide=True, timeout=15)
                return
            except AuthenticationException:
                raise
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(3)

    def install(self):
        self.provenance = self._resolve_source()
        hosts = self.cloud.hosts()
        if not hosts:
            raise RuntimeError('No Mahi-Mahi instances found. Run fab info; create or start the testbed.')
        scripts = PACKAGE_DIR / 'scripts'
        arguments = [str(self.source), self.provenance['commit'],
                     str(self.settings['remote']['build_jobs']), self.provenance['repository']]
        print('Installing and compiling on the controller (output is visible)...', flush=True)
        subprocess.run(['bash', str(scripts / 'install_node.sh'), *arguments], check=True)
        verify_build(self.source, self.provenance)
        self._parallel(self._wait_ssh, hosts)
        self.cloud.sync_network()

        def install_host(host):
            remote_tmp = '/tmp/mahi-install-' + uuid.uuid4().hex
            print(f'[{host.region} {host.ip}] Installing and compiling...', flush=True)
            with self._ssh(host) as connection:
                connection.run(f'mkdir -m 700 {shlex.quote(remote_tmp)}', hide=True)
                try:
                    for name in ('install_node.sh', 'build_node.sh', 'sync_source.sh', 'record_build.py'):
                        connection.put(str(scripts / name), remote=f'{remote_tmp}/{name}')
                    command = shlex.join(['bash', f'{remote_tmp}/install_node.sh', *arguments])
                    connection.run(command, hide=False, pty=False)
                    result = connection.run(verification_script(str(self.source), self.provenance), hide=True)
                    return {'host': host.ip, 'build': json.loads(result.stdout)}
                finally:
                    connection.run(f'rm -f -- {shlex.quote(remote_tmp + "/install_node.sh")} '
                                   f'{shlex.quote(remote_tmp + "/build_node.sh")} '
                                   f'{shlex.quote(remote_tmp + "/sync_source.sh")} '
                                   f'{shlex.quote(remote_tmp + "/record_build.py")} && '
                                   f'rmdir -- {shlex.quote(remote_tmp)}', hide=True, warn=True)
        records = self._parallel(install_host, hosts)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        save_json(self.results_dir / 'installation.json', {
            **self.provenance, 'controller': verify_build(self.source, self.provenance), 'nodes': records,
        })
        print(f'Built commit {self.provenance["commit"]} on controller and {len(hosts)} nodes; ready for fab remote.', flush=True)

    def remote(self, parameters):
        parameters = validate_benchmark(parameters)
        if not (self.source / '.mahi-build.json').is_file():
            verify_build(self.source)
        self.provenance = self._resolve_source()
        self.controller_build = verify_build(self.source, self.provenance)
        hosts = self.cloud.hosts()
        if len(hosts) < parameters['nodes']:
            raise RuntimeError(f'Need {parameters["nodes"]} running Mahi-Mahi instances; found {len(hosts)}')
        hosts = hosts[:parameters['nodes']]
        self.cloud.sync_network()

        def preflight(host):
            with self._ssh(host) as connection:
                result = connection.run(verification_script(str(self.source), self.provenance), hide=True)
                connection.run('command -v tmux >/dev/null && chronyc waitsync 1 0.01 0.0 1', hide=True, timeout=15)
                return {'host': host.ip, 'build': json.loads(result.stdout)}
        self.installed = self._parallel(preflight, hosts)
        for repetition in range(parameters['runs']):
            print(f'Experiment {repetition + 1}/{parameters["runs"]}', flush=True)
            self._run_once(hosts, parameters)

    def _stop(self, hosts):
        def stop_host(host):
            try:
                with self._ssh(host) as connection:
                    connection.run(f'tmux -L {self.socket} kill-server 2>/dev/null || true', hide=True, timeout=15)
            except Exception as error:
                return f'{host.ip}: {error}'
            return None
        return [error for error in self._parallel(stop_host, hosts) if error]

    def kill(self):
        errors = self._stop(self.cloud.hosts())
        if errors:
            raise RuntimeError('Could not stop every node: ' + '\n'.join(errors))
        print('Stopped Mahi-Mahi experiment processes; EC2 instances remain running.')

    def _configure_and_start(self, hosts, parameters, directory, remote_directory):
        genesis = directory / 'genesis'
        generate_config(self.binary, [host.ip for host in hosts], genesis, remote_directory, parameters)
        active = list(enumerate(hosts[:len(hosts) - parameters['faults']]))

        def launch(item):
            index, host = item
            with self._ssh(host) as connection:
                storage = f'{remote_directory}/storage-{index}'
                connection.run(f'mkdir -p -- {shlex.quote(storage)}', hide=True)
                for name in ('committee.yaml', 'public-config.yaml', 'client-parameters.yaml',
                             f'private-config-{index}.yaml'):
                    connection.put(str(genesis / name), remote=f'{remote_directory}/{name}')
                args = [str(self.binary), 'run', '--authority', str(index),
                        '--committee-path', f'{remote_directory}/committee.yaml',
                        '--public-config-path', f'{remote_directory}/public-config.yaml',
                        '--private-config-path', f'{remote_directory}/private-config-{index}.yaml',
                        '--client-parameters-path', f'{remote_directory}/client-parameters.yaml']
                script = '\n'.join([
                    '#!/usr/bin/env bash', 'set -euo pipefail',
                    f'cd -- {shlex.quote(remote_directory)}',
                    'export CONSENSUS_ONLY=1', 'export RUST_LOG=info',
                    f'exec {shlex.join(args)} > node.log 2>&1', '',
                ])
                connection.put(io.BytesIO(script.encode()), remote=f'{remote_directory}/start.sh')
                command = shlex.join(['bash', f'{remote_directory}/start.sh'])
                connection.run(f'tmux -L {self.socket} new-session -d -s node-{index} {shlex.quote(command)}', hide=True)
        self.launch_started = time.monotonic()
        self._parallel(launch, active)
        self.launch_completed = time.monotonic()

    def _collect(self, active, committee_size):
        def collect(item):
            index, host = item
            with self._ssh(host) as connection:
                connection.open()
                start = time.monotonic()
                result = connection.run(
                    f'tmux -L {self.socket} has-session -t node-{index} && '
                    f'curl -fsS --max-time 10 http://127.0.0.1:{1500 + committee_size + index}/metrics',
                    hide=True, timeout=20)
                end = time.monotonic()
                snapshot = parse_snapshot(result.stdout)
                return str(index), {'metrics': snapshot, 'monotonic': (start + end) / 2,
                                    'request_seconds': end - start, 'text': result.stdout}
        return dict(self._parallel(collect, active))

    def _record(self, directory, name, samples):
        snapshots = directory / 'metrics'
        snapshots.mkdir(exist_ok=True)
        record = {}
        for index, sample in samples.items():
            (snapshots / f'{name}-node-{index}.prom').write_text(sample['text'], encoding='utf-8')
            record[index] = {key: value for key, value in sample.items() if key != 'text'}
        with (directory / 'samples.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'phase': name, 'nodes': record}) + '\n')

    def _wait_ready(self, active, committee_size, require_commits=True):
        deadline = time.monotonic() + 120
        attempts = 0
        while True:
            try:
                samples = self._collect(active, committee_size)
                stalled = [i for i, sample in samples.items() if sample['metrics']['count'] <= 0]
                if require_commits and stalled:
                    raise RuntimeError('No shared commits on authorities: ' + ', '.join(stalled))
                return samples
            except Exception as error:
                if time.monotonic() >= deadline:
                    condition = 'shared commit progress' if require_commits else 'live process and valid metrics'
                    raise RuntimeError(f'No {condition} on every active validator within 120s: {error}; see results logs') from error
                attempts += 1
                if attempts % 5 == 1:
                    print(f'Waiting for ready validators: {error}', flush=True)
                time.sleep(2)

    def _download_logs(self, active, directory, remote_directory):
        (directory / 'logs').mkdir(exist_ok=True)
        def download(item):
            index, host = item
            try:
                with self._ssh(host) as connection:
                    connection.get(f'{remote_directory}/node.log', local=str(directory / 'logs' / f'node-{index}.log'))
            except Exception as error:
                return f'{host.ip}: {error}'
            return None
        return [error for error in self._parallel(download, active) if error]

    def _run_once(self, hosts, parameters):
        parameters = validate_benchmark(parameters)
        run_id = datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        directory = self.results_dir / run_id
        directory.mkdir(parents=True, exist_ok=False)
        remote_directory = self.settings['remote']['run_dir'] + '/' + run_id
        active = list(enumerate(hosts[:len(hosts) - parameters['faults']]))
        allocation = load_allocation(parameters['rate'], len(hosts), parameters['faults'])
        record = {'run_id': run_id, 'commit': self.provenance.get('commit'), 'source': self.provenance, 'status': 'starting', 'parameters': parameters,
                  'measurement_basis': UPSTREAM_BASIS if parameters['adversary']['mode'] == 'A0' else WINDOW_BASIS,
                  'load': allocation, 'builds': getattr(self, 'installed', []),
                  'controller_build': getattr(self, 'controller_build', None),
                  'adversary': parameters['adversary'],
                  'crashed_authorities': list(range(len(active), len(hosts))),
                  'enable_synchronizer': parameters['enable_synchronizer'],
                  'leader_schedule': parameters['leader_schedule'], 'enable_pipelining': True,
                  'hosts': [dict(authority=i, ip=h.ip, region=h.region, instance_id=h.instance_id,
                                 started=i < len(active), byzantine=i in parameters['adversary']['byzantine_authorities'])
                            for i, h in enumerate(hosts)]}
        save_json(directory / 'run.json', record)
        print(f'Results: {directory}\nTarget load: {allocation}; crashed tail members are not started.', flush=True)
        problem = None
        summary = None
        try:
            previous_errors = self._stop(hosts)
            if previous_errors:
                raise RuntimeError('Could not stop a previous experiment: ' + '\n'.join(previous_errors))
            self._configure_and_start(hosts, parameters, directory, remote_directory)
            # Valid zero output is allowed. Readiness requires live processes and
            # actual shared counters; it does not wait for a first commit.
            self._wait_ready(active, len(hosts), require_commits=False)
            print(f'All active nodes ready. Warming up for {parameters["warmup"]}s...', flush=True)
            deadline = time.monotonic() + parameters['warmup']
            record['launch_spread_seconds'] = getattr(self, 'launch_completed', deadline) - getattr(self, 'launch_started', deadline)
            while time.monotonic() < deadline:
                time.sleep(min(parameters['scrape_interval'], max(0, deadline - time.monotonic())))
                self._collect(active, len(hosts))
            before = self._collect(active, len(hosts))
            self._record(directory, 'before', before)
            start = time.monotonic()
            deadline = start + parameters['duration']
            sequence = 0
            while True:
                time.sleep(min(parameters['scrape_interval'], max(0, deadline - time.monotonic())))
                after = self._collect(active, len(hosts))
                sequence += 1
                finished = time.monotonic() >= deadline
                self._record(directory, 'after' if finished else f'sample-{sequence:04}', after)
                print(f'Measuring: {time.monotonic() - start:.1f}/{parameters["duration"]}s', flush=True)
                if finished:
                    break
            elapsed = {index: after[index]['monotonic'] - before[index]['monotonic'] for index in before}
            summary = summarize_run({i: s['metrics'] for i, s in before.items()},
                                    {i: s['metrics'] for i, s in after.items()}, elapsed,
                                    record['measurement_basis'])
            summary.update(load=allocation, commit=self.provenance.get('commit'), run_id=run_id,
                           adversary=parameters['adversary'], crashed_authorities=record['crashed_authorities'],
                           enable_synchronizer=parameters['enable_synchronizer'])
            save_json(directory / 'summary.json', summary)
            with (directory / 'summary.csv').open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(summary['nodes'][0]))
                writer.writeheader()
                writer.writerows(summary['nodes'])
            record['status'] = 'complete'
        except BaseException as error:
            record['status'] = 'failed'
            record['error'] = str(error) or type(error).__name__
            problem = error
        finally:
            try:
                visible = dict(record, status=('measurement complete' if record['status'] == 'complete' else 'failed')
                               + '; cleanup/log download pending')
                text = format_summary(visible, summary, directory)
                print(text, flush=True)
                (directory / 'summary.txt').write_text(text, encoding='utf-8')
            except BaseException as error:
                # Report failures and Ctrl-C during output must not leave validators running.
                record['report_error'] = str(error) or type(error).__name__
                record['status'] = 'failed'
                if problem is None:
                    problem = error
            stop_errors = self._stop(hosts)
            log_errors = self._download_logs(active, directory, remote_directory)
            record['cleanup_errors'] = stop_errors
            record['log_errors'] = log_errors
            if stop_errors or log_errors:
                record['status'] = 'failed'
                if problem is None:
                    problem = RuntimeError('Cleanup/log collection failed; see run.json')
            save_json(directory / 'run.json', record)
            text = format_summary(record, summary, directory)
            (directory / 'summary.txt').write_text(text, encoding='utf-8')
        if problem is not None:
            print(text, flush=True)
            raise problem
        print(f'Complete. Results and summary.txt: {directory}', flush=True)
