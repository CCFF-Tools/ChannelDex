"""Loopback-only launcher used by development and the bundled macOS app."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
import threading


def worker_command() -> list[str]:
    """Return the worker command for source and frozen packaged launches."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    return [sys.executable, "manage.py", "run_ultranexus_worker"]


def start_worker():
    """Start the singleton-supervised worker beside the local web server."""
    return subprocess.Popen(worker_command(), cwd=str(Path(__file__).resolve().parents[2]), close_fds=True)


def supervise_worker(application, host: str, port: int) -> None:
    """Run Gunicorn on the main thread; supervise its singleton worker beside it."""
    stopped = threading.Event()
    worker = start_worker()

    def monitor():
        nonlocal worker
        delay = 1.0
        while not stopped.wait(1.0):
            if worker.poll() is None:
                continue
            if stopped.wait(delay):
                return
            try:
                worker = start_worker()
            except OSError:
                pass
            delay = min(delay * 2, 60.0)

    watcher = threading.Thread(target=monitor, name="media-worker-supervisor", daemon=True)
    watcher.start()
    try:
        # Gunicorn installs signal handlers, which requires the main thread.
        run_gunicorn(application, host, port)
    finally:
        stopped.set()
        watcher.join(timeout=5)
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)


def default_data_dir() -> Path:
    """Return ChannelDex's data directory, retaining existing PUB-TV installs.

    The legacy directory is selected only when it already contains data; new
    installs use ChannelDex. PUBTV_DATA_DIR remains the compatibility override.
    """
    support = Path.home() / "Library" / "Application Support"
    legacy = support / "PUB-TV"
    current = support / "ChannelDex"
    if not current.exists() and (legacy / "pubtv.sqlite3").exists():
        return legacy
    return current


def prepare_data_dir() -> Path:
    path = Path(os.environ.get("PUBTV_DATA_DIR", default_data_dir())).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    os.environ.setdefault("PUBTV_DATA_DIR", str(path))
    secret_file = path / "secret.key"
    if not secret_file.exists():
        secret_file.write_text(os.urandom(32).hex(), encoding="utf-8")
        secret_file.chmod(0o600)
    os.environ.setdefault("PUBTV_SECRET_KEY", secret_file.read_text(encoding="utf-8").strip())
    return path


def _load_application():
    os.environ.setdefault("PUBTV_DEBUG", "0")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pubtv.config.settings")
    import django
    django.setup()
    from django.core.management import call_command
    call_command("migrate", interactive=False, verbosity=1)
    call_command("collectstatic", interactive=False, verbosity=0, clear=False)
    from pubtv.config.wsgi import application
    return application


def _open_browser_when_ready(host: str, port: int) -> threading.Thread:
    def open_browser():
        if wait_until_ready(host, port):
            webbrowser.open(f"http://{host}:{port}/")
    thread = threading.Thread(target=open_browser, daemon=True)
    thread.start()
    return thread


def run_gunicorn(application, host: str, port: int) -> None:
    """Run pinned Gunicorn in-process, avoiding a sibling executable/PATH lookup."""
    from gunicorn.app.base import BaseApplication

    class PubTVApplication(BaseApplication):
        def load_config(self):
            for key, value in {"bind": f"{host}:{port}", "workers": 1, "accesslog": "-", "errorlog": "-"}.items():
                self.cfg.set(key, value)

        def load(self):
            return application

    PubTVApplication().run()


def wait_until_ready(host: str, port: int, timeout: float = 15.0) -> bool:
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def main() -> None:
    if "--worker" in sys.argv[1:]:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pubtv.config.settings")
        import django
        django.setup()
        from django.core.management import call_command
        call_command("run_ultranexus_worker")
        return
    # Ensure all files created by Django/SQLite are private to the owner.
    os.umask(0o077)
    prepare_data_dir()
    application = _load_application()
    host = "127.0.0.1"
    port = int(os.environ.get("PUBTV_PORT", "8000"))
    os.environ.setdefault("PUBTV_ENABLE_QUIT", "1")
    os.environ["PUBTV_MASTER_PID"] = str(os.getpid())
    _open_browser_when_ready(host, port)
    supervise_worker(application, host, port)

if __name__ == "__main__": main()
