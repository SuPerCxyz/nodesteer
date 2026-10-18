#!/usr/bin/env python3
"""Owned-child-Agent heartbeat / revision-check / reconnect-backoff / jitter probe.

Runs ON a repair node as a standalone, non-systemd process.  It starts a minimal
RFC6455 WebSocket "fake Hub" on 127.0.0.1 and launches a *second*, fully owned
`nodesteer-agent` process (same binary/sha as the repair agents) against it in a
private temp data dir.  It never contacts the repair Hub, never touches the
`nodesteer-agent*.service` units and never writes outside its temp dir.

Measured, from the real production binary:
  * HEARTBEAT send cadence (+/-20% jitter) and the runtime SETTINGS override;
  * REVISION_CHECK send cadence;
  * reconnect backoff after the peer closes the connection.

Prints a single JSON document on stdout.
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

HEARTBEAT_SEC = 6
REVISION_SEC = 9
SETTINGS_CHANGE_AT = 50.0
CHANGED_HEARTBEAT_SEC = 12
CHANGED_REVISION_SEC = 15
DROP_AT = [15.0, 35.0, 70.0, 92.0]
RUN_SECONDS = 115.0


def ts():
    """RFC3339 timestamp string: protocol.Envelope.Timestamp is a time.Time."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class Frame:
    __slots__ = ("opcode", "payload")

    def __init__(self, opcode, payload):
        self.opcode = opcode
        self.payload = payload


def read_frame(conn, buf):
    """Read one WebSocket frame. Returns (Frame, buf) or (None, buf) when closed."""
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
                chunk = conn.recv(65536)
                if not chunk:
                    return None, buf
                buf += chunk
            length = struct.unpack("!H", buf[2:4])[0]
            offset = 4
        elif length == 127:
            while len(buf) < 10:
                chunk = conn.recv(65536)
                if not chunk:
                    return None, buf
                buf += chunk
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
        return Frame(opcode, bytes(payload)), buf[offset + mask_len + length:]


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
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    conn.sendall(
        ("HTTP/1.1 101 Switching Protocols\r\n"
         "Upgrade: websocket\r\n"
         "Connection: Upgrade\r\n"
         "Sec-WebSocket-Accept: " + accept + "\r\n"
         "Sec-WebSocket-Protocol: nodesteer\r\n\r\n").encode())
    return True, rest


def make_config(path, port, workdir):
    heartbeat = HEARTBEAT_SEC
    revision = REVISION_SEC
    with open(path, "w") as handle:
        handle.write(
            "hub_url: ws://127.0.0.1:%d/agent\n"
            "registration_token: qa-sysb09-owned-probe\n"
            "node_name: qa-sysb09-owned-heartbeat-probe\n"
            "deployment_mode: native\n"
            "data_dir: %s/data\n"
            "heartbeat_sec: %d\n"
            "revision_check_sec: %d\n" % (port, workdir, heartbeat, revision))


