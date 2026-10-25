# Fantasy NBA Windows Research Lab

Private, Windows-only NBA statistical research and fantasy-prediction workstation (research prototype).
Read `PROJECT_VISION.md`, `RESEARCH_DIRECTIVE.md` and `PUBLIC_RELEASE_REVIEW.md` for intent and constraints, `STATUS.md` for the candid state of the build, and `INSTALL.md` to run it.

**All bundled data is SYNTHETIC.** The app labels this everywhere; its picks are demonstrations of the machinery, not real recommendations.

```
python -m nbalab            # desktop window (needs pywebview + WebView2) or browser fallback
python -m unittest discover -s tests
```
Layout: `nbalab/` engine, models, providers, server and `ui/`; `tests/`; `tools/ui_smoke.py`; `packaging/` (PyInstaller spec, Inno Setup); `.github/workflows/windows-build.yml`.
