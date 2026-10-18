#!/usr/bin/env python3
"""Controlled remote-condition mechanism probe on the real repair Agent binary.

Runs ON a repair node. Owned child Agent + minimal loopback fake Hub. Pushes one
RUN_EXECUTION per case and answers REMOTE_STATE_REQ with a controlled payload, so
the Agent-side gates in validRemoteState can be isolated:

  case 1  observed_at = now - 1.0 s   value online    expect SUCCESS
  case 2  observed_at = now + 0.5 s   value online    expect BLOCKED (observed in the future)
  case 3  observed_at = now - 1.0 s   value UNKNOWN   expect BLOCKED (UNKNOWN marker)
  case 4  observed_at = now - 0.001 s value online    expect SUCCESS (tiny skew tolerated)

The Agent and the fake Hub share one clock here, so this isolates the *Agent-side*
gate; the real Hub-vs-Agent skew is measured separately (phase2c-clock-offset.json).
Prints one JSON document. No repair Hub contact.
"""
import base64
import hashlib
import json
import os
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
REMOTE_NODE_ID = "00000000-0000-0000-0000-00000000beef"

CASES = [
    ("observed_1s_past_online", -1.0, "online", "SUCCESS"),
    ("observed_500ms_future_online", +0.5, "online", "BLOCKED"),
    ("observed_1s_past_unknown", -1.0, "UNKNOWN", "BLOCKED"),
    ("observed_1ms_past_online", -0.001, "online", "SUCCESS"),
]

CONDITION = {"type": "remote", "remote": {"node_id": REMOTE_NODE_ID, "property": "online",
                                          "operator": "==", "value": "online"}}


