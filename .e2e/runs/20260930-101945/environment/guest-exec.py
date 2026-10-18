#!/usr/bin/env python3
"""通过本轮私密连接清单执行客户机 stdin 脚本，不回显认证资料。"""
import argparse
import json
import pathlib
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--node', required=True)
parser.add_argument('--timeout', type=int, default=120)
parser.add_argument('--connection', default='/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json')
args = parser.parse_args()
connection = json.loads(pathlib.Path(args.connection).read_text())
node = next((item for item in connection['nodes'] if args.node in (item['id'], item['name'], item['ssh_host'])), None)
if node is None:
    parser.error('node is absent from this run connection inventory')
command = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'LogLevel=ERROR', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-p', str(node['ssh_port']), '-i', node['ssh_identity_file'], node['ssh_target']]
command += ['docker', 'exec', '-i', node['docker_container'], 'sh', '-s'] if node.get('docker_container') else ['sh', '-s']
try:
    result = subprocess.run(command, input=sys.stdin.buffer.read(), timeout=args.timeout)
except subprocess.TimeoutExpired:
    print('guest script timed out', file=sys.stderr)
    sys.exit(124)
sys.exit(result.returncode)
