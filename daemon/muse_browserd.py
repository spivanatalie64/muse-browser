#!/usr/bin/env python3
"""
muse-browser daemon -- Sprint 1 browser core, three engine tracks.

A small token-authenticated HTTP API (Python standard library only) that
supervises one geckodriver + one Gecko browser instance using a dedicated
per-engine profile.

Engine tracks (Natalie's direction, 2026-11-25): the daemon offers three
Gecko releases -- "firefox" (Mozilla Firefox), "icecat" (GNU IceCat, the
DEFAULT track), and "firefox-esr" (Mozilla Firefox ESR). The active track
is chosen by config and takes effect on daemon (re)start; /status reports
the active track name and the engine version.

Sprint 1 scope (issues #1, #2): /status, /start, /stop, plus a minimal
/navigate + /title smoke path that proves the WebDriver session end to end.
The full verb set is Sprint 2 (issue #3). The API binds to localhost only;
the Tailscale bind is Sprint 2 (issue #4). Decisions: docs/architecture.md.

Host layout (nothing stateful lives in the repo):
    ~/muse-browser/firefox/      Firefox tarball install     (track firefox)
    ~/muse-browser/icecat/       IceCat tarball install      (track icecat)
    ~/muse-browser/firefox-esr/  Firefox ESR tarball install (track firefox-esr)
    ~/muse-browser/bin/          geckodriver
    ~/muse-browser/lib/          user-space libs an engine needs and the
                                 distro no longer ships (libdbus-glib for
                                 IceCat 60.7.0); put on LD_LIBRARY_PATH for
                                 the geckodriver child process
    ~/muse-browser/profiles/     one dedicated profile per track:
                                 profiles/firefox, profiles/icecat,
                                 profiles/firefox-esr (engines never share)
    ~/muse-browser/config.json   {"track": "icecat"} -- active track choice
    ~/muse-browser/daemon/       this file, deployed
    ~/muse-browser/token         API token, chmod 600, generated on the host
    ~/muse-browser/logs/         geckodriver log
"""

import hmac
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.join(os.path.expanduser("~"), "muse-browser")
TOKEN_FILE = os.path.join(BASE, "token")
CONFIG_FILE = os.path.join(BASE, "config.json")
GECKODRIVER_BIN = os.path.join(BASE, "bin", "geckodriver")
LIB_DIR = os.path.join(BASE, "lib")
GD_LOG = os.path.join(BASE, "logs", "geckodriver.log")

API_HOST = "127.0.0.1"
API_PORT = 8765
GD_HOST = "127.0.0.1"
GD_PORT = 4444

DEFAULT_TRACK = "icecat"
TRACKS = {
    "firefox": {
        "binary": os.path.join(BASE, "firefox", "firefox"),
        "profile": os.path.join(BASE, "profiles", "firefox"),
    },
    "icecat": {
        "binary": os.path.join(BASE, "icecat", "icecat"),
        "profile": os.path.join(BASE, "profiles", "icecat"),
        # IceCat 60.7.0's content-process sandbox predates this kernel:
        # its content process crashes on launch ("A content process
        # crashed and MOZ_CRASHREPORTER_SHUTDOWN is set"), which kills
        # the browser before WebDriver can use it. The sandbox is
        # disabled for this track's automation profile only.
        "prefs": {"security.sandbox.content.level": 0},
        "env": {"MOZ_DISABLE_CONTENT_SANDBOX": "1"},
    },
    "firefox-esr": {
        "binary": os.path.join(BASE, "firefox-esr", "firefox"),
        "profile": os.path.join(BASE, "profiles", "firefox-esr"),
    },
}


def log(msg):
    print(f"[muse-browserd] {msg}", flush=True)