def ts(offset=0.0):
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class Ws:
    """Blocking socket + resumable frame reader (partial frames never block forever)."""

    def __init__(self, conn):
        self.conn = conn
        self.buf = b""

    def _need(self, n, deadline):
        while len(self.buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.conn.settimeout(max(0.02, min(0.2, remaining)))
            try:
                chunk = self.conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return False
            if not chunk:
                return False
            self.buf += chunk
        return True

    def send(self, opcode, data):
        header = bytearray([0x80 | opcode])
        length = len(data)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        self.conn.sendall(bytes(header) + data)

    def send_json(self, envelope):
        self.send(0x1, json.dumps(envelope, separators=(",", ":")).encode())

    def next_frame(self, timeout=0.5):
        deadline = time.monotonic() + timeout
        if not self._need(2, deadline):
            return None
        b0, b1 = self.buf[0], self.buf[1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            if not self._need(4, deadline):
                return None
            length = struct.unpack("!H", self.buf[2:4])[0]
            offset = 4
        elif length == 127:
            if not self._need(10, deadline):
                return None
            length = struct.unpack("!Q", self.buf[2:10])[0]
            offset = 10
        mask_len = 4 if masked else 0
        if not self._need(offset + mask_len + length, deadline):
            return None
        payload = bytearray(self.buf[offset + mask_len:offset + mask_len + length])
        if masked:
            key = self.buf[offset:offset + 4]
            for i in range(len(payload)):
                payload[i] ^= key[i % 4]
        self.buf = self.buf[offset + mask_len + length:]
        return opcode, bytes(payload)


def handshake(conn):
    ws = Ws(conn)
    if not ws._need(4, time.monotonic() + 10):
        return None
    while b"\r\n\r\n" not in ws.buf:
        if not ws._need(len(ws.buf) + 1, time.monotonic() + 10):
            return None
    head, rest = ws.buf.split(b"\r\n\r\n", 1)
    ws.buf = rest
    key = None
    for line in head.decode("latin-1").split("\r\n")[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            if name.strip().lower() == "sec-websocket-key":
                key = value.strip()
    if not key:
        return None
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n"
                  "Sec-WebSocket-Protocol: nodesteer\r\n\r\n").encode())
    return ws


def main():
    workdir = sys.argv[1]
    agent_binary = sys.argv[2]
    port = int(sys.argv[3])
    binary_sha = hashlib.sha256(open(agent_binary, "rb").read()).hexdigest()
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    config = os.path.join(workdir, "probe.yaml")
    with open(config, "w") as handle:
        handle.write("hub_url: ws://127.0.0.1:%d/agent\n"
                     "registration_token: qa-sysb09-rc2\n"
                     "node_name: qa-sysb09-rc2\n"
                     "deployment_mode: native\n"
                     "data_dir: %s/data\n"
                     "heartbeat_sec: 30\nrevision_check_sec: 45\n" % (port, workdir))
    log_handle = open(os.path.join(workdir, "agent.log"), "wb")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(2)
    agent = subprocess.Popen([agent_binary, "--config", config], stdout=log_handle,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    start = time.monotonic()
    transcript = []
    results = {}
    ws = None
    index = 0
    pending = None
    ready = False

    def log(kind, **extra):
        transcript.append({"t": round(time.monotonic() - start, 3), "kind": kind, **extra})

    try:
        while time.monotonic() - start < 75:
            if ws is None:
                readable, _, _ = select.select([server], [], [], 0.2)
                if server in readable:
                    client, _ = server.accept()
                    ws = handshake(client)
                    log("CONNECT" if ws else "HANDSHAKE_FAILED")
                continue
            frame = ws.next_frame(timeout=0.2)
            if frame is not None:
                opcode, data = frame
                if opcode == 0x1:
                    try:
                        env = json.loads(data.decode())
                    except ValueError:
                        env = {}
                    mtype = env.get("type")
                    log("MSG", type=mtype)
                    if mtype == "HELLO":
                        ws.send_json({"type": "HELLO_ACK", "id": env.get("id", ""), "timestamp": ts(),
                                      "payload": {"accepted": True, "node_id": "qa-sysb09-rc2-node",
                                                  "agent_id": "qa-sysb09-rc2-agent",
                                                  "agent_credential": "qa-rc2-cred",
                                                  "desired_global_rev": 0, "node_status": "online"}})
                    elif mtype == "SYNC_REQUEST":
                        ready = True
                        ws.send_json({"type": "SYNC_RESPONSE", "id": env.get("id", ""), "timestamp": ts(),
                                      "payload": {"full_resync": True, "snapshot": True, "global_rev": 0}})
                    elif mtype == "REMOTE_STATE_REQ":
                        case = CASES[index] if index < len(CASES) else None
                        value = case[2] if case else "online"
                        reply_ts = ts(case[1] if case else -1.0)
                        log("REMOTE_STATE_REQ", case=case[0] if case else None, replied_value=value,
                            replied_observed_at=reply_ts)
                        ws.send_json({"type": "REMOTE_STATE", "id": env.get("id", ""), "timestamp": ts(),
                                      "payload": {"node_id": REMOTE_NODE_ID, "property": "online",
                                                  "value": value, "observed_at": reply_ts, "ttl_sec": 60}})
                    elif mtype == "EXECUTION_FINISHED":
                        payload = env.get("payload") or {}
                        name = CASES[index][0] if index < len(CASES) else "unknown"
                        results[name] = {"status": payload.get("status"),
                                         "block_reason": payload.get("block_reason"),
                                         "expected": CASES[index][3] if index < len(CASES) else None,
                                         "stdout": (payload.get("stdout") or "").strip(),
                                         "elapsed_s": round(time.monotonic() - start - pending, 3)
                                         if pending else None}
                        log("EXECUTION_FINISHED", case=name, status=payload.get("status"))
                        index += 1
                        pending = None
            if ready and ws is not None and pending is None and index < len(CASES):
                exec_id = str(uuid.uuid4())
                ws.send_json({"type": "RUN_EXECUTION", "id": exec_id, "timestamp": ts(),
                              "payload": {"execution_id": exec_id, "task_id": "qa-sysb09-rc2-task",
                                          "task_revision": 1, "type": "command",
                                          "command": "printf QA_REMOTE_OK", "timeout": 10, "retry": 0,
                                          "trigger_type": "manual", "condition": CONDITION}})
                pending = time.monotonic() - start
                log("RUN_EXECUTION_SENT", case=CASES[index][0], execution_id=exec_id)
            elif pending is not None and time.monotonic() - start - pending > 12:
                name = CASES[index][0]
                results[name] = {"status": "NO_FINISH", "block_reason": None,
                                 "expected": CASES[index][3]}
                index += 1
                pending = None
    finally:
        if ws is not None:
            try:
                ws.conn.close()
            except OSError:
                pass
        cleanup = {"pid": agent.pid}
        try:
            agent.send_signal(signal.SIGTERM)
            agent.wait(timeout=10)
            cleanup["sigterm_exit"] = agent.returncode
        except subprocess.TimeoutExpired:
            agent.kill()
            agent.wait(timeout=5)
            cleanup["killed"] = True
        cleanup["alive_after"] = agent.poll() is None
        server.close()
        log_handle.close()
        tail = []
        try:
            tail = [line.rstrip("\n") for line in
                    open(os.path.join(workdir, "agent.log"), errors="replace").readlines()[-6:]]
        except OSError:
            pass

    out = {
        "status": "OK",
        "child_agent_binary": {"path": agent_binary, "sha256": binary_sha},
        "cases": results,
        "all_expected": all(v.get("status") == v.get("expected") for v in results.values()),
        "transcript": transcript,
        "agent_log_tail": tail,
        "agent_cleanup": cleanup,
        "no_repair_hub_contact": True,
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
