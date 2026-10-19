#!/usr/bin/env python3
"""qa-resume-20261018-soak: fault-injection TCP proxy (owned test tooling).

Transparent TCP forwarder with a line-based control channel, used to inject
websocket / HTTP faults without touching any production endpoint:

  kill            close all active data connections (hard shutdown)
  drop on|off     while on, refuse new data connections immediately
  throttle <bps>  per-connection throughput cap (0 = unlimited)
  stats           JSON snapshot of counters + active connections
  stop            stop the proxy process

Only ever targets 127.0.0.1 owned ports. Logs JSONL events to --log.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

BUF = 65536


class Proxy:
    def __init__(self, listen_port, target_host, target_port, log_path, control_port,
                 throttle_bps=0):
        self.listen_port = listen_port
        self.target = (target_host, target_port)
        self.log_path = log_path
        self.control_port = control_port
        self.throttle_bps = throttle_bps
        self.lock = threading.Lock()
        self.conns = {}          # id -> {'csock': s, 'tsock': s, 'bytes': [up, down]}
        self.next_id = 0
        self.drop = False
        self.stopping = False
        self.stats = {'accepted': 0, 'refused': 0, 'killed': 0, 'closed': 0,
                      'bytes_up': 0, 'bytes_down': 0}

    # ---------------------------------------------------------------- logging
    def log(self, event, **kw):
        rec = {'t': time.time(), 'event': event, 'port': self.listen_port}
        rec.update(kw)
        try:
            with open(self.log_path, 'a') as f:
                f.write(json.dumps(rec, separators=(',', ':')) + '\n')
        except OSError:
            pass

    # ---------------------------------------------------------------- data path
    def handle(self, csock, addr):
        cid = None
        with self.lock:
            if self.drop:
                self.stats['refused'] += 1
                csock.close()
                self.log('refused', addr=str(addr))
                return
            self.stats['accepted'] += 1
        try:
            tsock = socket.create_connection(self.target, timeout=5)
            tsock.settimeout(None)  # steady-state: fully blocking (no idle timeout)
        except OSError as exc:
            self.log('target_connect_failed', addr=str(addr), error=type(exc).__name__)
            csock.close()
            return
        try:
            csock.settimeout(None)
        except OSError:
            pass
        with self.lock:
            if self.drop:
                self.stats['refused'] += 1
                tsock.close()
                csock.close()
                self.log('refused_after_connect', addr=str(addr))
                return
            cid = self.next_id
            self.next_id += 1
            self.conns[cid] = {'csock': csock, 'tsock': tsock, 'bytes': [0, 0],
                               'addr': str(addr), 'opened': time.time()}
        self.log('open', cid=cid, addr=str(addr))
        t1 = threading.Thread(target=self.pump, args=(cid, csock, tsock, 0), daemon=True)
        t2 = threading.Thread(target=self.pump, args=(cid, tsock, csock, 1), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        with self.lock:
            self.conns.pop(cid, None)
            self.stats['closed'] += 1
        self.log('close', cid=cid)

    def pump(self, cid, src, dst, direction):
        """direction 0 = client->target (up), 1 = target->client (down)."""
        try:
            while True:
                data = src.recv(BUF)
                if not data:
                    self.log('eof', cid=cid, direction=direction)
                    break
                dst.sendall(data)
                with self.lock:
                    if cid in self.conns:
                        self.conns[cid]['bytes'][direction] += len(data)
                    if direction == 0:
                        self.stats['bytes_up'] += len(data)
                    else:
                        self.stats['bytes_down'] += len(data)
                bps = self.throttle_bps
                if bps > 0:
                    time.sleep(len(data) / float(bps))
        except OSError as exc:
            self.log('pump_error', cid=cid, direction=direction, error=type(exc).__name__)
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass

    # ---------------------------------------------------------------- control
    def kill_all(self):
        with self.lock:
            items = list(self.conns.items())
        for cid, c in items:
            for key in ('csock', 'tsock'):
                try:
                    c[key].shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    c[key].close()
                except OSError:
                    pass
        with self.lock:
            self.stats['killed'] += len(items)
        self.log('kill_all', n=len(items))

    def control(self, conn):
        f = conn.makefile('rwb')
        try:
            for raw in f:
                cmd = raw.decode('utf-8', 'replace').strip()
                if cmd == 'kill':
                    self.kill_all()
                    resp = {'ok': True, 'killed': True}
                elif cmd.startswith('drop'):
                    arg = cmd.split(None, 1)[1] if len(cmd.split(None, 1)) > 1 else ''
                    with self.lock:
                        self.drop = arg == 'on'
                    self.log('drop', on=self.drop)
                    resp = {'ok': True, 'drop': self.drop}
                elif cmd.startswith('throttle'):
                    arg = cmd.split(None, 1)[1] if len(cmd.split(None, 1)) > 1 else '0'
                    self.throttle_bps = int(float(arg))
                    self.log('throttle', bps=self.throttle_bps)
                    resp = {'ok': True, 'throttle_bps': self.throttle_bps}
                elif cmd == 'stats':
                    with self.lock:
                        resp = {'ok': True, 'drop': self.drop, 'throttle_bps': self.throttle_bps,
                                'active': len(self.conns), 'stats': dict(self.stats),
                                'conns': {str(cid): {'addr': c['addr'],
                                                     'bytes_up': c['bytes'][0],
                                                     'bytes_down': c['bytes'][1],
                                                     'age': round(time.time() - c['opened'], 3)}
                                          for cid, c in self.conns.items()}}
                elif cmd == 'stop':
                    f.write((json.dumps({'ok': True, 'stopping': True}) + '\n').encode())
                    f.flush()
                    conn.close()
                    self.stopping = True
                    return
                else:
                    resp = {'ok': False, 'error': 'unknown command'}
                f.write((json.dumps(resp) + '\n').encode())
                f.flush()
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def serve(self):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', self.listen_port))
        srv.listen(128)
        ctl = socket.socket()
        ctl.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ctl.bind(('127.0.0.1', self.control_port))
        ctl.listen(8)
        ctl.settimeout(0.5)
        srv.settimeout(0.5)
        self.log('listening', target='%s:%d' % self.target, control=self.control_port,
                 throttle_bps=self.throttle_bps)
        while not self.stopping:
            for listener, is_ctl in ((srv, False), (ctl, True)):
                try:
                    conn, addr = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                if is_ctl:
                    threading.Thread(target=self.control, args=(conn,), daemon=True).start()
                else:
                    threading.Thread(target=self.handle, args=(conn, addr), daemon=True).start()
        srv.close()
        ctl.close()
        self.log('stopped')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--listen', type=int, required=True)
    ap.add_argument('--target-host', default='127.0.0.1')
    ap.add_argument('--target-port', type=int, required=True)
    ap.add_argument('--control', type=int, required=True)
    ap.add_argument('--log', required=True)
    ap.add_argument('--throttle-bps', type=int, default=0)
    args = ap.parse_args()
    os.umask(0o077)
    Proxy(args.listen, args.target_host, args.target_port, args.log, args.control,
          args.throttle_bps).serve()


if __name__ == '__main__':
    main()
