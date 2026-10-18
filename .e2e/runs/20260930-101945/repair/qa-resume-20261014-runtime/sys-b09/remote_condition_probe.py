#!/usr/bin/env python3
"""Owned-child-Agent remote-condition probe (isolates the Agent-side condition path).

Runs ON a repair node. Starts a minimal loopback "fake Hub" and one owned child
`nodesteer-agent` process (same repair binary/SHA) in a private temp data dir, then
pushes RUN_EXECUTION payloads carrying remote / and-remote conditions and records
every protocol message the child sends back, including whether it issues
REMOTE_STATE_REQ and how it finally evaluates the condition.

Prints one JSON document on stdout. No repair Hub contact, no systemd units touched.
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


def ts(offset=-1.0):
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def read_frame(conn, buf):
    while True:
        while len(buf) < 2:
            chunk = conn.recv(65536)
            if not chunk:
                return None, buf
            buf += chunk
        b0, b1 = buf[0], buf[1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            while len(buf) < 4:
                buf += conn.recv(65536)
            length = struct.unpack("!H", buf[2:4])[0]
            offset = 4
        elif length == 127:
            while len(buf) < 10:
                buf += conn.recv(65536)
            length = struct.unpack("!Q", buf[2:10])[0]
            offset = 10
        mask_len = 4 if masked else 0
        while len(buf) < offset + mask_len + length:
            chunk = conn.recv(max(65536, offset + mask_len + length - len(buf)))
            if not chunk:
                return None, buf
            buf += chunk
        payload = bytearray(buf[offset + mask_len:offset + mask_len + length])
        if masked:
            key = buf[offset:offset + 4]
            for i in range(len(payload)):
                payload[i] ^= key[i % 4]
        return (opcode, bytes(payload)), buf[offset + mask_len + length:]


def send_frame(conn, opcode, data):
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
    conn.sendall(bytes(header) + data)


def send_json(conn, envelope):
    send_frame(conn, 0x1, json.dumps(envelope, separators=(",", ":")).encode())


def handshake(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None, buf
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    key = None
    for line in head.decode("latin-1").split("\r\n")[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            if name.strip().lower() == "sec-websocket-key":
                key = value.strip()
    if not key:
        return None, rest
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n"
                  "Sec-WebSocket-Protocol: nodesteer\r\n\r\n").encode())
    return True, rest


CASES = [
    ("remote_online_eq_online", {
        "type": "remote",
        "remote": {"node_id": REMOTE_NODE_ID, "property": "online",
                   "operator": "==", "value": "online"}},
     "remote-optimistic"),
    ("and_local_remote", {
        "type": "and", "and": [
            {"type": "local", "local": {"metric": "file_exists", "operator": "==",
                                        "path": "/etc/hostname", "value": "true"}},
            {"type": "remote", "remote": {"node_id": REMOTE_NODE_ID, "property": "online",
                                          "operator": "==", "value": "online"}}]},
     "remote-optimistic"),
    ("remote_online_eq_offline", {
        "type": "remote",
        "remote": {"node_id": REMOTE_NODE_ID, "property": "online",
                   "operator": "==", "value": "offline"}},
     "remote-offline"),
]


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
                     "registration_token: qa-sysb09-remote-probe\n"
                     "node_name: qa-sysb09-remote-probe\n"
                     "deployment_mode: native\n"
                     "data_dir: %s/data\n"
                     "heartbeat_sec: 30\nrevision_check_sec: 45\n" % (port, workdir))
    log_path = os.path.join(workdir, "agent.log")
    log_handle = open(log_path, "wb")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(2)
    agent = subprocess.Popen([agent_binary, "--config", config], stdout=log_handle,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    start = time.monotonic()
    transcript = []
    finished = {}
    ready = False
    conn = None
    buf = b""

    def log(kind, **extra):
        transcript.append({"t": round(time.monotonic() - start, 3), "kind": kind, **extra})

    try:
        case_index = 0
        sent_at = None
        while time.monotonic() - start < 70:
            readable = [server] if conn is None else [server, conn]
            ready_fds, _, _ = select.select(readable, [], [], 0.2)
            if server in ready_fds:
                client, _ = server.accept()
                client.settimeout(20)
                ok, rest = handshake(client)
                if ok:
                    conn, buf = client, rest
                    log("CONNECT")
                else:
                    client.close()
            if conn is not None and conn in ready_fds:
                try:
                    frame, buf = read_frame(conn, buf)
                except (OSError, socket.timeout):
                    frame = None
                if frame is None:
                    log("PEER_CLOSED")
                    conn.close()
                    conn = None
                    buf = b""
                    continue
                opcode, data = frame
                if opcode != 0x1:
                    continue
                try:
                    env = json.loads(data.decode())
                except ValueError:
                    continue
                mtype = env.get("type")
                log("MSG", type=mtype, id=env.get("id"))
                if mtype == "HELLO":
                    send_json(conn, {"type": "HELLO_ACK", "id": env.get("id", ""), "timestamp": ts(),
                                     "payload": {"accepted": True, "node_id": "qa-sysb09-remote-node",
                                                 "agent_id": "qa-sysb09-remote-agent",
                                                 "agent_credential": "qa-remote-cred",
                                                 "desired_global_rev": 0, "node_status": "online",
                                                 "settings": {"heartbeat_interval_sec": "30",
                                                              "revision_check_interval_sec": "45"}}})
                elif mtype == "SYNC_REQUEST":
                    ready = True
                    send_json(conn, {"type": "SYNC_RESPONSE", "id": env.get("id", ""), "timestamp": ts(),
                                     "payload": {"full_resync": True, "snapshot": True, "global_rev": 0}})
                elif mtype == "REMOTE_STATE_REQ":
                    payload = json.loads(env.get("payload") or b"{}") if isinstance(env.get("payload"), (bytes, str)) else (env.get("payload") or {})
                    mode = CASES[case_index][2] if case_index < len(CASES) else "remote-optimistic"
                    value = "offline" if mode == "remote-offline" else "online"
                    log("REMOTE_STATE_REQ", payload=payload, answered_with=value)
                    send_json(conn, {"type": "REMOTE_STATE", "id": env.get("id", ""), "timestamp": ts(),
                                     "payload": {"node_id": REMOTE_NODE_ID, "property": "online",
                                                 "value": value, "observed_at": ts(-1.0), "ttl_sec": 60}})
                elif mtype == "EXECUTION_FINISHED":
                    payload = env.get("payload") or {}
                    finished[payload.get("execution_id")] = payload
                    log("EXECUTION_FINISHED", status=payload.get("status"),
                        block_reason=payload.get("block_reason"))
                elif mtype == "INVENTORY":
                    pass
            if ready and conn is not None and case_index < len(CASES) and sent_at is None:
                time.sleep(1.5)
                name, condition, mode = CASES[case_index]
                exec_id = str(uuid.uuid4())
                # make sure any pending sync response is processed first
                send_json(conn, {"type": "RUN_EXECUTION", "id": exec_id, "timestamp": ts(),
                                 "payload": {"execution_id": exec_id, "task_id": "qa-sysb09-remote-task",
                                             "task_revision": 1, "type": "command", "command": "printf QA_REMOTE_OK",
                                             "timeout": 10, "retry": 0, "trigger_type": "manual",
                                             "condition": condition}})
                log("RUN_EXECUTION_SENT", case=name, execution_id=exec_id)
                sent_at = time.monotonic()
            if sent_at is not None and len(finished) > case_index:
                case_index += 1
                sent_at = None
                if case_index >= len(CASES):
                    break
            if sent_at is not None and time.monotonic() - sent_at > 20:
                case_index += 1
                sent_at = None
    finally:
        if conn is not None:
            try:
                conn.close()
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
            tail = [line.rstrip("\n") for line in open(log_path, errors="replace").readlines()[-10:]]
        except OSError:
            pass

    result = {
        "status": "OK",
        "child_agent_binary": {"path": agent_binary, "sha256": binary_sha},
        "cases": [{"name": name, "condition": condition, "hub_reply_mode": mode} for name, condition, mode in CASES],
        "message_types_in_order": [t["type"] for t in transcript if t["kind"] == "MSG"],
        "remote_state_req_count": sum(1 for t in transcript if t["kind"] == "REMOTE_STATE_REQ"),
        "execution_finished": finished,
        "transcript": transcript,
        "agent_log_tail": tail,
        "agent_cleanup": cleanup,
        "no_repair_hub_contact": True,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
