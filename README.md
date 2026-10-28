# ModForge

ModForge is a Linux-first desktop tool for organizing local game mods and deploying ordinary files through explicit mappings. It supports registering arbitrary game installations, multiple roots, generic file-based deployment, per-game validation adapters, and read-only Steam library discovery. A game-specific adapter can improve validation or layouts, but is optional.

Generic registration does **not** mean every mod works with every game. Game engines, loaders, mod formats, anti-cheat systems, and Proton behavior vary. ModForge currently verifies its generic file-copy workflow and Cyberpunk 2077 root validation; it does not verify Far Cry 3, Sekiro, or other game-specific loaders. Those games can be registered and managed with manually chosen relative paths when their mods are ordinary files.

## Linux installation and launch

Use a virtual environment; root privileges are not required:

```sh
cd ~/ModForge
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
modforge
```

You can also launch with `python -m modforge`. PySide6 uses the current desktop session, including Wayland where the platform plugin is installed.

## Game setup

Open **Games → Add Game**, enter a display name, choose the installation directory, and optionally provide an executable path. An executable is not required. ModForge validates that a generic installation root is an accessible directory; this does not assert game or mod compatibility. The selected root can be marked active for deployment. Use **Edit selected**, **Rescan**, or **Remove registration** to maintain entries. Removing an entry only removes ModForge's registration.

**Discover Steam games** reads Steam library configuration and app manifests from standard user Steam locations and any available manifests in those libraries. It does not launch games or change files. For a library not detected automatically, use Add Game. Steam manifests may describe Proton installations; ModForge does not configure Proton or promise compatibility with Windows-only loaders.

The database migration adds game metadata in place and converts a prior `game_root` setting into a Cyberpunk 2077 registration. Existing mod, profile, deployment, backup, and journal data is retained.

## Import and deployment

1. Import a local ZIP from **Mod Library**. ModForge inspects and extracts it into separate managed storage; it does not execute files or extract directly into a game.
2. Review the listed archive paths. Enable the mod in a profile. Archive paths are used as relative destination paths; mapping controls are not yet provided, so restructure a mod archive before import if its paths do not match the intended game layout.
3. Select a registered game as active. **Dashboard → Deploy active profile** presents a per-file preview, then creates backups and a durable journal before deployment.
4. Use **Deployments**, **Backups**, and **Recovery** to inspect outcomes. Rollback and recovery preserve backups and stop when external changes make restoration ambiguous.

Deployment currently uses safe file copies. Symlink, hardlink, external-loader, and game-specific deployment strategies are not implemented. A file conflict blocks deployment until one contributing mod is disabled. File replacement order is not equivalent to runtime plugin or load order.

## Data and safety

Application data and managed archives/mods/backups/journals are stored under `~/.local/share/modforge`; logs are in `~/.local/state/modforge`; configuration is reserved under `~/.config/modforge`. ZIP entries are checked for traversal, absolute paths, duplicate/case-colliding names, links and special files, declared size limits, and suspicious compression ratios. Extraction is staged and bounded. Deployment uses no-follow filesystem operations, backups, per-root locks, hash verification, and durable recovery journals. Tests use temporary directories and do not target real game installations or Proton prefixes.

## Development

```sh
python -m pip install -e '.[dev]'
pytest -q
QT_QPA_PLATFORM=offscreen python -m modforge
```

The current app requires Python 3.11 or newer and PySide6 6.6 or newer. See [adapter development](docs/adapters.md) for the adapter contract and test approach. External mod providers, automatic downloads, launch integration, first-run wizard, profile export/import, destination remapping, and launcher integrations beyond Steam discovery are not implemented.
