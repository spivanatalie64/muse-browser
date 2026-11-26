# muse-browser architecture

Decisions recorded for issue #1 (Sprint 1 -- Foundations). These are the rules
the code is built to. If reality forces a change, the change and the reason get
written here, not just made in code.

## Shape of the system

```
Muse --(token-authenticated HTTP)--> muse-browser daemon
     --(WebDriver HTTP)--> geckodriver --> Firefox (dedicated profile)
```

The daemon is the only piece we own. Everything below it is Mozilla's.

## Engine control: geckodriver + classic WebDriver

- **Primary:** geckodriver driving Firefox through the classic WebDriver HTTP
  API (W3C WebDriver). It is the stable, documented control surface for Gecko,
  and it is enough for every verb in issue #3.
- **Future path:** WebDriver BiDi is where the standard is heading and
  geckodriver already exposes it; we note it and revisit after v1. Not used in
  Sprint 1.
- **Fallback note:** Firefox's Marionette protocol is what geckodriver itself
  speaks. We do not speak it directly unless geckodriver becomes a blocker.

## Daemon: Python 3, standard library only

- `http.server` for the API, `urllib` for geckodriver calls, `json` / `hmac` /
  `subprocess` for the rest. No pip dependencies: nothing to install, nothing
  extra to trust.
- One file: `daemon/muse_browserd.py`.

## Process model

- The daemon supervises exactly one geckodriver process and one WebDriver
  session.
- Firefox runs with a **dedicated profile** at `~/muse-browser/profile` --
  never Natalie's daily-driver profile.
- **Headless by default.** Windowed mode is possible later through the
  server's Xvnc display (`:5`); it is not a Sprint 1 feature.
- The daemon runs as a systemd `--user` service
  (`systemd/muse-browser.service`). The server currently has linger disabled
  and enabling it needs sudo, so until that changes the service lives inside
  the user session; see issue #2 for the live status.

## Host state (never in the repo)

Everything stateful stays on the host under `~/muse-browser/`:

```
firefox/    Firefox tarball install
bin/        geckodriver
profile/    dedicated Firefox profile
daemon/     muse_browserd.py, deployed copy
tests/      smoke test, deployed copy
logs/       geckodriver log
token       API token, chmod 600, generated on the host
```

The token is sent as the `X-Muse-Token` header on every API call. Secrets
never enter the repo, the logs, or chat.

## API skeleton (Sprint 1)

Token-authenticated HTTP POSTs, JSON in and out:

- `POST /status` -- daemon and browser state, Firefox version
- `POST /start` -- launch geckodriver + Firefox, open a session on a blank page
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
  installed at `~/muse-browser/firefox`
- geckodriver **0.37.1** (Mozilla's official GitHub releases),
  at `~/muse-browser/bin/geckodriver`
- Python **3.14.10** (the server's system Python)
