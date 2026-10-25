# PyInstaller spec: one-folder Windows build of NBA Research Lab.  Build: pyinstaller packaging/nbalab.spec --noconfirm
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("nbalab") + collect_submodules("webview")

a = Analysis(
    ["../nbalab/__main__.py"],
    pathex=[".."],
    datas=[("../nbalab/ui", "nbalab/ui")],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "pytest", "playwright"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NBALab", console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="NBALab")
