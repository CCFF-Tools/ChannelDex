# Build with: pyinstaller --noconfirm --clean packaging/pubtv.spec
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
datas = [
    (str(ROOT / "pubtv" / "templates"), "pubtv/templates"),
    (str(ROOT / "pubtv" / "static"), "pubtv/static"),
]
hiddenimports = (
    collect_submodules("pubtv.config")
    + collect_submodules("pubtv.operations")
    + collect_submodules("gunicorn")
    + collect_submodules("whitenoise")
)
analysis = Analysis(
    [str(ROOT / "packaging" / "macos_entry.py")],
    pathex=[str(ROOT)], binaries=[], datas=datas,
    hiddenimports=hiddenimports + ["pubtv.config.settings"], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="PUB-TV", console=False)
app = BUNDLE(
    COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="PUB-TV"),
    name="PUB-TV.app", icon=None,
    info_plist={
        "CFBundleDisplayName": "PUB-TV", "CFBundleIdentifier": "com.capitalcityfilmfest.pubtv",
        "CFBundleName": "PUB-TV", "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0", "LSMinimumSystemVersion": "26.0", "LSUIElement": False,
    },
)
