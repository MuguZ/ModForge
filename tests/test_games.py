from pathlib import Path

import pytest

from modforge.database import Database
from modforge.games.discovery import discover_steam_games, parse_vdf
from modforge.games.registry import GameRegistry
from modforge.core.deployment_manager import DeploymentManager
from modforge.games.generic import GenericFileAdapter
from modforge.config import AppPaths
from modforge.core.profile_manager import ProfileManager


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
    assert registry.get(first)["enabled"] == 0
    assert ProfileManager(registry.db).list_profiles(first)


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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    game = GameRegistry(reopened).get("legacy-cyberpunk2077")
    assert game["root"] == str(tmp_path / "Cyberpunk")
    assert game["adapter_id"] == "cyberpunk2077"


def test_migration_from_existing_schema_preserves_profile_mod_and_deployment_ids(tmp_path):
    import sqlite3
    path=tmp_path/"schema-v1.sqlite3"
    connection=sqlite3.connect(path)
    connection.executescript("""
      CREATE TABLE games(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT);
      CREATE TABLE mods(id TEXT PRIMARY KEY,name TEXT NOT NULL,version TEXT,author TEXT,description TEXT,
        archive TEXT NOT NULL,archive_hash TEXT NOT NULL UNIQUE,imported_at TEXT NOT NULL,extracted_path TEXT NOT NULL,notes TEXT DEFAULT '');
      CREATE TABLE mod_files(mod_id TEXT NOT NULL REFERENCES mods(id) ON DELETE CASCADE,relative_path TEXT NOT NULL,
        size INTEGER NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(mod_id,relative_path));
      CREATE TABLE profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
      CREATE TABLE profile_mods(profile_id TEXT REFERENCES profiles(id) ON DELETE CASCADE,
        mod_id TEXT REFERENCES mods(id) ON DELETE CASCADE,PRIMARY KEY(profile_id,mod_id));
      CREATE TABLE deployments(id TEXT PRIMARY KEY,profile_id TEXT,started_at TEXT NOT NULL,finished_at TEXT,
        status TEXT NOT NULL,journal TEXT NOT NULL,message TEXT DEFAULT '');
      CREATE TABLE deployment_files(deployment_id TEXT REFERENCES deployments(id),relative_path TEXT NOT NULL,
        existed INTEGER NOT NULL,original_hash TEXT,backup_path TEXT,deployed_hash TEXT,status TEXT NOT NULL,
        PRIMARY KEY(deployment_id,relative_path));
      CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      INSERT INTO games VALUES('game-1','Example','/tmp/example');
      INSERT INTO profiles VALUES('default','Default','2025-01-01T00:00:00+00:00');
      INSERT INTO mods(id,name,archive,archive_hash,imported_at,extracted_path) VALUES('mod-1','Example mod','/archive.zip','hash','2025-01-01','/mods/mod-1');
      INSERT INTO profile_mods VALUES('default','mod-1');
      INSERT INTO deployments(id,profile_id,started_at,status,journal) VALUES('deploy-1','default','2025-01-01','success','/journals/deploy-1.json');
      PRAGMA user_version=1;
    """)
    connection.commit(); connection.close()
    migrated=Database(path)
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0]==5
        profile=db.execute("SELECT game_id,is_default FROM profiles WHERE id='default'").fetchone()
        assert tuple(profile)==("game-1",1)
        assert db.execute("SELECT mod_id FROM profile_mods WHERE profile_id='default'").fetchone()[0]=="mod-1"
        assert db.execute("SELECT game_id FROM deployments WHERE id='deploy-1'").fetchone()[0]=="game-1"


