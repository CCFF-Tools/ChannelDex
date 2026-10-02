#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
PYINSTALLER=${PYINSTALLER:-.venv/bin/pyinstaller}
if [ ! -x "$PYINSTALLER" ]; then
  echo "PyInstaller not found at $PYINSTALLER; install packaging/requirements.lock first." >&2
  exit 1
fi
PYTHON=${PYTHON:-$(dirname "$PYINSTALLER")/python}
if [ ! -x "$PYTHON" ]; then
  echo "Python interpreter not found at $PYTHON; set PYTHON explicitly." >&2
  exit 1
fi
test -f "$ROOT/requirements.lock"
test -f "$ROOT/packaging/requirements.lock"
"$PYTHON" -m pip check
"$PYTHON" -c 'import importlib.metadata as m; from pathlib import Path; files=(Path("requirements.lock"), Path("packaging/requirements.lock")); pairs=[line.split("==", 1) for f in files for line in f.read_text().splitlines() if "==" in line and not line.startswith("#")]; missing=[f"{n}=={v}" for n,v in pairs if m.version(n) != v]; sys=__import__("sys"); sys.exit("Unmatched lock packages: " + ", ".join(missing)) if missing else print("runtime/build lock versions OK")'
clean_generated_dir() {
  target=$1
  stale="${target}.stale.$$"
  if [ -e "$target" ]; then
    mv "$target" "$stale"
  fi
  mkdir -p "$target"
  # Finder can recreate metadata inside an app bundle while it is being
  # removed. The active output path is already clean, so stale cleanup must
  # not abort an otherwise valid build.
  rm -rf "$stale" || echo "Warning: could not fully remove generated $stale" >&2
}

# Move old output out of the active path before deleting it. This avoids
# failures when Finder (or another process) recreates .DS_Store mid-delete.
clean_generated_dir packaging/build
clean_generated_dir packaging/dist
export PYINSTALLER_CONFIG_DIR="$ROOT/packaging/build/pyinstaller-config"
"$PYINSTALLER" --noconfirm --clean --workpath packaging/build --distpath packaging/dist packaging/pubtv.spec
echo "Built $ROOT/packaging/dist/ChannelDex.app"
