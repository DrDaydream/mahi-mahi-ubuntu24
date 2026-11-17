"""A real subprocess/HTTP fixture; this does not simulate the consensus protocol."""
import argparse
import os
from pathlib import Path
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import yaml

p = argparse.ArgumentParser()
sub = p.add_subparsers(dest='operation', required=True)
gen = sub.add_parser('benchmark-genesis')
gen.add_argument('--ips', nargs='+', required=True)
gen.add_argument('--working-directory', required=True)
gen.add_argument('--node-parameters-path', required=True)
run = sub.add_parser('run')
run.add_argument('--authority', type=int, required=True)
for flag in ('committee-path', 'public-config-path', 'private-config-path', 'client-parameters-path'):
    run.add_argument('--' + flag, required=True)
args = p.parse_args()

if args.operation == 'benchmark-genesis':
    directory = Path(args.working_directory)
    directory.mkdir(parents=True, exist_ok=True)
    parameters = yaml.safe_load(Path(args.node_parameters_path).read_text())
    identifiers = []
    for index, ip in enumerate(args.ips):
        identifiers.append({'public_key': 'test-key', 'network_address': f'{ip}:{1500+index}',
                            'metrics_address': f'{ip}:{1500+len(args.ips)+index}'})
        storage = directory / f'storage-{index}'
        storage.mkdir()
        (directory / f'private-config-{index}.yaml').write_text(yaml.safe_dump({
            'authority': index, 'keypair': 'test-key', 'storage_path': str(storage)}))
    (directory / 'committee.yaml').write_text('test-committee: true\n')
    (directory / 'public-config.yaml').write_text(yaml.safe_dump({
        'identifiers': identifiers, 'parameters': parameters}))
    raise SystemExit(0)

for filename in (args.committee_path, args.private_config_path):
    if not Path(filename).is_file():
        raise SystemExit(2)
if os.environ.get('FAKE_EXIT_AUTHORITY') == str(args.authority):
    print('deliberate validator failure', flush=True)
    raise SystemExit(7)
config = yaml.safe_load(Path(args.public_config_path).read_text())
client = yaml.safe_load(Path(args.client_parameters_path).read_text())
rate = client['load'] * len(config['identifiers'])
address = config['identifiers'][args.authority]['metrics_address']
host, port = address.split(':')
start = time.monotonic()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        count = 100 + int((time.monotonic() - start) * rate)
        body = (f'latency_s_count{{workload="shared"}} {count}\n'
                f'latency_s_sum{{workload="shared"}} {count/10}\n').encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

HTTPServer((host, int(port)), Handler).serve_forever()