def test_v2_multi_game_migration_clones_profile_state_without_cross_game_sharing(tmp_path):
    import sqlite3
    path=tmp_path/"schema-v2.sqlite3";connection=sqlite3.connect(path)
    connection.executescript("""
      CREATE TABLE games(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT,executable TEXT,launcher TEXT,
        platform TEXT NOT NULL DEFAULT 'linux',source TEXT NOT NULL DEFAULT 'manual',strategy TEXT NOT NULL DEFAULT 'copy',
        adapter_id TEXT,enabled INTEGER NOT NULL DEFAULT 1,validation_status TEXT NOT NULL DEFAULT 'unvalidated',
        validation_message TEXT NOT NULL DEFAULT '',last_scanned_at TEXT,created_at TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL DEFAULT '');
      CREATE TABLE mods(id TEXT PRIMARY KEY,name TEXT NOT NULL,version TEXT,author TEXT,description TEXT,
        archive TEXT NOT NULL,archive_hash TEXT NOT NULL UNIQUE,imported_at TEXT NOT NULL,extracted_path TEXT NOT NULL,notes TEXT DEFAULT '');
      CREATE TABLE mod_files(mod_id TEXT NOT NULL REFERENCES mods(id) ON DELETE CASCADE,relative_path TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(mod_id,relative_path));
      CREATE TABLE profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
      CREATE TABLE profile_mods(profile_id TEXT REFERENCES profiles(id) ON DELETE CASCADE,mod_id TEXT REFERENCES mods(id) ON DELETE CASCADE,PRIMARY KEY(profile_id,mod_id));
      CREATE TABLE deployments(id TEXT PRIMARY KEY,profile_id TEXT,started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,journal TEXT NOT NULL,message TEXT DEFAULT '');
      CREATE TABLE deployment_files(deployment_id TEXT REFERENCES deployments(id),relative_path TEXT NOT NULL,existed INTEGER NOT NULL,original_hash TEXT,backup_path TEXT,deployed_hash TEXT,status TEXT NOT NULL,PRIMARY KEY(deployment_id,relative_path));
      CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      INSERT INTO games(id,name,root,created_at) VALUES('g1','One','/tmp/g1','2025-01-01');
      INSERT INTO games(id,name,root,created_at) VALUES('g2','Two','/tmp/g2','2025-01-02');
      INSERT INTO profiles VALUES('default','Default','2025-01-01');
      INSERT INTO profiles VALUES('custom','Custom','2025-01-02');
      INSERT INTO mods(id,name,archive,archive_hash,imported_at,extracted_path) VALUES('m1','One mod','/a','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','2025-01-01','/mods/m1');
      INSERT INTO profile_mods VALUES('custom','m1');
      INSERT INTO settings VALUES('active_profile','custom');
      PRAGMA user_version=2;
    """)
    connection.commit();connection.close()
    migrated=Database(path)
    with migrated.connect() as db:
        g1=db.execute("SELECT id FROM profiles WHERE game_id='g1' AND name='Custom [g1]'").fetchone()
        g2=db.execute("SELECT id FROM profiles WHERE game_id='g2' AND name='Custom [g2]'").fetchone()
        assert g1 and g2 and g1[0]!=g2[0]
        assert db.execute("SELECT value FROM settings WHERE key='active_profile:g1'").fetchone()[0]==g1[0]
        assert db.execute("SELECT value FROM settings WHERE key='active_profile:g2'").fetchone()[0]==g2[0]
        assert db.execute("SELECT COUNT(*) FROM profile_mods WHERE profile_id IN (?,?)",(g1[0],g2[0])).fetchone()[0]==2
        assert db.execute("SELECT COUNT(*) FROM profiles WHERE game_id IS NULL").fetchone()[0]==2


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


