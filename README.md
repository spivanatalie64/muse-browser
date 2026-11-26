# muse-browser

> **Personal project -- built only for Natalie Cole-Clift. Not for distribution.**
> This exists because of Google Mobile Services (GMS) tracking: it is part of getting one person's phone off GMS. It is not a product, and it is not made for anyone else.
> The repository is left public, effectively as a template: if you want a starting point like this for your own setup, you are welcome to use it as one.

A small Gecko-based browser that Muse can drive over the internet -- through my Tailscale network, and nothing else.

## What this is

muse-browser is not a new browser engine. It is a thin control layer around Firefox (Gecko): a small daemon that owns a dedicated Firefox profile and exposes a token-authenticated control API. Muse connects to that API over my tailnet and can navigate, read pages, click, type, and take screenshots -- a real browser, on my machine, under my rules.

It is part of the same project as the rest of my de-Googling work: my tools, on my hardware, with no GMS in the loop.

## How it works

- The daemon runs on my local server (natalie-server) and supervises one dedicated Firefox profile -- never my daily-driver profile.
- The control API binds to the Tailscale interface only. Never the public internet, never 0.0.0.0.
- Every API call requires the token. The daemon never stores passwords.
- The profile persists, so signed-in sessions survive restarts.

## Status

Sprint 1 -- Foundations is in progress: the browser core is standing up on the local server. The architecture decisions are recorded in [docs/architecture.md](docs/architecture.md). The work is filed as issues, assigned, and scheduled into sprints (see Milestones). The host decision is made: the local server (issue #4).

## The plan

- **Sprint 1 -- Foundations:** architecture decisions (#1), browser core (#2).
- **Sprint 2 -- Control and connection:** the control API (#3), Tailscale-only networking (#4), the consent and safety model (#5).
- **Sprint 3 -- v1: driven over the tailnet:** runs as a service with a persistent profile (#6), then the end-to-end proof (#7) -- Muse opens a page, reads it, follows a link, and screenshots it back, all remotely.

## Rules it runs under

- Reading and navigating are free.
- Ask first: purchases, form submits with consequences, posting or sending anything as me, account changes.
- Credentials only through approved flows; the daemon stores none.
- Stop at account locks, rate limits, and refusals -- no routing around them.

## Why this is public

This repo is public on purpose -- not as a product launch, but as a template. My instance is mine: my server, my accounts, my rules. The code and the plan stay public so anyone who wants to build their own version of this can use it as a starting point.

## License

Apache License 2.0 -- see [LICENSE](LICENSE).
