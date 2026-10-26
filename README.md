# ModForge

ModForge is a Linux-first, local Cyberpunk 2077 mod library and conservative file deployment tool. The first release supports user-supplied ZIP archives and ordinary file-based mods. It does not download mods or run their contents.

## Install and launch on CachyOS

Install Python and Qt runtime support from the distribution, then create an isolated environment:

```sh
cd ~/ModForge
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
modforge
```

Alternatively launch with `python -m modforge`. This app does not need root access. For a Wayland session, use the system Qt platform plugin; Qt will select the current session automatically.

## Usage

1. In **Settings**, choose the Cyberpunk 2077 installation root. For Steam, this is commonly `~/.local/share/Steam/steamapps/common/Cyberpunk 2077`; Flatpak Steam commonly uses `~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Cyberpunk 2077`. Proton does not change the Linux game root. Select the directory that directly contains `bin/x64`.
2. Import a ZIP in **Mod Library**. ModForge retains the archive and extracts it after checking paths, entry types, file counts, sizes, and compression ratios. Double-click a row to inspect the paths.
3. Select or create a profile and enable mods in **Profiles**. Profile edits do not change game files.
4. Deploy from **Dashboard** only after checking the preview. ZIP paths are preserved exactly: the archive should contain intended paths such as `archive/pc/mod/example.archive` or `r6/scripts/example.lua`. ModForge never guesses or flattens destinations. Duplicate destination paths block deployment.
5. Use **Deployments** to review history and roll back an eligible deployment.

Framework instructions, REDmod deployment, dependency installation, and configuration edits remain manual. ModForge cannot establish compatibility for every mod.

## Data locations

- `~/.local/share/modforge/archives/`: imported original archives
- `~/.local/share/modforge/mods/`: validated extracted contents
- `~/.local/share/modforge/backups/`: timestamp-independent deployment ID snapshots
- `~/.local/share/modforge/journals/`: atomic JSON recovery journals and staging
- `~/.local/share/modforge/modforge.sqlite3`: SQLite metadata and history
- `~/.config/modforge/`: reserved application configuration
- `~/.local/state/modforge/modforge.log`: application log

## Safety model and limitations

ZIP paths are normalized and rejected for absolute paths, traversal, drive-like paths, case-insensitive collisions, links, special files, and configured size/count/compression limits. Extracted files are never executed. Deployment validates the game root and every relative path, rejects symlink path components, holds an advisory per-game-root lock, stages files before touching destinations, backs up existing regular files, atomically replaces files where supported, and verifies hashes. A failed transaction attempts automatic restoration and retains a journal.

Every deployment begins with a durable JSON journal before staging or backup work. Journal states include planned, staging, backing up, applying, verifying, committed, rolling back, rolled back, and recovery required. Each planned file records its original and intended hashes, and progress is flushed as operations proceed. File replacements and rollback restores use same-directory atomic replacement where supported, with directory descriptors that reject symlink traversal. These filesystem operations are not a single atomic transaction; crashes can leave a partially applied set of files.

At startup, ModForge scans journals and database records without changing game files. Incomplete journals, corrupt or missing journals, missing backups, and database/journal state mismatches appear in **Recovery**. Review shows each affected path, its current hash classification, and backup verification. Recovery requires explicit confirmation, validates the configured root and backup paths, restores only from hash-verified backups, removes a ModForge-created file only while its hash still matches the recorded deployed hash, and journals its own progress. Ambiguous changed or missing paths require confirmation; unrecognized files that ModForge did not originally replace are preserved and left for manual intervention. A recovery that cannot be completed keeps its journal and backups and remains visible for follow-up.

Rollback compares current hashes with the expected deployed hashes. External changes stop rollback and require explicit confirmation before restoring verified backups. Files created by ModForge are removed only if they still match the deployed hash. If files overlap with another deployment that has not been rolled back, the earlier operation is blocked. Backups are retained after rollback.

Atomic rename and flush durability depend on the filesystem and storage device. A process kill, kernel crash, power loss, or disk failure can interrupt a transaction. Recovery never blindly replays a deployment; if current contents do not match either recorded state, it stops and preserves recovery material. Advisory locks coordinate ModForge instances, not unrelated programs, and cannot prevent a different application from changing the same game files. Permissions and metadata beyond ordinary file mode are not guaranteed. Symlink-based mod layouts are intentionally unsupported. A corrupt journal or an externally replaced file with no valid original backup needs manual intervention.

Archive extraction, deployment, rollback, journal inspection, and recovery run in a Qt thread pool so the window remains responsive. Cancellation is unavailable once a filesystem transaction starts; failures retain their journal and recovery material. Directory opening uses Qt desktop integration.

## Test and development

```sh
python -m pip install -e '.[dev]'
pytest -q
QT_QPA_PLATFORM=offscreen python -m modforge
```

Tests use temporary directories and dummy files; they do not target a real game installation.
The schema remains at version 1 because this phase adds no database columns or tables. The recovery reader accepts the prior schema-less journal format and normalizes its deployment states in memory.

## Disposable deployment walkthrough

Create an isolated fake root with the required marker and deployable paths, then use a small ZIP containing `archive/pc/mod/demo.archive`:

```sh
tmpdir=$(mktemp -d)
mkdir -p "$tmpdir/game/bin/x64"
printf 'old game data\n' > "$tmpdir/game/archive/pc/mod/demo.archive" 2>/dev/null || {
  mkdir -p "$tmpdir/game/archive/pc/mod"
  printf 'old game data\n' > "$tmpdir/game/archive/pc/mod/demo.archive"
}
```

Set that disposable `game` directory as the game root, import/enable the archive in a test profile, deploy, verify the changed contents, then roll back from Deployments and confirm the original content returns. Remove the temporary directory after the exercise.