def test_each_registered_game_gets_isolated_default_profile(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    games = GameRegistry(db)
    first = games.register("One", tmp_path / "one")
    second = games.register("Two", tmp_path / "two")
    profiles = ProfileManager(db)
    first_profiles = profiles.list_profiles(first)
    second_profiles = profiles.list_profiles(second)
    assert len(first_profiles) == len(second_profiles) == 1
    assert first_profiles[0]["id"] != second_profiles[0]["id"]
    custom = profiles.create("Testing", game_id=first)
    same_name_other_game = profiles.create("Testing", game_id=second)
    assert {p["id"] for p in profiles.list_profiles(first)} == {first_profiles[0]["id"], custom}
    assert {p["id"] for p in profiles.list_profiles(second)} == {second_profiles[0]["id"], same_name_other_game}
    assert next(p for p in profiles.list_profiles(second) if p["id"]==same_name_other_game)["name"]=="Testing"
    with pytest.raises(ValueError, match="same game"):
        profiles.create("Wrong game", first_profiles[0]["id"], game_id=second)


def test_profile_definition_export_import_resolves_mods_by_archive_hash(tmp_path):
    import zipfile
    from modforge.core.archive_importer import ArchiveImporter
    from modforge.core.mod_library import ModLibrary
    archive=tmp_path/"portable-profile.zip"
    with zipfile.ZipFile(archive,"w") as stream:stream.writestr("some/file.dat",b"profile content")
    library_one=ModLibrary(Database(tmp_path/"one.db"),ArchiveImporter(tmp_path/"one-archives",tmp_path/"one-mods"))
    mod_id=library_one.import_archive(archive)
    game_one=GameRegistry(library_one.db).register("Portable one",tmp_path/"game-one")
    profile_one=ProfileManager(library_one.db).list_profiles(game_one)[0]["id"]
    ProfileManager(library_one.db).set_enabled(profile_one,mod_id,True)
    definition=ProfileManager(library_one.db).export_definition(profile_one)

    library_two=ModLibrary(Database(tmp_path/"two.db"),ArchiveImporter(tmp_path/"two-archives",tmp_path/"two-mods"))
    second_mod=library_two.import_archive(archive)
    game_two=GameRegistry(library_two.db).register("Portable two",tmp_path/"game-two")
    profile_two=ProfileManager(library_two.db).import_definition(definition,game_two,name="Imported")
    assert library_two.enabled_mod_ids(profile_two)==[second_mod]
    assert next(p for p in ProfileManager(library_two.db).list_profiles(game_two) if p["id"]==profile_two)["name"]=="Imported"
    empty=ProfileManager(library_two.db).create("Empty",game_id=game_two)
    assert ProfileManager(library_two.db).compare(empty,profile_two)["added"]==["portable-profile"]
    invalid={"schema":1,"profile":{"name":"bad"},"mods":[{"id":"x","archive_hash":"not-a-hash","path":"../../outside"}]}
    with pytest.raises(ValueError,match="invalid mod reference"):
        ProfileManager(library_two.db).import_definition(invalid,game_two)


def test_profile_comparison_requires_same_game(services,tmp_path):
    db,*_=services;registry=GameRegistry(db);profiles=ProfileManager(db)
    first=registry.register("Compare One",tmp_path/"compare-one")
    second=registry.register("Compare Two",tmp_path/"compare-two")
    p1=profiles.list_profiles(first)[0]["id"];p2=profiles.list_profiles(second)[0]["id"]
    with pytest.raises(ValueError,match="different games"):
        profiles.compare(p1,p2)


def test_mod_metadata_edit_is_persistent_and_bounded(services,tmp_path):
    import zipfile
    _,library,*_=services
    archive=tmp_path/"metadata.zip"
    with zipfile.ZipFile(archive,"w") as stream:stream.writestr("file.dat",b"x")
    mod_id=library.import_archive(archive)
    library.update_metadata(mod_id,name="Better name",author="Mod author",source_url="https://example.invalid/mod",
                            category="Graphics",tags="visual, texture",notes="local note")
    mod=next(row for row in library.list_mods() if row["id"]==mod_id)
    assert (mod["name"],mod["author"],mod["category"],mod["tags"],mod["notes"])==(
        "Better name","Mod author","Graphics","visual, texture","local note")
    with pytest.raises(ValueError,match="text length"):
        library.update_metadata(mod_id,name="Invalid",description="d"*20_001)


def test_game_specific_mappings_persist_and_reject_collisions(services, tmp_path):
    import zipfile
    _, library, _, _, _, _ = services
    archive = tmp_path / "mapping.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("nested/one.dat", b"one")
        stream.writestr("nested/two.dat", b"two")
    mod_id = library.import_archive(archive)
    db = library.db
    game_id = GameRegistry(db).register("Mapped Game", tmp_path / "mapped")
    adapter = GenericFileAdapter()
    mapping = library.mappings(game_id, mod_id)
    mapping["nested/one.dat"] = "install/one.dat"
    mapping["nested/two.dat"] = "install/two.dat"
    library.set_mappings(game_id, mod_id, mapping, adapter)
    assert library.mappings(game_id, mod_id) == mapping
    mapping["nested/two.dat"] = "install/one.dat"
    with pytest.raises(ValueError, match="same destination"):
        library.set_mappings(game_id, mod_id, mapping, adapter)
    mapping["nested/two.dat"] = "../outside"
    with pytest.raises(ValueError, match="Unsafe deployment path"):
        library.set_mappings(game_id, mod_id, mapping, adapter)


def test_same_relative_file_can_be_deployed_to_two_registered_games(services, tmp_path):
    db, _, manager, _, _, _ = services
    registry = GameRegistry(db)
    first_root, second_root = tmp_path / "first root", tmp_path / "second root"
    first_root.mkdir(); second_root.mkdir()
    first_id = registry.register("First", first_root)
    second_id = registry.register("Second", second_root)
    first_default = ProfileManager(db).list_profiles(first_id)[0]["id"]
    second_default = ProfileManager(db).list_profiles(second_id)[0]["id"]
    source = tmp_path / "shared.bin"; source.write_bytes(b"payload")
    manager.deploy(first_root, GenericFileAdapter(), first_default, {"mods/a.bin": source}, game_id=first_id)
    manager.deploy(second_root, GenericFileAdapter(), second_default, {"mods/a.bin": source}, game_id=second_id)
    assert (first_root / "mods/a.bin").read_bytes() == (second_root / "mods/a.bin").read_bytes()
    from modforge.core.rollback_manager import RollbackManager
    RollbackManager(db,tmp_path/"rollback-locks").rollback(
        _deployment_id_for(db,first_id),first_root,GenericFileAdapter())
    assert not (first_root/"mods/a.bin").exists()
    assert (second_root/"mods/a.bin").read_bytes()==b"payload"


def _deployment_id_for(db,game_id):
    with db.connect() as connection:
        return connection.execute("SELECT id FROM deployments WHERE game_id=?",(game_id,)).fetchone()[0]


def test_mapped_manifest_uses_saved_game_destinations(services, tmp_path):
    import zipfile
    db, library, _, _, _, _ = services
    archive=tmp_path/"mapped.zip"
    with zipfile.ZipFile(archive,"w") as stream:
        stream.writestr("Publisher/Files/file.bin",b"mapped")
    mod_id=library.import_archive(archive)
    game_id=GameRegistry(db).register("Mapping target",tmp_path/"mapping-game")
    profile=ProfileManager(db).list_profiles(game_id)[0]["id"]
    ProfileManager(db).set_enabled(profile,mod_id,True)
    library.set_mappings(game_id,mod_id,{"Publisher/Files/file.bin":"content/file.bin"},GenericFileAdapter())
    assert library.mapped_manifest(profile,game_id)=={
        "content/file.bin": Path(library.list_mods()[0]["extracted_path"])/"Publisher/Files/file.bin"
    }


def test_mapped_manifest_rejects_source_parent_symlink(services,tmp_path):
    import zipfile
    db,library,_,_,_,_=services
    archive=tmp_path/"symlink-source.zip"
    with zipfile.ZipFile(archive,"w") as stream:stream.writestr("parent/file.bin",b"mod")
    mod_id=library.import_archive(archive)
    game_id=GameRegistry(db).register("Symlink check",tmp_path/"symlink-game")
    profile=ProfileManager(db).list_profiles(game_id)[0]["id"]
    ProfileManager(db).set_enabled(profile,mod_id,True)
    extracted=Path(library.list_mods()[0]["extracted_path"])
    outside=tmp_path/"outside";outside.mkdir();(outside/"file.bin").write_bytes(b"outside")
    (extracted/"parent").rename(extracted/"parent-original")
    (extracted/"parent").symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match="missing or unsafe"):
        library.mapped_manifest(profile,game_id)