def load_config():
    """Active track + track map. Built-in defaults, overridable from
    ~/muse-browser/config.json: {"track": "icecat", "tracks": {...}}."""
    cfg = {"track": DEFAULT_TRACK,
           "tracks": {name: dict(t) for name, t in TRACKS.items()}}
    try:
        with open(CONFIG_FILE) as f:
            raw = json.load(f)
        if isinstance(raw.get("track"), str):
            cfg["track"] = raw["track"]
        for name, over in (raw.get("tracks") or {}).items():
            if name in cfg["tracks"] and isinstance(over, dict):
                for key in ("binary", "profile"):
                    if isinstance(over.get(key), str):
                        cfg["tracks"][name][key] = over[key]
                for key in ("prefs", "env"):
                    if isinstance(over.get(key), dict):
                        merged = dict(cfg["tracks"][name].get(key) or {})
                        merged.update(over[key])
                        cfg["tracks"][name][key] = merged
        log(f"config loaded from {CONFIG_FILE}: track={cfg['track']}")
    except FileNotFoundError:
        log(f"no config file; default track={cfg['track']}")
    except Exception as e:
        log(f"config file unreadable ({e}); default track={cfg['track']}")
    if cfg["track"] not in cfg["tracks"]:
        log(f"unknown track {cfg['track']!r}; falling back to {DEFAULT_TRACK}")
        cfg["track"] = DEFAULT_TRACK
    return cfg


CONFIG = load_config()
TRACK_NAME = CONFIG["track"]
TRACK = CONFIG["tracks"][TRACK_NAME]


def child_env():
    """Environment for the geckodriver child (the browser inherits it).
    ~/muse-browser/lib carries user-space copies of libraries an engine
    needs that the distro no longer ships (libdbus-glib for IceCat)."""
    env = dict(os.environ)
    if os.path.isdir(LIB_DIR):
        prev = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = LIB_DIR + (os.pathsep + prev if prev else "")
    return env


class Driver:
    """Owns the geckodriver subprocess and the one WebDriver session."""

    def __init__(self):
        self.proc = None
        self.session_id = None
        self.capabilities = {}
        self._binary_version = None
        self.lock = threading.RLock()

    def wd(self, method, path, body=None, timeout=60):
        url = f"http://{GD_HOST}:{GD_PORT}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=utf-8")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def binary_version(self):
        """Version string of the track's binary itself (`--version`),
        used when no live session is reporting one."""
        if self._binary_version is None:
            try:
                out = subprocess.run([TRACK["binary"], "--version"],
                                     capture_output=True, text=True,
                                     timeout=30, env=child_env())
                m = re.search(r"(\d+(?:\.\d+)+(?:esr)?)", out.stdout.strip())
                self._binary_version = m.group(1) if m else None
            except Exception as e:
                log(f"binary --version failed: {e}")
                self._binary_version = None
        return self._binary_version

    def info(self):
        version = self.capabilities.get("browserVersion") \
            or self.binary_version()
        return {
            "track": TRACK_NAME,
            "browser_running": self.session_id is not None,
            "engine_version": version,
            # Sprint 1 field name, kept: the engine version of the
            # active track, whatever Gecko release it is.
            "firefox_version": version,
            "session_id": self.session_id,
        }

    def start(self):
        with self.lock:
            if self.session_id:
                return self.info()
            os.makedirs(TRACK["profile"], exist_ok=True)
            os.makedirs(os.path.dirname(GD_LOG), exist_ok=True)
            logfile = open(GD_LOG, "ab")
            self.proc = subprocess.Popen(
                [GECKODRIVER_BIN, "--host", GD_HOST, "--port", str(GD_PORT)],
                stdout=logfile, stderr=logfile, env=child_env())
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
            options = {
                "binary": TRACK["binary"],
                "args": ["-headless", "-profile", TRACK["profile"]],
                "prefs": {
                    "browser.shell.checkDefaultBrowser": False,
                    "browser.startup.homepage": "about:blank",
                    "datareporting.policy.dataSubmissionEnabled": False,
                    "toolkit.telemetry.enabled": False,
                },
            }
            options["prefs"].update(TRACK.get("prefs") or {})
            if TRACK.get("env"):
                options["env"] = dict(TRACK["env"])
            caps = {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "firefox",
                        "moz:firefoxOptions": options,
                    }
                }
            }
            resp = self.wd("POST", "/session", caps, timeout=120)
            value = resp["value"]
            self.session_id = value["sessionId"]
            self.capabilities = value.get("capabilities", {})
            log(f"session started: {self.session_id} "
                f"track={TRACK_NAME} "
                f"engine={self.capabilities.get('browserVersion')}")
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
        token = self.headers.get("X-Muse-Token")
        if TOKEN is None or not token or not hmac.compare_digest(token, TOKEN):
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
        f"(localhost only, Sprint 1) track={TRACK_NAME}")
    server.serve_forever()


if __name__ == "__main__":
    main()
