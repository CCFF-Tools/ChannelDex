# ChannelDex local runtime

ChannelDex is owned, developed, and published by Capital Media Systems. The
existing `pubtv` Python package, `PUBTV_*` environment variables, and
`Application Support/PUB-TV` data location are compatibility identifiers.

The initial supported test target is macOS 26.x on Apple Silicon. Intel Mac compatibility is deferred for later validation and is not currently promised. The runtime and packaging approach must remain portable enough to evaluate that target separately; do not assume one universal executable.

An unsigned local macOS app can be built on the current machine with PyInstaller 6.22.2 (the build targets the current CPU architecture; it is not a universal binary):

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements.lock
python -m pip install -r packaging/requirements.lock
packaging/build_macos.sh
open packaging/dist/ChannelDex.app
```

The expected output is `packaging/dist/ChannelDex.app`. The app bundles Python,
Django, templates, static files, migrations, and Gunicorn; it does not require an
activated virtual environment or external Gunicorn executable. New installs use
`~/Library/Application Support/ChannelDex`. To preserve an existing installation,
the launcher continues using `~/Library/Application Support/PUB-TV` when the new
directory does not exist and the legacy directory contains `pubtv.sqlite3`.
`PUBTV_DATA_DIR` remains the explicit location override, and `PUBTV_PORT` can
override port 8000 for smoke tests. The app migrates, collects static files, starts
one Gunicorn worker bound only to `127.0.0.1`, waits for readiness in a helper
thread, and opens the default browser. Gunicorn owns normal SIGTERM/quit worker
shutdown. No signing or notarization is performed.

The browser's close button does not stop ChannelDex. Use the visible CSRF-protected **Quit ChannelDex** button, or terminate the app from the Dock/Activity Monitor. Runtime data and SQLite sidecars are created with owner-only permissions.

This is a private, synthetic-data V1. Create an environment, install the project, then run:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements.lock
python -m pip install -r packaging/requirements.lock
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Open `http://127.0.0.1:8000/`. The SQLite database is `pubtv.sqlite3` in this checkout for development; an operational launcher should set a local Application Support path before startup. Keep the server on loopback. Back up only after quitting the server, copying the complete data folder (including SQLite sidecars when present). No media bytes, credentials, connectors, playout control, or vendor exchange are included.

The tested environment is Python 3.11.15 with Django 5.2.17, Gunicorn 23.0.0, WhiteNoise 6.9.0, asgiref 3.12.1, packaging 26.3, and sqlparse 0.6.0. The focused validation suite contains 42 deterministic tests across `tests.test_launcher`, `tests.test_quit`, `tests.test_core`, and `tests.test_workflows`.

Run the complete focused suite with:

```sh
python manage.py test tests.test_launcher tests.test_quit tests.test_core tests.test_workflows
```

For the loopback-only production-style launcher, install the pinned runtime lockfile. It defaults to the private Application Support directory; set a local data directory explicitly when desired:

```sh
export PUBTV_DATA_DIR="/path/to/private/PUB-TV-data"
python -m pubtv.config.launcher
```

The launcher generates a 0600 secret in a 0700 directory and binds only to `127.0.0.1:8000`. `runserver` above is development-only.