def test_apply_profile_disables_only_owned_files(services, tmp_path):
    import zipfile
    db, library, manager, _, _, data = services
    game_id=GameRegistry(db).register("Lifecycle",tmp_path/"lifecycle-game")
    game=GameRegistry(db).get(game_id); root=Path(game["root"]); root.mkdir()
    profile=ProfileManager(db).list_profiles(game_id)[0]["id"]
    archive=tmp_path/"life.zip"
    with zipfile.ZipFile(archive,"w") as stream: stream.writestr("mod/file.dat",b"managed")
    mod_id=library.import_archive(archive); ProfileManager(db).set_enabled(profile,mod_id,True)
    library.set_mappings(game_id,mod_id,{"mod/file.dat":"game/file.dat"},GenericFileAdapter())
    unowned=root/"notes.txt"; unowned.write_text("keep")
    manifest=library.mapped_manifest(profile,game_id)
    deployment=manager.apply_profile(root,GenericFileAdapter(),profile,manifest,game_id,
                                     owners={"game/file.dat":[mod_id]})
    assert deployment and (root/"game/file.dat").read_bytes()==b"managed"
    with db.connect() as connection:
        owner=connection.execute("SELECT mod_ids,fingerprint,status FROM deployed_file_owners WHERE game_id=? AND relative_path='game/file.dat'",(game_id,)).fetchone()
        assert owner["mod_ids"]==f'["{mod_id}"]' and owner["status"]=="active"
    ProfileManager(db).set_enabled(profile,mod_id,False)
    assert manager.apply_profile(root,GenericFileAdapter(),profile,{},game_id) is None
    assert not (root/"game/file.dat").exists()
    assert unowned.read_text()=="keep"
    with db.connect() as connection:
        assert connection.execute("SELECT status FROM deployments WHERE id=?",(deployment,)).fetchone()[0]=="rolled_back"
        assert connection.execute("SELECT status FROM deployed_file_owners WHERE game_id=? AND relative_path='game/file.dat'",(game_id,)).fetchone()[0]=="rolled_back"


