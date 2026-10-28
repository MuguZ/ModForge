# ModForge Phase 2 implementation status

## Audit and base state

- [x] Inspect repository, configuration, source, migrations, UI, docs, tests, and Git state.
- [x] Phase 2 baseline at commit `5ec9d6e`: 37 tests passed; wheel built; Python 3.14.8/PySide6 6.12.
- [x] Compare remote before work: `main` matched `origin/main` at `5ec9d6e`.
- [x] Preserve Phase 2 commit `7c69b67`; it is local and still needs to be pushed.

## Domain, migrations, and profiles

- [x] Schema v3 game-scoped profiles/deployments and per-game mappings.
- [x] Schema v4 persistent deployed-file ownership (game, path, transaction, profile, mod IDs, fingerprint, status).
- [x] Schema v5 optional mod metadata columns and indexes.
- [x] Additive migrations preserve IDs, mod membership, game records, and deployment records; tests migrate a representative v1 database to v5.
- [x] Isolated default profiles, membership, active profile setting, profile comparison, and JSON import/export.
- [x] Game registration disable/re-enable preserves related data instead of cascading deletion.

## Import, mappings, and deployment

- [x] ZIP preflight preview without extraction; bounded paths, depth, count, expanded-size, compression ratio, links/special files, and file/directory conflicts are checked.
- [x] ZIP content is extracted into managed storage only after explicit import confirmation.
- [x] Persist per-game, per-file source-to-destination mappings with adapter path checks and collision rejection.
- [ ] Directory-prefix mapping rules, auto-suggestions, and adapter layout suggestions.
- [x] Validate managed source containment and reject symlink path components.
- [x] Profile apply rolls back old successful operations, applies the selected manifest, safely compensates recovered failures, and refuses externally modified files.
- [x] Per-file owner rows are committed/reconciled with deployment state and marked rolled back during recovery.
- [x] Preview includes previous managed files that will be restored/removed; unowned files remain preserved.
- [ ] One journaled transaction for the entire profile transition; current flow uses durable sub-transactions and recovery material.

## Library, UI, and Linux

- [x] Edit optional mod name/version/author/description/source URL/category/tags/notes; show archive checksum and file manifest.
- [x] Display imported/enabled/deployed/missing mod state; dashboard reports enabled and deployed counts.
- [x] Export/import and compare per-game profile definitions; import validates local archive checksums and does not accept paths.
- [x] Backups page can invoke safe transaction rollback.
- [x] Steam discovery and XDG path support from Phase 1 remain covered; generic file deployment and Cyberpunk root validation are distinct.
- [ ] Local mod version update/candidate workflow and safe library uninstall.
- [ ] Per-file restore UI, backup retention/usage reporting, profile restore point, dependency/incompatibility metadata.
- [ ] Launcher execution, Proton prefix detection, Lutris/Heroic, network providers, symlink/hardlink strategies, and specialized Far Cry 3/Sekiro adapters.
- [ ] Full storage relocation workflow, diagnostics export, first-run wizard, and runtime load-order support.

## QA, documentation, release

- [x] Add migration, mapping, owner lifecycle/recovery, profile transition, archive security, UI preview, and cross-game tests.
- [x] Add CI for Python 3.11–3.14, offscreen pytest, and wheel build.
- [x] Update README and adapter documentation with verified behavior and limitations.
- [x] Final suite after continuation edits: 54 tests pass; `compileall` passes.
- [x] Build wheel with `pip wheel . --no-deps --no-build-isolation`; wheel created successfully.
- [x] Offscreen launch smoke reaches the Qt event loop with isolated `/tmp` XDG directories (timeout exit 124 is expected for a running GUI).
- [x] `git diff --check` passes; tests use disposable roots and smoke data stayed under `/tmp`.
- [ ] Formatting/lint/type checks: no formatter, linter, or type checker is configured or installed in the development environment.
- [ ] Stage and review release files; fetch remote; commit continuation work and push both commits to `main`.

All deployment tests use temporary game roots. Do not target installed games, Proton prefixes, user libraries, archives, or existing backups.
