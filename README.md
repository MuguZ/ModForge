# ModForge

ModForge is a Linux-first desktop manager for local, file-based game mods. It supports multiple registered installations, Steam library discovery, managed ZIP imports, per-game profiles, configurable source-to-destination mappings, copy deployment, backups, rollback, and crash recovery.

Universal file management does **not** make every mod compatible with every game. Game engines, loaders, mod formats, anti-cheat systems, and Proton behavior vary. The generic adapter validates an accessible game directory and delegates file safety to the shared deployment engine. The Cyberpunk 2077 adapter verifies its `bin/x64` marker. ModForge has no verified specialized Far Cry 3, Sekiro, Lutris, Heroic, or Windows-only loader integration; those games can be registered and used with manual file mappings where their mods are ordinary files.

## Install and launch on Linux

Use a virtual environment; root privileges are not required:

```sh
cd ~/ModForge
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
modforge
```

Alternatively run `python -m modforge`. PySide6 uses the current desktop platform plugin. ModForge honors `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, and `XDG_STATE_HOME`; otherwise its data, configuration, and state default under `~/.local/share/modforge`, `~/.config/modforge`, and `~/.local/state/modforge`.

## Register and discover games

Use **Games → Add Game** to register any installation directory. Enter a display name, select the directory, and optionally enter an executable path. Registration does not require a game-specific directory marker or an executable. Multiple installations with the same name are allowed when their roots differ. **Edit selected**, **Rescan**, **Disable registration**, and **Re-enable selected** maintain registrations. Disabling preserves profiles, mappings, deployment history, backups, recovery journals, and game files.

**Discover Steam games** reads standard Steam user roots, `libraryfolders.vdf`, and `appmanifest_*.acf` files. It handles multiple libraries and paths containing spaces; it does not launch or change games. Unmounted libraries may remain missing until restored. Add a custom Steam root manually if it is outside standard locations. Steam discovery is fixture-tested; it does not determine whether a game-specific mod or loader works. Proton prefixes and Steam launch options are never modified.

## Import a mod and map it

1. Choose **Import ZIP** in **Mod Library**. ModForge checks archive paths, entry types, expanded sizes, compression ratios, file count, and nesting depth before presenting a preview. Confirm import to store the original archive and extracted files in managed ModForge storage. Import never writes to a game.
2. Select a game on **Games**. Select a mod and choose **Map selected mod**. Each archive file starts with its original relative path as the proposed destination; edit individual destinations to match the selected game's layout. The mappings are saved separately for each game.
3. In **Profiles**, enable the mod in that game's selected profile. Profile configuration changes do not directly alter game files.
4. Use **Dashboard → Deploy active profile**. Review additions, replacements, files that will be restored or removed from the previous deployment, and externally changed files. Confirm to apply the profile.

ModForge does not automatically flatten nested folders. For example, `ExampleMod/SomeFolder/file.dat` is initially mapped to that complete path; change it to `SomeFolder/file.dat` or another destination only when that matches the mod's documented layout. The preview shows the game-relative destination. Destination mapping suggestions and specialized loader installation are not available.

## Profiles, deployment, and recovery

Profiles and enabled-mod membership are scoped to a registered game installation. Each game has a protected default profile. Selecting or editing a profile changes its configuration; **Deploy active profile** applies it after preview. Profile application rolls back the previous successful ModForge deployment for that game, then deploys the desired file manifest. If the new apply fails after rollback, ModForge attempts to reapply the previous manifest. If files changed outside ModForge or recovery is uncertain, it stops and retains the journal and backups for **Recovery**.

Deployment uses copy only. Every operation uses path validation, no-follow destination checks, per-root locking, staging, durable journals, file hashes, and backups for overwritten files. On rollback or profile transition, ModForge restores verified original files or removes a file it created only while the deployed fingerprint still matches. Unrelated files are preserved. Backups remain after rollback. A file conflict blocks the profile until the contributing mods or mappings are changed. File replacement priority is not the same as a game's runtime plugin or asset load order.

The **Deployments**, **Backups**, and **Recovery** pages show transaction history and unresolved work. Rollback/recovery use the game root stored in each transaction rather than whichever game is currently selected. A disabled registration can still be re-enabled to manage it; existing deployment records retain their recorded path.

## Data safety and limits

ZIP content is never executed. Absolute paths, traversal, symlinks, special files, duplicate/case-colliding names, excessive sizes/counts/depth, and suspicious compression ratios are rejected. Managed source paths are checked for symlink components before deployment. Deployment does not use `sudo`, change Proton prefixes, or alter launcher configuration.

The database uses additive versioned migrations. Existing IDs, profile membership, game registration, and deployment history are retained; a legacy global profile is associated with a sole registered game, or copied into isolated profiles when multiple game roots already exist. Removing a game registration is a reversible disable, preserving related records.

## Development and tests

```sh
python -m pip install -e '.[dev]'
pytest -q
QT_QPA_PLATFORM=offscreen python -m modforge
```

Tests use disposable temporary directories and fixture Steam manifests. They do not target installed games, Proton prefixes, personal mod libraries, or existing backups. The current supported Python declaration is 3.11 or newer; the Linux development environment uses Python 3.14 and PySide6 6.12.

## Known limitations

- Copy deployment is the only implemented strategy; symlink, hardlink, and adapter-defined deployment are not active.
- There is no online provider, automatic mod download/update, launch integration, or automatic dependency resolution.
- Mod metadata editing, update/version management, profile import/export and comparison, and standalone backup restore UI are not implemented.
- Deployment is file-based. Game-specific runtime load orders, anti-cheat constraints, DLL injection, and Windows-only loaders are unverified.
- The current profile transition uses multiple durable rollback/deployment transactions. If the process stops between them, Recovery retains the state; it is not one filesystem-atomic operation.

See [adapter development](docs/adapters.md) for the current adapter contract and its limits.
