# Local runtime

The initial supported test target is macOS 26.x on Apple Silicon. Intel Mac compatibility is deferred for later validation and is not currently promised. The runtime and packaging approach must remain portable enough to evaluate that target separately; do not assume one universal executable.

This is a private, synthetic-data V1. Create an environment, install the project, then run:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements.lock
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Open `http://127.0.0.1:8000/`. The SQLite database is `pubtv.sqlite3` in this checkout for development; an operational launcher should set a local Application Support path before startup. Keep the server on loopback. Back up only after quitting the server, copying the complete data folder (including SQLite sidecars when present). No media bytes, credentials, connectors, playout control, or vendor exchange are included.

The tested environment is Python 3.11.15 with Django 5.2.17, Gunicorn 23.0.0, WhiteNoise 6.9.0, asgiref 3.12.1, packaging 26.3, and sqlparse 0.6.0. The focused validation suite contains 35 deterministic tests across `tests.test_launcher`, `tests.test_core`, and `tests.test_workflows`.

Run the complete focused suite with:

```sh
python manage.py test tests.test_launcher tests.test_core tests.test_workflows
```

For the loopback-only production-style launcher, install the pinned lockfile and set a local data directory explicitly (the application-support location is intentionally not fixed):

```sh
export PUBTV_DATA_DIR="/path/to/private/PUB-TV-data"
python -m pubtv.config.launcher
```

The launcher generates a 0600 secret in that directory and binds only to `127.0.0.1:8000`. `runserver` above is development-only.
