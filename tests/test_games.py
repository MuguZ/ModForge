from pathlib import Path

import pytest

from modforge.database import Database
from modforge.games.discovery import discover_steam_games, parse_vdf
from modforge.games.registry import GameRegistry
from modforge.core.deployment_manager import DeploymentManager
from modforge.games.generic import GenericFileAdapter
from modforge.config import AppPaths


def test_generic_registry_multiple_paths_missing_and_duplicates(tmp_path):
    registry = GameRegistry(Database(tmp_path / "db.sqlite3"))
    game_a = tmp_path / "Games" / "Example One"
    game_b = tmp_path / "Games" / "例示 Game"
    game_a.mkdir(parents=True)
    game_b.mkdir()
    first = registry.register("Example One", game_a)
    second = registry.register("Example One", game_b, executable="bin/game.exe", platform="proton")
    assert first != second
    assert registry.get(first)["validation_status"] == "valid"
    assert registry.get(second)["executable"] == "bin/game.exe"
    with pytest.raises(ValueError, match="already registered"):
        registry.register("Duplicate", game_a)
    absent = registry.register("Portable Game", tmp_path / "unmounted" / "install")
    assert registry.get(absent)["validation_status"] == "missing_or_invalid"
    registry.edit(absent, root=game_a / "renamed")
    assert registry.get(absent)["root"].endswith("renamed")
    registry.remove(first)
    assert game_a.is_dir()  # registration removal leaves game files alone


def test_migration_preserves_legacy_game_root(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    db = Database(db_path)
    db.set_setting("game_root", str(tmp_path / "Cyberpunk"))
    # Reopen after simulating the previous schema version. The additive migration
    # discovers and preserves the legacy root as an adapter-specific registration.
    with db.connect() as connection:
        connection.execute("DELETE FROM games")
        connection.execute("PRAGMA user_version=1")
    reopened = Database(db_path)
    with reopened.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    game = GameRegistry(reopened).get("legacy-cyberpunk2077")
    assert game["root"] == str(tmp_path / "Cyberpunk")
    assert game["adapter_id"] == "cyberpunk2077"


def test_steam_multiple_library_folders_and_missing_mount(tmp_path):
    steam = tmp_path / "Steam Root"
    external = tmp_path / "mnt" / "Games Library"
    for library in (steam, external):
        (library / "steamapps/common").mkdir(parents=True)
    (steam / "steamapps/libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "' + str(steam) + '" } '
        '"1" { "path" "' + str(external) + '" } "2" { "path" "/missing/disk" } }'
    )
    (steam / "steamapps/appmanifest_10.acf").write_text(
        '"AppState" { "appid" "10" "name" "Native Game" "installdir" "Native Game" }'
    )
    (external / "steamapps/appmanifest_20.acf").write_text(
        '"AppState" { "appid" "20" "name" "Proton Game" "installdir" "Proton Game" }'
    )
    found = discover_steam_games(home=tmp_path / "unrelated", extras=[steam])
    assert {game["name"] for game in found} == {"Native Game", "Proton Game"}
    assert any("Games Library" in str(game["root"]) for game in found)


def test_generic_deployment_does_not_require_cyberpunk_layout(tmp_path):
    root = tmp_path / "Game Folder with spaces"
    root.mkdir()
    source = tmp_path / "mod.dat"
    source.write_bytes(b"mod")
    manager = DeploymentManager(Database(tmp_path / "db.sqlite3"), tmp_path / "backups",
                                tmp_path / "journals", tmp_path / "locks")
    manager.deploy(root, GenericFileAdapter(), "default", {"mods/custom file.dat": source})
    assert (root / "mods/custom file.dat").read_bytes() == b"mod"


def test_vdf_parser_rejects_unterminated_objects():
    with pytest.raises(ValueError, match="Unterminated"):
        parse_vdf('"libraryfolders" { "0" { "path" "/x" }')


def test_xdg_paths_are_respected(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state home"))
    paths = AppPaths.default()
    assert paths.data == tmp_path / "data home/modforge"
    assert paths.config == tmp_path / "config home/modforge"
    assert paths.state == tmp_path / "state home/modforge"
