# muse-browser architecture

Decisions recorded for issue #1 (Sprint 1 -- Foundations). These are the rules
the code is built to. If reality forces a change, the change and the reason get
written here, not just made in code.

## Shape of the system

```
Muse --(token-authenticated HTTP)--> muse-browser daemon
     --(WebDriver HTTP)--> geckodriver --> the active track's engine
                                           (dedicated per-engine profile)
```

The daemon is the only piece we own. Everything below it is the engine
makers'.

## Engine tracks: three releases

Natalie's direction, 2026-11-25: "You are going to offer three releases.
Normal firefox, icecat, and firefox-esr." This supersedes the single-engine
(Firefox-only) note this document launched with, the same day.

| Track         | Engine          | Binary on the host              | Profile                          |
|---------------|-----------------|---------------------------------|----------------------------------|
| `firefox`     | Mozilla Firefox | `~/muse-browser/firefox/firefox`     | `~/muse-browser/profiles/firefox`     |
| `icecat`      | GNU IceCat      | `~/muse-browser/icecat/icecat`       | `~/muse-browser/profiles/icecat`      |
| `firefox-esr` | Mozilla Firefox ESR | `~/muse-browser/firefox-esr/firefox` | `~/muse-browser/profiles/firefox-esr` |

- **Default track: `icecat`.**
- Per-engine profiles are deliberate: engine families must not share a
  profile. The Sprint 1 profile was migrated to `profiles/firefox`.
- Selection is by config: `~/muse-browser/config.json` holds
  `{"track": "icecat"}` (the track map itself lives in the daemon and can
  be overridden from the same file). The choice takes effect on daemon
  (re)start -- there is no hot-switching mid-session.
- `POST /status` reports the active track name and the engine version
  (from the live session's capabilities when a session is up, otherwise
  from the binary's own `--version`).
- All three engines are Gecko, so one control path drives all of them:
  geckodriver + classic WebDriver, `browserName: "firefox"` in the
  capabilities (that is the protocol's name for the Gecko family;
  geckodriver reports the real `browserName` back, e.g. `"icecat"`).

### IceCat notes (the default track is the old engine)

- Installed version is **60.7.0** -- the newest release GNU publishes.
  Checked 2026-11-25: `ftp.gnu.org/gnu/gnuzilla/` tops out at 60.7.0, and
  the mirrors.kernel.org mirror agrees. There is no newer IceCat to take.
- Integrity: the 60.7.0 directory publishes only GPG `.sig` files, no
  checksum file. The tarball was therefore fetched from two independent
  GNU mirrors (ftp.gnu.org and mirrors.kernel.org) and the sha256 had to
  agree before unpacking. It did:
  `4f0dffe7ae5d98a63bd44b2b0dbd39f93d60056391214e8565892ab1ade36a85`.
- The 2019 build needs `libdbus-glib-1.so.2`, which current Arch no
  longer installs by default, and there is no sudo on the host. The
  library (from Arch's own dbus-glib 0.116-1 package) sits in
  `~/muse-browser/lib/`, user-space only, and the daemon puts that
  directory on `LD_LIBRARY_PATH` for the geckodriver child process.
- Its content-process sandbox predates this kernel and crashes the
  browser on launch ("A content process crashed and
  MOZ_CRASHREPORTER_SHUTDOWN is set, shutting down"). The sandbox is
  disabled for the icecat track's automation profile only
  (`security.sandbox.content.level: 0` +
  `MOZ_DISABLE_CONTENT_SANDBOX=1`, both in the daemon's track config).
  With that, IceCat passes the same 3-cycle smoke test as the other
  tracks.

## Engine control: geckodriver + classic WebDriver

- **Primary:** geckodriver driving the active engine through the classic
  WebDriver HTTP API (W3C WebDriver). It is the stable, documented control
  surface for Gecko, and it is enough for every verb in issue #3.
- **Future path:** WebDriver BiDi is where the standard is heading and
  geckodriver already exposes it; we note it and revisit after v1. Not used
  in Sprint 1.
- **Fallback note:** the Marionette protocol is what geckodriver itself
  speaks. We do not speak it directly unless geckodriver becomes a blocker.

## Daemon: Python 3, standard library only

- `http.server` for the API, `urllib` for geckodriver calls, `json` / `hmac` /
  `subprocess` for the rest. No pip dependencies: nothing to install, nothing
  extra to trust.
- One file: `daemon/muse_browserd.py`.

## Process model

- The daemon supervises exactly one geckodriver process and one WebDriver
  session, for the one active track.
- The engine runs with its **dedicated per-engine profile** (see the track
  table) -- never Natalie's daily-driver profile.
- **Headless by default.** Windowed mode is possible later through the
  server's Xvnc display (`:5`); it is not a Sprint 1 feature.
- The daemon runs as a systemd `--user` service
  (`systemd/muse-browser.service`). The server currently has linger disabled
  and enabling it needs sudo, so until that changes the service lives inside
  the user session; see issue #2 for the live status.

## Host state (never in the repo)

Everything stateful stays on the host under `~/muse-browser/`:

```
firefox/      Firefox tarball install        (track firefox)
icecat/       IceCat tarball install         (track icecat)
firefox-esr/  Firefox ESR tarball install    (track firefox-esr)
bin/          geckodriver
lib/          user-space libs an engine needs (libdbus-glib, for IceCat)
profiles/     one dedicated profile per track: firefox, icecat, firefox-esr
config.json   {"track": "icecat"} -- the active track choice
daemon/       muse_browserd.py, deployed copy
tests/        smoke test, deployed copy
logs/         geckodriver log, per-track smoke logs
token         API token, chmod 600, generated on the host
```

The token is sent as the `X-Muse-Token` header on every API call. Secrets
never enter the repo, the logs, or chat.

## API skeleton (Sprint 1)

Token-authenticated HTTP POSTs, JSON in and out:

- `POST /status` -- daemon and browser state, active track, engine version
- `POST /start` -- launch geckodriver + the track's engine, open a session on a blank page
- `POST /stop` -- end the session, stop geckodriver
- `POST /navigate` `{"url": ...}` -- minimal smoke path; returns the page title
- `POST /title` -- current page title

The full verb set (find, click, type, scroll, screenshot, tabs) is Sprint 2,
issue #3.

## Networking

- **Sprint 1:** the API binds to `127.0.0.1` on the server only. Muse reaches
  it over the established SSH route (Muse VM -> Natalie's PC -> server).
- **Sprint 2 (issue #4):** the bind moves to the server's Tailscale interface,
  same token auth. Never `0.0.0.0`, never the public internet.

## Installed baseline (Sprint 1, 2026-11-25)

- Mozilla Firefox **161.0** (official Mozilla tarball, linux64, en-US),
  installed at `~/muse-browser/firefox` -- track `firefox`
- GNU IceCat **60.7.0** (official GNU tarball, en-US, gnulinux-x86_64;
  sha256 cross-verified between two GNU mirrors, see above),
  installed at `~/muse-browser/icecat` -- track `icecat`, the default
- Mozilla Firefox ESR **153.8.0esr** (official Mozilla tarball, linux64,
  en-US), installed at `~/muse-browser/firefox-esr` -- track `firefox-esr`
- geckodriver **0.37.1** (Mozilla's official GitHub releases),
  at `~/muse-browser/bin/geckodriver`
- libdbus-glib **0.116-1** (Arch package, library files only, user-space),
  at `~/muse-browser/lib` -- for IceCat
- Python **3.14.10** (the server's system Python)
