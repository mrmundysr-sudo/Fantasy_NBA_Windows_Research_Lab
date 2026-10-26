# PyInstaller spec: one-folder Windows build of NBA Research Lab.  Build: pyinstaller packaging/nbalab.spec --noconfirm
import os, pathlib
from PyInstaller.utils.hooks import collect_submodules

HERE = pathlib.Path(SPECPATH).resolve()
ROOT = HERE.parent
# enumerate our own modules from disk (do not rely on importing the package at spec time)
mods = []
for p in (ROOT / "nbalab").rglob("*.py"):
    rel = p.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    mods.append(".".join(parts))
hidden = sorted(set(mods)) + collect_submodules("webview")

a = Analysis(
    [str(HERE / "launch.py")],
    pathex=[str(ROOT)],
    # NOTE: UI files go to "ui_data", not "nbalab/ui": a data folder named like the package can shadow it in the frozen app
    datas=[(str(ROOT / "nbalab" / "ui"), "ui_data")],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "pytest", "playwright"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NBALab", console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="NBALab")
