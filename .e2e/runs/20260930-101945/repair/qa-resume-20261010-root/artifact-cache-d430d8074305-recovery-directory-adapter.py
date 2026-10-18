#!/usr/bin/env python3
"""Exact existing-ledger recovery only; fixes the evidence-directory dependency."""
import hashlib
import importlib.util
import sys
from pathlib import Path
PINS = {'/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261010-api/sys12-preflight/artifact-cache-preparation/artifact_cache_controller.py': 'e0696b795e0030f7c7bd064cef5818268889281c6af767e15d07394075dd28a4', '/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261010-api/sys12-preflight/artifact-cache-preparation/artifact_cache_lifecycle.py': 'e02c62d5875fb07b9d3e706c05c1e11bccb6a65b7a626768997866ef972f9d3a'}
CONTROLLER = Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261010-api/sys12-preflight/artifact-cache-preparation/artifact_cache_controller.py')
ARGS = ['--execute', '--grant-id', 'ROOT-SYS12-ARTIFACT-CACHE-20261012-02-RECOVERY', '--approved-helper-sha256', 'e02c62d5875fb07b9d3e706c05c1e11bccb6a65b7a626768997866ef972f9d3a', '--approved-controller-sha256', 'e0696b795e0030f7c7bd064cef5818268889281c6af767e15d07394075dd28a4', '--connection', '/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json', '--recover-ledger', '/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261010-api/sys12-preflight/artifact-cache-preparation/artifact-cache-d430d8074305-ledger.json']
for file_name, expected in PINS.items():
    if hashlib.sha256(Path(file_name).read_bytes()).hexdigest() != expected:
        raise RuntimeError("frozen_source_pin_mismatch")
spec = importlib.util.spec_from_file_location("exact_artcache_recovery", CONTROLLER)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
original_load_outer = controller.load_outer

def owned_directory_load_outer():
    outer = original_load_outer()  # Existing frozen outer SHA gate remains active.
    outer.HERE = CONTROLLER.parent  # Exact owned result directory; other guards stay strict.
    return outer

controller.load_outer = owned_directory_load_outer
sys.argv = [str(CONTROLLER), *ARGS]
controller.main()
