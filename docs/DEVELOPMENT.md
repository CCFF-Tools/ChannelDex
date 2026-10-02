# ChannelDex developer guide

This document is for contributors working on the ChannelDex source, runtime, data
contracts, tests, and packaging. The root [README](../README.md) is the end-user
guide.

## Project status and boundaries

ChannelDex is a local-first scheduling and logging app for one owner/editor on one
computer. The product plan, data model, architecture, decisions, and Python +
Django + SQLite stack were approved for implementation on 2026-09-09. This
repository contains an implemented V1 scaffold and an unsigned, locally
smoke-tested Apple Silicon macOS `.app` packaging path.

The accepted initial runtime, packaging, and testing target is macOS 26.x on Apple
Silicon on the development machine. Intel Mac support, a universal binary, signing,
notarization, installation restrictions, and final distribution remain open or
deferred.

The application must remain local-only by default. It does not require external
hosting, a cloud or CDN runtime, vendor APIs, an AI runtime, media downloads,
transcoding, schedule exchange, or broadcast control. PUB-TV is the first station
boundary; GOV-TV is later scope.

## Source setup

The tested development environment uses Python 3.11. Create an isolated environment
and install the project plus its pinned dependencies:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements.lock
python -m pip install -r packaging/requirements.lock
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. Keep development servers bound to loopback. The
development database defaults to `pubtv.sqlite3` in the repository checkout.

The pinned and tested set is Python 3.11.15, Django 5.2.17, Gunicorn 23.0.0,
WhiteNoise 6.9.0, asgiref 3.12.1, packaging 26.3, and sqlparse 0.6.0.

## Validation

Run the complete focused suite with:

```sh
python manage.py test tests.test_launcher tests.test_quit tests.test_core tests.test_workflows tests.test_todo_workflows
```

The tests cover launcher and quit behavior, core scheduling contracts, operational
workflows, and usability-backlog features. When changing a contract, update both
its focused tests and the corresponding source-of-truth document.

## Production-style local launcher

The loopback-only launcher uses Gunicorn and defaults to the private Application
Support directory at `~/Library/Application Support/ChannelDex`. For upgrade
compatibility, it continues using `~/Library/Application Support/PUB-TV` when
the new directory does not exist and the legacy directory contains
`pubtv.sqlite3`. Override the location for isolated smoke tests when needed:

```sh
export PUBTV_DATA_DIR="/path/to/private/PUB-TV-data"
python -m pubtv.config.launcher
```

`PUBTV_PORT` overrides port 8000. The launcher creates its data directory with
mode `0700`, creates its secret with mode `0600`, runs migrations, collects static
files, binds one Gunicorn worker to `127.0.0.1`, and opens the default browser after
readiness. The visible, CSRF-protected **Quit ChannelDex** control terminates the
launcher. Closing the browser does not.

## macOS packaging

