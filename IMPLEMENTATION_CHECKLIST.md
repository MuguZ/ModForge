# Universal game support implementation checklist

- [x] Audit repository, Git state, dependencies, current architecture, and baseline test availability.
- [x] Add a versioned, data-preserving universal game registry and generic adapter.
- [x] Add manual game registration, validation, editing, removal, and game list UI.
- [x] Add read-only Steam library discovery with support for multiple libraries.
- [x] Generalize active-game deployment paths while retaining Cyberpunk validation in its adapter.
- [x] Update README and developer documentation to describe universal support honestly.
- [x] Add isolated tests for game registry, migrations, Steam parsing, and generic deployment.
- [x] Run tests and available quality/package checks; review diff.
- [x] Commit and push to the configured GitHub remote; Git confirmed the update to `main`.

Audit note: baseline `pytest -q` could not start because pytest was not installed. Python 3.14.8 and PySide6 6.12.0 are available. The post-change suite passes in the isolated `.venv`. `git fetch origin` succeeded and `main` matched `origin/main` before this work.
