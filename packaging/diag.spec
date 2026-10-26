# One-file, windowed, no admin needed:  pyinstaller packaging/diag.spec --noconfirm   ->  dist/NBA-Source-Diagnostics.exe
import pathlib
from PyInstaller.utils.hooks import collect_submodules
HERE = pathlib.Path(SPECPATH).resolve()
ROOT = HERE.parent
hidden = ["nbalab.diag_app", "nbalab.diag_page", "nbalab.diagnostics", "nbalab.store", "nbalab.schema"] + collect_submodules("nbalab.sources") + collect_submodules("pypdf")
a = Analysis([str(HERE / "diag_launch.py")], pathex=[str(ROOT)], hiddenimports=hidden,
             excludes=["numpy", "tkinter", "matplotlib", "scipy", "pandas", "pytest", "playwright", "webview"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="NBA-Source-Diagnostics", console=False, upx=False, disable_windowed_traceback=False)
