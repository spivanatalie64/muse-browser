#!/usr/bin/env python3
"""
muse-browser daemon -- Sprint 1 browser core.

A small token-authenticated HTTP API (Python standard library only) that
supervises one geckodriver + one Firefox instance using a dedicated profile.

Sprint 1 scope (issues #1, #2): /status, /start, /stop, plus a minimal
/navigate + /title smoke path that proves the WebDriver session end to end.
The full verb set is Sprint 2 (issue #3). The API binds to localhost only;
the Tailscale bind is Sprint 2 (issue #4). Decisions: docs/architecture.md.

Host layout (nothing stateful lives in the repo):
    ~/muse-browser/firefox/    Firefox tarball install
    ~/muse-browser/bin/        geckodriver
    ~/muse-browser/profile/    dedicated Firefox profile
    ~/muse-browser/daemon/     this file, deployed
    ~/muse-browser/token       API token, chmod 600, generated on the host
    ~/muse-browser/logs/       geckodriver log
"""

import hmac
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.join(os.path.expanduser("~"), "muse-browser")
TOKEN_FILE = os.path.join(BASE, "token")
PROFILE_DIR = os.path.join(BASE, "profile")
FIREFOX_BIN = os.path.join(BASE, "firefox", "firefox")
GECKODRIVER_BIN = os.path.join(BASE, "bin", "geckodriver")
GD_LOG = os.path.join(BASE, "logs", "geckodriver.log")

API_HOST = "127.0.0.1"
API_PORT = 8765
GD_HOST = "127.0.0.1"
GD_PORT = 4444


def log(msg):
    print(f"[muse-browserd] {msg}", flush=True)


class Driver:
    """Owns the geckodriver subprocess and the one WebDriver session."""

    def __init__(self):
        self.proc = None
        self.session_id = None
        self.capabilities = {}
        self.lock = threading.RLock()

    def wd(self, method, path, body=None, timeout=60):
        url = f"http://{GD_HOST}:{GD_PORT}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=utf-8")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def info(self):
        return {
            "browser_running": self.session_id is not None,
            "firefox_version": self.capabilities.get("browserVersion"),
            "session_id": self.session_id,
        }

    def start(self):
        with self.lock:
            if self.session_id:
                return self.info()
            os.makedirs(PROFILE_DIR, exist_ok=True)
            os.makedirs(os.path.dirname(GD_LOG), exist_ok=True)
            logfile = open(GD_LOG, "ab")
            self.proc = subprocess.Popen(
                [GECKODRIVER_BIN, "--host", GD_HOST, "--port", str(GD_PORT)],
                stdout=logfile, stderr=logfile)
            deadline = time.time() + 30
            while True:
                try:
                    st = self.wd("GET", "/status", timeout=5)
                    if st.get("value", {}).get("ready"):
                        break
                except Exception:
                    pass
                if time.time() > deadline:
                    raise RuntimeError("geckodriver did not become ready in 30s")
                time.sleep(0.5)
            caps = {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "firefox",
                        "moz:firefoxOptions": {
                            "binary": FIREFOX_BIN,
                            "args": ["-headless", "-profile", PROFILE_DIR],
                            "prefs": {
                                "browser.shell.checkDefaultBrowser": False,
                                "browser.startup.homepage": "about:blank",
                                "datareporting.policy.dataSubmissionEnabled": False,
                                "toolkit.telemetry.enabled": False,
                            },
                        },
                    }
                }
            }
            resp = self.wd("POST", "/session", caps, timeout=120)
            value = resp["value"]
            self.session_id = value["sessionId"]
            self.capabilities = value.get("capabilities", {})
            log(f"session started: {self.session_id} "
                f"firefox={self.capabilities.get('browserVersion')}")
            return self.info()

    def stop(self):
        with self.lock:
            if self.session_id:
                try:
                    self.wd("DELETE", f"/session/{self.session_id}", timeout=15)
                except Exception as e:
                    log(f"session delete failed (continuing): {e}")
                self.session_id = None
                self.capabilities = {}
            if self.proc:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                self.proc = None
            log("driver stopped")
            return self.info()

    def navigate(self, url):
        with self.lock:
            if not self.session_id:
                self.start()
            self.wd("POST", f"/session/{self.session_id}/url",
                    {"url": url}, timeout=120)
            return self.title()

    def title(self):
        with self.lock:
            if not self.session_id:
                return None
            return self.wd("GET", f"/session/{self.session_id}/title",
                           timeout=30).get("value")


DRIVER = Driver()
TOKEN = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log(fmt % args)

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        token = self.headers.get("X-Muse-Token", "")
        if TOKEN is None or not hmac.compare_digest(token, TOKEN):
            return self._send(401, {"ok": False,
                                    "error": "bad or missing token"})
        try:
            body = json.loads(raw.decode()) if raw else {}
        except Exception:
            return self._send(400, {"ok": False,
                                    "error": "request body is not valid JSON"})
        try:
            if self.path == "/status":
                return self._send(200, {"ok": True, **DRIVER.info()})
            if self.path == "/start":
                return self._send(200, {"ok": True, **DRIVER.start()})
            if self.path == "/stop":
                return self._send(200, {"ok": True, **DRIVER.stop()})
            if self.path == "/navigate":
                url = body.get("url")
                if not url:
                    return self._send(400, {"ok": False,
                                            "error": "missing 'url'"})
                title = DRIVER.navigate(url)
                return self._send(200, {"ok": True, "url": url,
                                        "title": title})
            if self.path == "/title":
                return self._send(200, {"ok": True,
                                        "title": DRIVER.title()})
            return self._send(404, {"ok": False,
                                    "error": f"unknown path {self.path}"})
        except Exception as e:
            return self._send(500, {"ok": False,
                                    "error": f"{type(e).__name__}: {e}"})


def main():
    global TOKEN
    with open(TOKEN_FILE) as f:
        TOKEN = f.read().strip()
    if not TOKEN:
        log("token file is empty; refusing to start")
        sys.exit(1)

    def on_term(signum, frame):
        log(f"signal {signum}; stopping driver and exiting")
        DRIVER.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    server = ThreadingHTTPServer((API_HOST, API_PORT), Handler)
    log(f"API listening on http://{API_HOST}:{API_PORT} "
        f"(localhost only, Sprint 1)")
    server.serve_forever()


if __name__ == "__main__":
    main()
