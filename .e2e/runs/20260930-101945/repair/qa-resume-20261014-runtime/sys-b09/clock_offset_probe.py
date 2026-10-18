#!/usr/bin/env python3
"""Measure the relative wall-clock offset between the repair Hub and the Agent VMs.

Streams `date +%s.%N` from each host over one SSH session per host and stamps the
local arrival time of every line.  The one-way SSH/pipe latency is a common bias
for all hosts, so *relative* offsets between hosts remain comparable, which is what
matters for the Agent-side `observed.After(now)` remote-condition gate.

Read-only; prints one JSON document.
"""
import json
import subprocess
import sys
import time

KEY = "/home/superc/.ssh/id_ed25519"
HOSTS = {
    "hub": "192.168.100.209",
    "native-1": "192.168.100.204",
    "native-2": "192.168.100.206",
    "native-3": "192.168.100.222",
    "native-4": "192.168.100.247",
}
SAMPLES = 6


def sample(host_ip, samples=SAMPLES):
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
           "-i", KEY, "root@" + host_ip,
           "for i in $(seq 1 %d); do date +%%s.%%N; sleep 0.4; done" % samples]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = []
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        arrive = time.time()
        text = line.strip()
        if text:
            try:
                out.append({"remote_epoch": float(text), "local_arrival_epoch": arrive,
                            "diff": round(float(text) - arrive, 6)})
            except ValueError:
                pass
    proc.wait(timeout=30)
    return out


def main():
    result = {"measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "method": "streamed `date +%s.%N` per host, local arrival stamp; "
                        "common one-way latency bias, relative offsets comparable",
              "hosts": {}}
    for label, ip in HOSTS.items():
        samples = sample(ip)
        diffs = [s["diff"] for s in samples]
        result["hosts"][label] = {
            "ip": ip,
            "samples": samples,
            "min_diff": min(diffs) if diffs else None,
            "max_diff": max(diffs) if diffs else None,
            "mean_diff": round(sum(diffs) / len(diffs), 6) if diffs else None,
            "count": len(diffs),
        }
    base = result["hosts"]["hub"]["min_diff"]
    rel = {}
    for label, data in result["hosts"].items():
        if data["min_diff"] is None or base is None:
            continue
        # host_clock - hub_clock (same-order bias cancels)
        rel[label] = round(data["min_diff"] - base, 6)
    result["relative_to_hub_clock_seconds_positive_means_host_ahead"] = rel
    result["interpretation"] = ("Agent-side validRemoteState fails closed when Hub "
                                "observed_at is ahead of the Agent clock; a positive "
                                "hub-vs-agent skew reproduces that signature.")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
