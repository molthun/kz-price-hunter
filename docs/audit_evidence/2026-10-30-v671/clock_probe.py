"""Independent clock/serialization probe; no database or external requests.

Run from repository root: ./venv/bin/python docs/audit_evidence/2026-10-30-v671/clock_probe.py
"""
import asyncio
import importlib.util
from pathlib import Path
import subprocess
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("audit_product_verify", ROOT / "product_verify.py")
current = importlib.util.module_from_spec(spec)
spec.loader.exec_module(current)
previous = types.ModuleType("previous_product_verify")
exec(compile(subprocess.check_output(["git", "show", "ce70a09:product_verify.py"], cwd=ROOT),
             "ce70a09:product_verify.py", "exec"), previous.__dict__)


def probe(module, start, durations, interval=90, gap=0, concurrent=False, unavailable=False):
    clock = [float(start)]
    starts, waits = [], []
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
        await real_sleep(0)

    async def request(*args):
        index = len(starts)
        starts.append(clock[0])
        await real_sleep(0)
        clock[0] += durations[index]
        return None if unavailable else []

    async def scenario():
        if concurrent:
            await asyncio.gather(*(module.searxng_search("http://unused", str(i), min_interval=interval)
                                   for i in range(len(durations))))
        else:
            for i in range(len(durations)):
                if i:
                    clock[0] += gap
                await module.searxng_search("http://unused", str(i), min_interval=interval)

    # Preserve the actual module's initial sentinel; reset only its lock for each loop.
    with patch.object(module, "_last_search", module._last_search), patch.object(module, "_search_lock", None), \
            patch.object(module, "_searxng_request", request), patch.object(time, "monotonic", lambda: clock[0]), \
            patch.object(asyncio, "sleep", sleep):
        asyncio.run(scenario())
    return starts, waits


cases = [
    ("first_at_zero", dict(start=0, durations=[0, 0]), ([0, 90], [90])),
    ("fresh_boot", dict(start=30, durations=[0, 0]), ([30, 120], [90])),
    ("long_uptime", dict(start=100000, durations=[0, 0]), ([100000, 100090], [90])),
    ("request_takes_15s", dict(start=30, durations=[15, 0]), ([30, 120], [75])),
    ("interval_already_elapsed", dict(start=30, durations=[0, 0], gap=100), ([30, 130], [])),
    ("zero_interval", dict(start=0, durations=[0, 0], interval=0), ([0, 0], [])),
    ("unavailable_still_throttled", dict(start=30, durations=[0, 0], unavailable=True), ([30, 120], [90])),
    ("three_concurrent_requests", dict(start=30, durations=[15, 15, 15], concurrent=True),
     ([30, 120, 210], [75, 75])),
]
for name, kwargs, expected in cases:
    actual = probe(current, **kwargs)
    assert actual == expected, (name, actual, expected)
    print(f"PASS {name}: starts={actual[0]}, waits={actual[1]}")
old = probe(previous, start=30, durations=[0, 0])
assert old == ([90, 180], [60, 90]), old
print(f"CONTROL ce70a09 reproduces defect: starts={old[0]}, waits={old[1]}")
print("RESULT: 8/8 current scenarios PASS; previous-version defect reproduced")
