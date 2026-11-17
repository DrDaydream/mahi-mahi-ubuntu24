import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
import run_local as app

FIXTURE = Path(__file__).with_name('fake_validator.py')


def free_range():
    for base in range(2000, 6000, 17):
        sockets = []
        try:
            for port in list(range(base, base + 8)) + [10 * (base + i) for i in range(4)]:
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(('127.0.0.1', port))
            return base
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError('No available ports for fixture')


def parameters(base):
    return argparse.Namespace(nodes=4, rate=400, duration=0.35, warmup=0,
                              tx_size=512, base_port=base, leaders=4,
                              worker_threads=2)


class LocalTests(unittest.TestCase):
    def test_success_uses_replica_mean_and_preserves_unrelated_process(self):
        # Catch summing replicas, incorrect units, lost artifacts, and global process cleanup.
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                args = parameters(free_range())
                result = app.run_cluster(args, [sys.executable, str(FIXTURE)], directory)
                self.assertAlmostEqual(result['mean_latency_ms'], 100.0, places=5)
                self.assertTrue(350 < result['replica_tps_mean'] < 450, result)
                self.assertEqual(len(result['nodes']), 4)
                self.assertEqual(json.loads((directory / 'run.json').read_text())['status'], 'complete')
                self.assertTrue((directory / 'summary.csv').is_file())
                self.assertTrue((directory / 'metrics' / 'after-node-0.prom').is_file())
                config = yaml.safe_load((directory / 'genesis' / 'public-config.yaml').read_text())
                self.assertEqual(config['identifiers'][0]['network_address'], f'127.0.0.1:{args.base_port}')
                self.assertEqual(config['identifiers'][3]['metrics_address'], f'127.0.0.1:{args.base_port+7}')
                self.assertEqual(config['parameters']['wave_length'], 5)
                self.assertEqual(yaml.safe_load((directory / 'client.yaml').read_text())['load'], 100)
                app.check_ports(args.base_port, args.nodes)
                self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=5)

    def test_failed_validator_returns_error_and_cleans_up_remaining_children(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            args = parameters(free_range())
            with patch.dict(os.environ, {'FAKE_EXIT_AUTHORITY': '1'}):
                with self.assertRaisesRegex(RuntimeError, 'Validator 1 exited'):
                    app.run_cluster(args, [sys.executable, str(FIXTURE)], directory)
            record = json.loads((directory / 'run.json').read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertFalse((directory / 'summary.json').exists())
            self.assertIn('deliberate validator failure', (directory / 'node-1.log').read_text())
            app.check_ports(args.base_port, args.nodes)

    def test_conflicting_port_does_not_launch_or_stop_other_service(self):
        with socket.socket() as sock:
            sock.bind(('0.0.0.0', free_range()))
            port = sock.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, 'Port'):
                app.check_ports(port, 1)
            self.assertEqual(sock.getsockname()[1], port)

    def test_default_ports_can_round_trip_through_upstream_peer_mapping(self):
        args = app.arguments([])
        for index in range(args.nodes):
            advertised = args.base_port + index
            self.assertEqual(((advertised * 10) & 65535) // 10, advertised)

    def test_rejects_source_port_overflow_before_starting_processes(self):
        for base in (17000, 18000, 6551):
            with self.subTest(base=base), self.assertRaises(SystemExit):
                app.arguments(['--nodes', '4', '--base-port', str(base)])
            with self.subTest(preflight=base), self.assertRaisesRegex(RuntimeError, 'source port'):
                app.check_ports(base, 4)

    def test_last_valid_base_port_is_accepted(self):
        self.assertEqual(app.arguments(['--nodes', '4', '--base-port', '6550']).base_port, 6550)

    def test_source_port_conflict_is_detected_without_stopping_other_service(self):
        base = free_range()
        with socket.socket() as sock:
            sock.bind(('0.0.0.0', (base + 3) * 10))
            with self.assertRaisesRegex(RuntimeError, f'Port {(base + 3) * 10}'):
                app.check_ports(base, 4)
            self.assertEqual(sock.getsockname()[1], (base + 3) * 10)

    def test_rate_rounding_is_not_silent(self):
        with self.assertRaises(SystemExit):
            app.arguments(['--nodes', '4', '--rate', '1001'])

    def test_invalid_configuration_is_rejected(self):
        for flags in (['--nodes', '3'], ['--tx-size', '16'], ['--duration', '0'],
                      ['--warmup', '-1'], ['--base-port', '65530'], ['--leaders', '5']):
            with self.subTest(flags=flags), self.assertRaises(SystemExit):
                app.arguments(flags)


if __name__ == '__main__':
    unittest.main()
