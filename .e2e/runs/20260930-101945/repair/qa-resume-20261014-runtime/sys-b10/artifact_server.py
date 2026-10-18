#!/usr/bin/env python3
"""Harness-controlled artifact HTTP server (owned fixture, sys-b10).

Runs on the owned child's host (native-2) and serves deliberately controlled
responses so the harness can exercise the Agent's real download path under
interruption / stall / HTTP-error conditions without touching product code,
the Hub, or any protected Agent.

Config file `artifact_server_config.json` (same directory) provides:
  payloads: {name: base64 bytes}
  truncate: {declare_len, send_bytes, hold_sec}
  stall:    {hold_sec}

Endpoints:
  GET /payload/<name>   -> 200, exact bytes of payloads[name]
  GET /wrongbody        -> 200, payload named "__wrong__"
  GET /truncate         -> declares large Content-Length, sends a prefix, holds, closes
  GET /stall            -> sends headers only, holds (no body, no read timeout proof)
  GET /status/<code>    -> returns that HTTP status
  GET /shutdown         -> process exits (harness control)
"""
import base64
import json
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, 'artifact_server_config.json'), 'rb'))


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'sysb10-fixture/1.0'

    def log_message(self, fmt, *args):
        sys.stdout.write('REQ %s %s\n' % (self.client_address[0], fmt % args))
        sys.stdout.flush()

    def _payload(self, name):
        b64 = CFG.get('payloads', {}).get(name)
        if b64 is None:
            return None
        return base64.b64decode(b64)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/shutdown':
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            os._exit(0)
        if path.startswith('/status/'):
            try:
                code = int(path.rsplit('/', 1)[1])
            except ValueError:
                code = 400
            body = ('status-%d' % code).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == '/truncate':
            tc = CFG.get('truncate', {})
            declare = int(tc.get('declare_len', 8192))
            send_n = int(tc.get('send_bytes', 200))
            hold = float(tc.get('hold_sec', 8))
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(declare))
            self.end_headers()
            self.wfile.write(b'\x41' * send_n)
            self.wfile.flush()
            time.sleep(hold)
            # close abruptly so the client sees a short body vs declared length
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.close_connection = True
            return
        if path == '/stall':
            hold = float(CFG.get('stall', {}).get('hold_sec', 60))
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', '1024')
            self.end_headers()
            self.wfile.flush()
            time.sleep(hold)
            self.close_connection = True
            return
        if path == '/wrongbody':
            body = self._payload('__wrong__') or b'wrong-body'
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith('/payload/'):
            name = path[len('/payload/'):]
            body = self._payload(name)
            if body is None:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header('Content-Length', '0')
        self.end_headers()


def main():
    port = int(sys.argv[sys.argv.index('--port') + 1])
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    sys.stdout.write('LISTEN 127.0.0.1:%d\n' % port)
    sys.stdout.flush()
    srv.serve_forever()


if __name__ == '__main__':
    main()
