#!/usr/bin/env python3
"""在已启动的隔离实例上执行 enrollment 连发探针（不重复启动实例）。"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tail_common as C  # noqa: E402

EV = HERE.parent
CREDS = sorted(C.CRED_DIR.glob("tail-creds-*.json"))
if not CREDS:
    raise SystemExit("no creds")
cred = json.loads(CREDS[-1].read_text())
NONCE = cred["instance"].replace("nodesteer-qa-", "")
PORT = cred["port"]
LOG = f"/var/log/{cred['instance']}.log"
BASE = f"http://{C.HOST}:{PORT}"
OUT = EV / "enroll-burst.json"


def sh(cmd, check=False, timeout=120):
    return C.ssh(cmd, check=check, timeout=timeout)


def login():
    body = json.dumps({"username": cred["admin"], "password": cred["password"]}).encode()
    req = urllib.request.Request(BASE + "/api/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]


def enroll(token, name, ip=""):
    payload = {"node_name": name}
    if ip:
        payload["node_ip"] = ip
    body = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + "/api/nodes/enrollment", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"http": r.status, "body": r.read().decode("utf-8", "replace")[:300],
                    "ms": round((time.time() - t0) * 1000, 1)}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "replace")[:600],
                "ms": round((time.time() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"http": 0, "body": repr(e)[:300], "ms": round((time.time() - t0) * 1000, 1)}


def counts(rs):
    c = {}
    for r in rs:
        c[str(r["http"])] = c.get(str(r["http"]), 0) + 1
    return c


def main() -> None:
    token = login()
    res = {"nonce": NONCE, "instance": cred["instance"], "port": PORT,
           "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "pattern": "rapid POST /api/nodes/enrollment",
           "binary_sha256": sh(f"sha256sum {C.REMOTE_BIN} | awk '{{print $1}}'").stdout.strip(),
           "groups": {}}

    # A: 同参数（同 name/ip）快速连发 12 次
    a = [enroll(token, f"qa-{NONCE}-same", "10.99.0.1") for _ in range(12)]
    res["groups"]["A_same_params_sequential"] = {"results": a, "http_counts": counts(a)}

    # A2: 同参数再来 12 次（提高竞态命中率）
    a2 = [enroll(token, f"qa-{NONCE}-same2", "10.99.0.2") for _ in range(12)]
    res["groups"]["A2_same_params_sequential"] = {"results": a2, "http_counts": counts(a2)}

    # B: 不同参数快速连发 12 次
    b = [enroll(token, f"qa-{NONCE}-diff-{i:02d}", f"10.99.1.{i}") for i in range(12)]
    res["groups"]["B_diff_params_sequential"] = {"results": b, "http_counts": counts(b)}

    # C: 并发 12 次
    with ThreadPoolExecutor(max_workers=12) as ex:
        c = list(ex.map(lambda i: enroll(token, f"qa-{NONCE}-conc-{i:02d}", f"10.99.2.{i}"), range(12)))
    res["groups"]["C_concurrent"] = {"results": c, "http_counts": counts(c)}

    # C2: 并发同参数 12 次
    with ThreadPoolExecutor(max_workers=12) as ex:
        c2 = list(ex.map(lambda _: enroll(token, f"qa-{NONCE}-csame", "10.99.3.1"), range(12)))
    res["groups"]["C2_concurrent_same"] = {"results": c2, "http_counts": counts(c2)}

    res["hub_log_error_lines"] = sh(
        f"grep -n -iE 'error|busy|locked|constraint|panic|500' {LOG} | tail -80").stdout.splitlines()
    res["hub_log_tail"] = sh(f"tail -30 {LOG}").stdout.splitlines()
    res["hub_log_sha256"] = sh(f"sha256sum {LOG} | awk '{{print $1}}'").stdout.strip()
    res["nodes_count"] = sh(
        f"python3 -c \"import sqlite3;print(sqlite3.connect('{cred['instance'].replace('nodesteer-qa-','/var/lib/nodesteer-qa-')}/nodesteer.db').execute('select count(*) from nodes').fetchone()[0])\"",
        check=False).stdout.strip()

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "groups"}, ensure_ascii=False, indent=1)[:2500])
    for g, v in res["groups"].items():
        print(g, v["http_counts"])
        for r in v["results"]:
            if r["http"] != 200:
                print("   NON200:", r)


if __name__ == "__main__":
    main()