Build the unsigned application for the current machine's architecture with:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements.lock
python -m pip install -r packaging/requirements.lock
packaging/build_macos.sh
open packaging/dist/ChannelDex.app
```

The output is `packaging/dist/ChannelDex.app`. It bundles Python, Django, Gunicorn,
templates, static files, and migrations; an activated virtual environment is not
required at runtime. The build targets the current CPU architecture and performs
no signing or notarization.

## Repository map

| Path | Purpose |
| --- | --- |
| `pubtv/config/` | Django settings, URL routing, WSGI, static-file setup, and the local launcher |
| `pubtv/operations/` | Domain models, forms, services, views, and schema migrations |
| `pubtv/templates/` | Server-rendered end-user interface |
| `pubtv/static/` | Local application styling |
| `tests/` | Contract, workflow, launcher, quit, and UX tests |
| `packaging/` | PyInstaller entry point, specification, lockfile, and macOS build script |
| `docs/` | Accepted/proposed product contracts, architecture, decisions, and backlog |

## Documentation sources of truth

- [Product plan](PRODUCT_PLAN.md): scope, workflows, rules, and acceptance scenarios.
- [Data model](DATA_MODEL.md): entities, relationships, state, and deferred choices.
- [Architecture](ARCHITECTURE.md): runtime shape, boundaries, storage, and delivery.
- [Decisions](DECISIONS.md): accepted decisions, open questions, and approval record.
- [TODO and usability backlog](TODO.md): accepted follow-up requirements and open UX work.
- [Runtime notes](../README_RUNTIME.md): concise historical runtime and packaging commands.

Documentation must distinguish **accepted**, **proposed**, and **open** statements.
Do not describe a roadmap capability as implemented. Preserve planned schedules,
uploaded schedule evidence, attributed reports, and log-verified airings as separate
concepts in code and documentation.

## Data and operational safety

- Keep the authoritative SQLite database on local Mac storage, not an SMB or synced
  folder. SMB paths are references to media, not application storage.
- Store no media bytes, external-service credentials, or connector sessions.
- Stop the application before copying the complete data directory for backup.
- Keep future imports additive, auditable, idempotent, and previewed before commit.
- Preserve source, actor, revision, and correction provenance instead of overwriting
  confirmed history.
- Keep ordinary runtime traffic on loopback. Accounts, LAN access, and public access
  require an explicit later design decision.

## Previous root README

The following is the complete content of the root `README.md` immediately before
the end-user/developer documentation split. It is preserved verbatim for project
history.

---

# ChannelDex

ChannelDex is a local-first scheduling and logging app for one owner/editor on one computer. The product plan, data model, architecture, decisions, and Python + Django + SQLite stack were approved for implementation on 2026-09-09. This repository now contains an implemented V1 scaffold and an unsigned, locally smoke-tested Apple Silicon macOS `.app` packaging path; signing, notarization, distribution, and Intel validation remain separate follow-up work.

The accepted initial runtime, packaging, and testing target is macOS 26.x on Apple Silicon on the development machine. Intel Mac support is a later compatibility-validation target and is not yet proven; no single universal binary is promised. Installation restrictions and the final distribution form remain open.

The first station boundary is PUB-TV. Scheduling, catalog, and broadcast history remain core; ChannelDex plans the complete 24-hour day, including programs, filler, station IDs, PSAs, and live events. Reusable non-episode assets and timed live entries are first-class schedule items. Preserve confirmed history with source and actor provenance. Qualified UltraNEXUS media encoding, validation, and FTP transfer are available through Approval 1. Restricted NMG/BIN generation and attended XPASS/LOADSCH delivery are implemented behind Approval 2 and destination-specific qualification gates. The design does not require vendor APIs, external hosting, cloud or CDN runtime services, or an AI runtime.

Replays use the episode from the most recent premiere until the next premiere. This premiere-to-premiere cycle is independent of the Monday calendar boundary: a Monday replay before a Wednesday premiere still uses the prior cycle's episode. The app suggests the next queued episode and configured premiere, and the owner confirms the plan or chooses an older episode case by case by default. An off-by-default station setting may carry the most recent explicitly planned episode into otherwise unassigned current/future weeks; generated cycles are visibly labeled and audited, yield to explicit plans, stop after No program, and never advance the pending queue. Keep a complete planned day distinct from evidence that it actually aired. Calendar views group weeks from Monday at 00:00 Eastern on a macOS host. Private in-app day/week/agenda views are V1; export is a near-term roadmap follow-up. Public access and shared LAN/accounts are deferred.

Planning documents:

- [Product plan](PRODUCT_PLAN.md)
- [Data model](DATA_MODEL.md)
- [Architecture](ARCHITECTURE.md)
- [Decisions](DECISIONS.md)
- [TODO and usability backlog](TODO.md)

The implemented scaffold covers local Django/SQLite records, server-rendered planning views, recurrence and weekly assignment rules, preparation/history provenance, conflict warnings, stale-edit checks, and local macOS packaging. It does not claim vendor exchange, device automation, public/LAN access, or production distribution. Proposed, accepted, and open statements remain clearly distinguished in project documentation.
