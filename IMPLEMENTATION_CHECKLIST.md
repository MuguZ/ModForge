# ModForge Phase 2 checklist

## Audit and baseline

- [x] Verify branch, remote, working tree, package configuration, source, tests, and docs.
- [x] Confirm post-Phase-1 baseline: 37 pytest tests passed and wheel built; local branch was `main` at `5ec9d6e`.
- [x] Fetch remote references before commit/push. Escalated fetch succeeded; local `main` and `origin/main` matched before this phase's commit.
- [x] Confirm schema v2 and identify global profile selection, unassociated deployment rows, and archive paths deploying without a mapping layer.

## Implemented in this phase

- [x] Add schema v3 game associations for profiles/deployments and a per-game mod destination mapping table.
- [x] Preserve v1/v2 data through additive migration; associate sole-game legacy records and clone profile memberships when multiple installations exist.
- [x] Give every game a separate default profile and isolate profile listing, enabled states, and active-profile settings by game.
- [x] Persist source-to-destination mappings and validate relative paths/collisions through the selected adapter.
- [x] Preview ZIP contents before extraction/import; add archive nesting limits and file/directory conflict checks.
- [x] Reject managed source paths with symlink components before they reach deployment.
- [x] Scope deployment collisions/history by game installation; bind transactions to game IDs and recorded roots.
- [x] Add profile application with reverse-order rollback, deployment, compensation on safely recovered failures, and recovery blocking for external file changes.
- [x] Show prior-profile removals/restores in deployment preview; preserve external edits and unrelated files.
- [x] Make game registration removal reversible by disabling it, preserving profiles, mappings, transactions, backups, journals, and game files.
- [x] Add regression tests for migrations, mappings, per-game profiles/deployments, profile transition/disable, failure compensation, external modifications, and UI preview.
- [x] Add CI jobs for Python 3.11–3.14, offscreen pytest, and wheel build.
- [x] Update usage, adapter, security, and limitation documentation.

## Verification and release

- [x] Run full tests, compilation, wheel build, editable install, startup smoke, and `git diff --check` after final changes.
- [x] Review the source/test/docs/workflow file set; generated artifacts, credentials, and game files are excluded.
- [ ] Commit and push to `main` only after remote state is verified.

## Remaining limitations

- [ ] Mod library metadata/version editing, update workflow, profile import/export/comparison, and standalone backup restore UI.
- [ ] Explicit file ownership table and per-file owner attribution across concurrently active mods.
- [ ] Single journaled transaction spanning profile rollback plus re-deployment; current transition has durable sub-transactions and a recovery path.
- [ ] Symlink/hardlink/adapter deployment strategies, launcher execution, Proton prefix awareness, Lutris/Heroic, dependencies, online providers, and specialized Far Cry 3/Sekiro loaders.
- [ ] Full import wizard with directory-level mapping rules and adapter-generated suggestions; current UI previews files and supports per-file mappings.
- [ ] Formatter, linter, and type checker; the repository did not previously configure these tools.

Tests use temporary game roots and fixture archives/manifests. Never point automated tests at installed games, Proton prefixes, personal mod libraries, or existing backups.
