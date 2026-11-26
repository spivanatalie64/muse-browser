#!/usr/bin/env python3
"""
Sprint 1 acceptance smoke test (issue #2). Runs on the host, standard
library only. Three clean cycles of:

    daemon start -> Firefox up on a blank page -> engine version reported
    -> navigate to https://example.com -> title reads "Example Domain"
    -> clean stop

Prints every API response so the output can be quoted as evidence.
Exits 0 only if all three cycles pass.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.join(os.path.expanduser("~"), "muse-browser")
API = "http://127.0.0.1:8765"

with open(os.path.join(BASE, "token")) as f:
    TOKEN = f.read().strip()


def call(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(API + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Muse-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def expect(cond, msg):
    if not cond:
        print(f"FAIL: {msg}", flush=True)
        sys.exit(1)


def main():
    print("muse-browser Sprint 1 smoke test -- 3 start/navigate/stop cycles",
          flush=True)
    for cycle in (1, 2, 3):
        print(f"--- cycle {cycle} ---", flush=True)
        r = call("/stop")
        print("stop (clean slate):", r, flush=True)
        r = call("/start")
        print("start:", r, flush=True)
        expect(r.get("ok"), f"cycle {cycle}: /start not ok: {r}")
        expect(r.get("browser_running"),
               f"cycle {cycle}: browser not running after /start")
        version = r.get("firefox_version")
        expect(version, f"cycle {cycle}: no firefox version reported")
        print(f"firefox version: {version}", flush=True)
        r = call("/title")
        print("title on blank page:", r, flush=True)
        r = call("/navigate", {"url": "https://example.com"})
        print("navigate:", r, flush=True)
        expect(r.get("ok"), f"cycle {cycle}: /navigate not ok: {r}")
        expect(r.get("title") == "Example Domain",
               f"cycle {cycle}: expected title 'Example Domain', "
               f"got {r.get('title')!r}")
        r = call("/title")
        expect(r.get("title") == "Example Domain",
               f"cycle {cycle}: /title mismatch: {r}")
        r = call("/stop")
        print("stop:", r, flush=True)
        expect(r.get("ok") and not r.get("browser_running"),
               f"cycle {cycle}: /stop failed: {r}")
        print(f"CYCLE {cycle}: PASS", flush=True)
        time.sleep(1)
    print("SMOKE RESULT: 3/3 cycles passed", flush=True)


if __name__ == "__main__":
    main()