def test_profile_transition_preserves_previous_profile_after_new_apply_failure(services, tmp_path, monkeypatch):
    import zipfile
    from modforge.core import deployment_manager as deployment_module
    db, library, manager, _, _, _ = services
    game_id=GameRegistry(db).register("Transition",tmp_path/"transition-game")
    root=Path(GameRegistry(db).get(game_id)["root"]); root.mkdir()
    profiles=ProfileManager(db); old_profile=profiles.list_profiles(game_id)[0]["id"]
    new_profile=profiles.create("Candidate",game_id=game_id)
    mods=[]
    for name,content in (("old",b"old contents"),("new",b"new contents")):
        archive=tmp_path/f"{name}.zip"
        with zipfile.ZipFile(archive,"w") as stream: stream.writestr("mod/file.dat",content)
        mod_id=library.import_archive(archive); mods.append(mod_id)
        library.set_mappings(game_id,mod_id,{"mod/file.dat":"game/file.dat"},GenericFileAdapter())
    profiles.set_enabled(old_profile,mods[0],True); profiles.set_enabled(new_profile,mods[1],True)
    initial=library.mapped_manifest(old_profile,game_id)
    manager.apply_profile(root,GenericFileAdapter(),old_profile,initial,game_id)
    original_copy=deployment_module.copy_durable
    failed=False
    with db.connect() as connection:
        candidate_root=connection.execute("SELECT extracted_path FROM mods WHERE id=?",(mods[1],)).fetchone()[0]
    new_source=Path(candidate_root)/"mod/file.dat"
    def fail_candidate(source,destination,**kwargs):
        nonlocal failed
        if Path(source)==new_source and ".stage-" in str(destination) and not failed:
            failed=True
            raise OSError("injected candidate staging failure")
        return original_copy(source,destination,**kwargs)
    monkeypatch.setattr(deployment_module,"copy_durable",fail_candidate)
    candidate=library.mapped_manifest(new_profile,game_id)
    with pytest.raises(Exception,match="previous deployed files were restored"):
        manager.apply_profile(root,GenericFileAdapter(),new_profile,candidate,game_id)
    assert (root/"game/file.dat").read_bytes()==b"old contents"


def test_profile_transition_does_not_overwrite_external_changes(services,tmp_path):
    import zipfile
    from modforge.core.deployment_manager import DeploymentError
    db,library,manager,_,_,_=services
    game_id=GameRegistry(db).register("External edit",tmp_path/"external-game")
    root=Path(GameRegistry(db).get(game_id)["root"]); root.mkdir()
    profile=ProfileManager(db).list_profiles(game_id)[0]["id"]
    archive=tmp_path/"external.zip"
    with zipfile.ZipFile(archive,"w") as stream:stream.writestr("mod/file.dat",b"managed")
    mod_id=library.import_archive(archive); ProfileManager(db).set_enabled(profile,mod_id,True)
    library.set_mappings(game_id,mod_id,{"mod/file.dat":"game/file.dat"},GenericFileAdapter())
    manager.apply_profile(root,GenericFileAdapter(),profile,library.mapped_manifest(profile,game_id),game_id)
    destination=root/"game/file.dat"; destination.write_bytes(b"user edit")
    with pytest.raises(DeploymentError,match="Inspect Recovery"):
        manager.apply_profile(root,GenericFileAdapter(),profile,{},game_id)
    assert destination.read_bytes()==b"user edit"