def main():
    global RUN_SECONDS, DROP_AT, SETTINGS_CHANGE_AT
    workdir = sys.argv[1]
    agent_binary = sys.argv[2]
    port = int(sys.argv[3])
    if len(sys.argv) > 4:
        # smoke-test override: run_seconds
        RUN_SECONDS = float(sys.argv[4])
        DROP_AT = [x for x in DROP_AT if x < RUN_SECONDS]
        SETTINGS_CHANGE_AT = min(SETTINGS_CHANGE_AT, RUN_SECONDS / 2)
    binary_sha = hashlib.sha256(open(agent_binary, "rb").read()).hexdigest()
    os.makedirs(workdir, exist_ok=True)
    shutil.rmtree(os.path.join(workdir, "data"), ignore_errors=True)
    config = os.path.join(workdir, "probe.yaml")
    make_config(config, port, workdir)
    log_path = os.path.join(workdir, "agent.log")
    log_handle = open(log_path, "wb")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(4)

    agent = subprocess.Popen([agent_binary, "--config", config],
                             stdout=log_handle, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)

    start = time.monotonic()
    events = []
    heartbeat_ts = []
    revision_ts = []
    connections = []
    drops = []
    messages = {}

    def record(kind, **extra):
        events.append({"t": round(time.monotonic() - start, 3), "kind": kind, **extra})

    conn = None
    buf = b""
    conn_opened = None
    settings_state = {"heartbeat_interval_sec": str(HEARTBEAT_SEC),
                      "revision_check_interval_sec": str(REVISION_SEC)}
    num_settings = {"heartbeat_sec": HEARTBEAT_SEC, "revision_check_sec": REVISION_SEC,
                    "max_log_bytes": 1 << 20}
    drop_index = 0
    changed = False

    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= RUN_SECONDS:
                break
            if drop_index < len(DROP_AT) and elapsed >= DROP_AT[drop_index] and conn is not None:
                drops.append(round(time.monotonic() - start, 3))
                record("DROP", at=drops[-1])
                try:
                    send_frame(conn, 0x8, struct.pack("!H", 1001))
                except OSError:
                    pass
                try:
                    conn.close()
                except OSError:
                    pass
                if conn_opened is not None:
                    connections.append({"opened_t": round(conn_opened - start, 3),
                                        "closed_t": round(time.monotonic() - start, 3)})
                conn = None
                buf = b""
                conn_opened = None
                drop_index += 1
                continue
            if not changed and elapsed >= SETTINGS_CHANGE_AT:
                changed = True
                record("SETTINGS_SENT", heartbeat_sec=CHANGED_HEARTBEAT_SEC,
                       revision_check_sec=CHANGED_REVISION_SEC)
                settings_state = {"heartbeat_interval_sec": str(CHANGED_HEARTBEAT_SEC),
                                  "revision_check_interval_sec": str(CHANGED_REVISION_SEC)}
                num_settings = {"heartbeat_sec": CHANGED_HEARTBEAT_SEC,
                                "revision_check_sec": CHANGED_REVISION_SEC,
                                "max_log_bytes": 1 << 20}
                if conn is not None:
                    send_json(conn, {"type": "SETTINGS", "id": str(uuid.uuid4()),
                                     "timestamp": ts(),
                                     "payload": {"heartbeat_sec": CHANGED_HEARTBEAT_SEC,
                                                 "revision_check_sec": CHANGED_REVISION_SEC}})

            readable = [server] if conn is None else [server, conn]
            ready, _, _ = select.select(readable, [], [], 0.2)
            if server in ready:
                client, _ = server.accept()
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client.settimeout(30)
                ok, rest = handshake(client)
                if not ok:
                    client.close()
                else:
                    conn = client
                    buf = rest
                    conn_opened = time.monotonic()
                    record("CONNECT")
            if conn is not None and conn in ready:
                try:
                    frame, buf = read_frame(conn, buf)
                except (OSError, socket.timeout):
                    frame = None
                if frame is None:
                    if conn_opened is not None:
                        connections.append({"opened_t": round(conn_opened - start, 3),
                                            "closed_t": round(time.monotonic() - start, 3)})
                    record("PEER_CLOSED")
                    conn.close()
                    conn = None
                    buf = b""
                    conn_opened = None
                    continue
                if frame.opcode == 0x8:
                    conn.close()
                    conn = None
                    buf = b""
                    conn_opened = None
                    continue
                if frame.opcode == 0x9:
                    send_frame(conn, 0xA, frame.payload)
                    continue
                if frame.opcode != 0x1:
                    continue
                try:
                    env = json.loads(frame.payload.decode())
                except ValueError:
                    continue
                mtype = env.get("type")
                messages[mtype] = messages.get(mtype, 0) + 1
                t = round(time.monotonic() - start, 3)
                record("MSG", type=mtype)
                if mtype == "HELLO":
                    send_json(conn, {"type": "HELLO_ACK", "id": env.get("id", ""),
                                     "timestamp": ts(),
                                     "payload": {"accepted": True,
                                                 "node_id": "qa-sysb09-owned-probe-node",
                                                 "agent_id": "qa-sysb09-owned-probe-agent",
                                                 "agent_credential": "qa-owned-probe-cred",
                                                 "desired_global_rev": 0,
                                                 "node_status": "online",
                                                 "settings": dict(settings_state)}})
                elif mtype == "HEARTBEAT":
                    heartbeat_ts.append(t)
                    send_json(conn, {"type": "HEARTBEAT_ACK", "id": env.get("id", ""),
                                     "timestamp": ts(),
                                     "payload": dict(num_settings)})
                elif mtype == "REVISION_CHECK":
                    revision_ts.append(t)
                    send_json(conn, {"type": "REVISION_CHECK_ACK", "id": env.get("id", ""),
                                     "timestamp": ts(),
                                     "payload": {"node_id": "qa-sysb09-owned-probe-node",
                                                 "global_rev": 0}})
                elif mtype == "SYNC_REQUEST":
                    send_json(conn, {"type": "SYNC_RESPONSE", "id": env.get("id", ""),
                                     "timestamp": ts(),
                                     "payload": {"full_resync": True, "snapshot": True,
                                                 "global_rev": 0}})
                elif mtype == "INVENTORY":
                    pass
    finally:
        end = time.monotonic()
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
            if conn_opened is not None:
                connections.append({"opened_t": round(conn_opened - start, 3),
                                    "closed_t": round(end - start, 3)})
        agent_cleanup = {"pid": agent.pid}
        try:
            agent.send_signal(signal.SIGTERM)
            agent.wait(timeout=10)
            agent_cleanup["sigterm_exit"] = agent.returncode
        except subprocess.TimeoutExpired:
            agent.kill()
            agent.wait(timeout=5)
            agent_cleanup["killed"] = True
        agent_cleanup["alive_after"] = agent.poll() is None
        server.close()
        log_handle.close()
        log_tail = []
        try:
            with open(log_path, "r", errors="replace") as handle:
                log_tail = [line.rstrip("\n") for line in handle.readlines()[-8:]]
        except OSError:
            pass

    def deltas(series):
        return [round(series[i + 1] - series[i], 3) for i in range(len(series) - 1)]

    def in_window(a, b):
        """True when no connection close/open happened between a and b."""
        for window in connections:
            if window["closed_t"] is not None and a < window["closed_t"] < b:
                return False
            if a < window["opened_t"] < b:
                return False
        return True

    hb_raw = deltas(heartbeat_ts)
    pairs = [(round(heartbeat_ts[i + 1] - heartbeat_ts[i], 3), heartbeat_ts[i])
             for i in range(len(heartbeat_ts) - 1)
             if in_window(heartbeat_ts[i], heartbeat_ts[i + 1])]
    hb_clean = [delta for delta, _ in pairs]
    change_t = SETTINGS_CHANGE_AT if changed else None
    hb_pre = [delta for delta, at in pairs if change_t is None or at < change_t]
    hb_post = [delta for delta, at in pairs if change_t is not None and at >= change_t]
    rev_raw = deltas(revision_ts)
    rev_clean = [round(revision_ts[i + 1] - revision_ts[i], 3)
                 for i in range(len(revision_ts) - 1)
                 if in_window(revision_ts[i], revision_ts[i + 1])]
    backoff = []
    for index in range(len(connections) - 1):
        if connections[index]["closed_t"] is not None:
            backoff.append(round(connections[index + 1]["opened_t"] - connections[index]["closed_t"], 3))
    result = {
        "status": "OK",
        "node_local_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owned_child_agent_binary": {"path": agent_binary, "sha256": binary_sha},
        "config_file": {
            "hub_url": "ws://127.0.0.1:%d/agent" % port,
            "heartbeat_sec": HEARTBEAT_SEC,
            "revision_check_sec": REVISION_SEC,
            "node_name": "qa-sysb09-owned-heartbeat-probe",
        },
        "fake_hub": {
            "port": port,
            "hello_ack_settings": {"heartbeat_interval_sec": str(HEARTBEAT_SEC),
                                   "revision_check_interval_sec": str(REVISION_SEC)},
            "runtime_settings_change_at_s": change_t,
            "runtime_settings_change_to": {"heartbeat_sec": CHANGED_HEARTBEAT_SEC,
                                           "revision_check_sec": CHANGED_REVISION_SEC},
            "drop_schedule_s": DROP_AT,
            "run_seconds": RUN_SECONDS,
        },
        "message_counts": messages,
        "connections": connections,
        "drops_requested_s": drops,
        "heartbeat_send_times_s": heartbeat_ts,
        "revision_check_send_times_s": revision_ts,
        "heartbeat_deltas_all_s": hb_raw,
        "heartbeat_deltas_same_connection_s": hb_clean,
        "heartbeat_deltas_before_settings_change_s": hb_pre,
        "heartbeat_deltas_after_settings_change_s": hb_post,
        "revision_check_deltas_s": rev_raw,
        "revision_check_deltas_same_connection_s": rev_clean,
        "reconnect_backoff_s": backoff,
        "agent_cleanup": agent_cleanup,
        "agent_log_tail": log_tail,
        "no_repair_hub_contact": True,
        "systemd_units_touched": False,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
