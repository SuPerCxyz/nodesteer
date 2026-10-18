#!/usr/bin/env python3
"""Shared read/write API helper for the p-web batch (repair authority :18080 only).

Credentials are read from the 0600 file; they are never printed.
"""
import json
import urllib.error
import urllib.request

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"


def login():
    c = json.load(open(CRED))
    st, raw = call("POST", "/api/login", body={
        "username": c["admin_username"], "password": c["admin_password"]})
    if st != 200:
        raise RuntimeError(f"login failed http={st}")
    return json.loads(raw)["token"]


def call(method, path, token=None, body=None, raw_body=None, headers=None):
    url = BASE + path
    data = None
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None and raw_body is None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def j(method, path, token=None, body=None):
    st, raw = call(method, path, token=token, body=body)
    try:
        return st, json.loads(raw)
    except Exception:
        return st, raw.decode(errors="replace")


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "data", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def counts(token):
    out = {}
    for ep in ["/api/nodes", "/api/applications", "/api/tasks", "/api/schedules",
               "/api/scripts", "/api/artifacts", "/api/transfers", "/api/executions",
               "/api/users", "/api/groups"]:
        st, payload = j("GET", ep, token)
        lst = as_list(payload)
        out[ep] = {"http": st, "count": len(lst) if lst else (
            payload.get("total") if isinstance(payload, dict) else None)}
    return out
