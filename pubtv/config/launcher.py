"""Loopback-only production launcher; requires PUBTV_DATA_DIR."""
import os
import sys
from pathlib import Path

def main():
    data_dir = os.environ.get("PUBTV_DATA_DIR")
    if not data_dir:
        raise SystemExit("Set PUBTV_DATA_DIR to a local application data directory before launching.")
    path = Path(data_dir).expanduser(); path.mkdir(parents=True, exist_ok=True)
    secret_file = path / "secret.key"
    if not secret_file.exists():
        secret_file.write_text(os.urandom(32).hex()); secret_file.chmod(0o600)
    os.environ.setdefault("PUBTV_SECRET_KEY", secret_file.read_text().strip())
    os.environ.setdefault("PUBTV_DEBUG", "0")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pubtv.config.settings")
    import django
    django.setup()
    from django.core.management import call_command
    call_command("migrate", interactive=False, verbosity=1)
    call_command("collectstatic", interactive=False, verbosity=0, clear=False)
    gunicorn = Path(sys.executable).with_name("gunicorn")
    if not gunicorn.exists():
        raise SystemExit(f"Gunicorn is not installed beside {sys.executable}.")
    argv = [str(gunicorn), "--bind", "127.0.0.1:8000", "--workers", "1", "pubtv.config.wsgi:application"]
    os.execv(str(gunicorn), argv)

if __name__ == "__main__": main()
