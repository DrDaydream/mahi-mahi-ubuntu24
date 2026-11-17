#!/usr/bin/env python3
"""Run a pinned Mahi-Mahi binary locally and measure shared commit counters."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import uuid
import os

import yaml
from metrics import parse_snapshot, summarize

COMMIT = '7c928c06bd436c96b94065119e0e8c2d4ac1bcb6'


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.home() / 'experiments/mahi-mahi-source')
    parser.add_argument('--nodes', type=int, default=4)
    parser.add_argument('--rate', type=int, default=1000, help='Total input TPS across all validators')
    parser.add_argument('--tx-size', type=int, default=512)
    parser.add_argument('--duration', type=float, default=60, help='Measurement seconds after warmup')
    parser.add_argument('--warmup', type=float, default=10)
    parser.add_argument('--leaders', type=int, default=4)
    parser.add_argument('--base-port', type=int, default=2000)
    parser.add_argument('--worker-threads', type=int, default=2)
    args = parser.parse_args(argv)
    if not 4 <= args.nodes <= 128:
        parser.error('--nodes must be between 4 and 128')
    if args.rate <= 0 or args.rate % (10 * args.nodes):
        parser.error('--rate must be a positive multiple of 10 * nodes (the generator emits 100 ms batches)')
    if not 17 <= args.tx_size <= 4 * 1024 * 1024:
        parser.error('--tx-size must be between 17 and 4194304 bytes')
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error('--duration must be finite and positive')
    if not math.isfinite(args.warmup) or args.warmup < 0:
        parser.error('--warmup must be finite and nonnegative')
    if not 1 <= args.leaders <= args.nodes or not 1 <= args.worker_threads <= 128:
        parser.error('--leaders must be in 1..nodes and --worker-threads in 1..128')
    if args.base_port < 1024 or args.base_port + 2 * args.nodes - 1 > 65535:
        parser.error('Requested network/metrics port range is invalid')
    if (args.base_port + args.nodes - 1) * 10 > 65535:
        parser.error('Mahi-Mahi uses network_port * 10 as a source port; lower --base-port to avoid overflow')
    args.repo = args.repo.expanduser().resolve()
    return args


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def verify_build(repo):
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != COMMIT:
        raise RuntimeError(f'Expected Mahi-Mahi commit {COMMIT}, got {revision}')
    subprocess.run(['git', '-C', str(repo), 'diff', '--exit-code', '--quiet', 'HEAD', '--'], check=True)
    binary = repo / 'target/release/mysticeti'
    manifest = repo / 'target/mahi-build.json'
    if not binary.is_file() or not manifest.is_file():
        raise RuntimeError('Build the binary first using local/build.sh and the source directory.')
    record = json.loads(manifest.read_text(encoding='utf-8'))
    if (record.get('commit') != COMMIT
            or not str(record.get('rustc', '')).startswith('rustc 1.78.0 ')
            or record.get('binary_sha256') != hashlib.sha256(binary.read_bytes()).hexdigest()
            or record.get('cargo_lock_sha256') != hashlib.sha256((repo / 'Cargo.lock').read_bytes()).hexdigest()):
        raise RuntimeError('Build record does not match the source/binary. Repeat the compilation step.')
    return binary, record


def check_ports(base, nodes):
    if (base + nodes - 1) * 10 > 65535:
        raise RuntimeError('Mahi-Mahi source port exceeds 65535; lower --base-port.')
    ports = list(range(base, base + 2 * nodes))
    ports.extend((base + index) * 10 for index in range(nodes))
    for port in ports:
        with socket.socket() as sock:
            if sys.platform != 'win32':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', port))
            except OSError as error:
                raise RuntimeError(f'Port {port} is unavailable; use another --base-port.') from error


def run_cluster(args, binary_command, directory):
    """Use the real binary in production; tests substitute a standalone HTTP fixture."""
    directory = directory.resolve()
    check_ports(args.base_port, args.nodes)
    genesis = directory / 'genesis'
    metrics_dir = directory / 'metrics'
    metrics_dir.mkdir()
    node_parameters = {'wave_length': 5, 'leader_timeout': {'secs': 0, 'nanos': 250000000},
                       'number_of_leaders': args.leaders, 'enable_pipelining': True,
                       'consensus_only': True, 'enable_synchronizer': True}
    client_parameters = {'load': args.rate // args.nodes, 'transaction_size': args.tx_size,
                         'initial_delay': {'secs': 0, 'nanos': 0}}
    (directory / 'node.yaml').write_text(yaml.safe_dump(node_parameters), encoding='utf-8')
    (directory / 'client.yaml').write_text(yaml.safe_dump(client_parameters), encoding='utf-8')
    record = {'status': 'starting', 'commit': COMMIT, 'nodes': args.nodes,
              'requested_total_tps': args.rate, 'effective_total_tps': client_parameters['load'] * args.nodes,
              'node_parameters': node_parameters, 'client_parameters_per_node': client_parameters,
              'warmup_s': args.warmup, 'measurement_s': args.duration,
              'base_port': args.base_port, 'tokio_worker_threads_per_node': args.worker_threads,
              'command_prefix': binary_command, 'pids': []}
    write_json(directory / 'run.json', record)
    processes, logs = [], []
    summary = None

    def alive():
        for index, process in enumerate(processes):
            code = process.poll()
            if code is not None:
                raise RuntimeError(f'Validator {index} exited with code {code}; see {directory / f"node-{index}.log"}')

    def collect():
        alive()

        def get(index):
            port = args.base_port + args.nodes + index
            start = time.monotonic()
            with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/metrics', timeout=3) as response:
                raw = response.read().decode('utf-8')
            end = time.monotonic()
            return str(index), {'metrics': parse_snapshot(raw), 'monotonic': (start + end) / 2,
                                'request_seconds': end - start, 'text': raw}

        with ThreadPoolExecutor(max_workers=args.nodes) as pool:
            result = dict(pool.map(get, range(args.nodes)))
        alive()
        return result

    def save_sample(phase, samples):
        data = {}
        for index, sample in samples.items():
            (metrics_dir / f'{phase}-node-{index}.prom').write_text(sample['text'], encoding='utf-8')
            data[index] = {key: value for key, value in sample.items() if key != 'text'}
        with (directory / 'samples.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'phase': phase, 'nodes': data}) + '\n')

    def pause(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            alive()
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        alive()

    try:
        env = os.environ.copy()
        env.update(CONSENSUS_ONLY='1', RUST_LOG='info', TOKIO_WORKER_THREADS=str(args.worker_threads))
        with (directory / 'genesis.log').open('w', encoding='utf-8') as log:
            subprocess.run(binary_command + ['benchmark-genesis', '--ips', *(['127.0.0.1'] * args.nodes),
                           '--working-directory', str(genesis), '--node-parameters-path', str(directory / 'node.yaml')],
                           cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        public_path = genesis / 'public-config.yaml'
        config = yaml.safe_load(public_path.read_text(encoding='utf-8'))
        if len(config['identifiers']) != args.nodes:
            raise RuntimeError('Unexpected number of generated validator identifiers')
        for index, identifier in enumerate(config['identifiers']):
            identifier['network_address'] = f'127.0.0.1:{args.base_port + index}'
            identifier['metrics_address'] = f'127.0.0.1:{args.base_port + args.nodes + index}'
        public_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
        for index in range(args.nodes):
            log = (directory / f'node-{index}.log').open('w', encoding='utf-8')
            logs.append(log)
            command = binary_command + ['run', '--authority', str(index),
                       '--committee-path', str(genesis / 'committee.yaml'),
                       '--public-config-path', str(public_path),
                       '--private-config-path', str(genesis / f'private-config-{index}.yaml'),
                       '--client-parameters-path', str(directory / 'client.yaml')]
            process = subprocess.Popen(command, cwd=directory, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            record['pids'].append(process.pid)
            write_json(directory / 'run.json', record)
            print(f'Validator {index}: PID {process.pid}; metrics 127.0.0.1:{args.base_port + args.nodes + index}', flush=True)

        ready_deadline = time.monotonic() + 60
        while True:
            alive()
            try:
                ready = collect()
                if all(value['metrics']['count'] > 0 for value in ready.values()):
                    break
            except (OSError, URLError, ValueError):
                pass
            if time.monotonic() >= ready_deadline:
                raise RuntimeError('No shared commit progress on every validator within 60 seconds; see node logs.')
            pause(0.25)
        print(f'All validators have committed transactions. Warming up for {args.warmup:g}s...', flush=True)
        pause(args.warmup)
        before = collect()
        save_sample('before', before)
        start = time.monotonic()
        deadline = start + args.duration
        sequence = 0
        while True:
            pause(min(5, max(0, deadline - time.monotonic())))
            after = collect()
            sequence += 1
            finished = time.monotonic() >= deadline
            save_sample('after' if finished else f'sample-{sequence:04}', after)
            print(f'Measuring: {time.monotonic()-start:.1f}/{args.duration:g}s', flush=True)
            if finished:
                break
        summary = summarize({i: row['metrics'] for i, row in before.items()},
                            {i: row['metrics'] for i, row in after.items()},
                            {i: after[i]['monotonic'] - before[i]['monotonic'] for i in before})
        summary.update(requested_total_tps=args.rate, effective_total_tps=record['effective_total_tps'], commit=COMMIT)
        record['status'] = 'complete'
    except BaseException as error:
        record.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                      error=str(error) or type(error).__name__)
        raise
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        stop_deadline = time.monotonic() + 5
        for process in processes:
            try:
                process.wait(timeout=max(0.1, stop_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
        record['process_exit_codes'] = [process.poll() for process in processes]
        write_json(directory / 'run.json', record)
    write_json(directory / 'summary.json', summary)
    with (directory / 'summary.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary['nodes'][0]))
        writer.writeheader()
        writer.writerows(summary['nodes'])
    return summary


def main():
    args = arguments()
    if sys.platform != 'linux':
        raise RuntimeError('Run this launcher on Ubuntu/Linux.')
    import fcntl
    with (args.repo / '.mahi-local.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another local run is already using this repository.') from error
        binary, build = verify_build(args.repo)
        check_ports(args.base_port, args.nodes)
        directory = args.repo / 'results/local' / (datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True)
        write_json(directory / 'build.json', build)

        def interrupt(signum, frame):
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGHUP, interrupt)
        print(f'Mahi-Mahi {COMMIT}\nResults: {directory}\nTarget load: {args.rate} TPS total; {args.rate // args.nodes} per validator.', flush=True)
        summary = run_cluster(args, [str(binary)], directory)
        print(f'Complete. Replica mean TPS: {summary["replica_tps_mean"]:.2f}; '
              f'mean shared commit latency: {summary["mean_latency_ms"]:.2f} ms\n{directory}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted; this run\'s validators were stopped and logs retained.', file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
