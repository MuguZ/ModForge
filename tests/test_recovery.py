from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from modforge.core import deployment_manager, recovery_manager
from modforge.core.deployment_manager import DeploymentError
from modforge.core.recovery_manager import RecoveryError, RecoveryManager
from modforge.core.rollback_manager import RollbackManager


class SimulatedProcessExit(BaseException):
    """Bypass normal exception recovery to model an abrupt process exit."""


def make_manager(services):
    db, _, deployer, root, adapter, data = services
    recoverer = RecoveryManager(db, data / "backups", data / "journals", data / "locks")
    return db, deployer, recoverer, root, adapter, data


def test_recover_crash_after_backup_creation(services, tmp_path, monkeypatch):
    db, deployer, recovery, root, adapter, data = make_manager(services)
    destination = root / "r6/scripts/existing.lua"
    destination.parent.mkdir(parents=True)
    destination.write_text("before")
    source = tmp_path / "mod.lua"
    source.write_text("after")
    original_copy = deployment_manager.copy_durable

    def interrupt_after_backup(source_path, destination_path, **kwargs):
        original_copy(source_path, destination_path, **kwargs)
        if destination_path.is_relative_to(data / "backups"):
            raise SimulatedProcessExit()

    monkeypatch.setattr(deployment_manager, "copy_durable", interrupt_after_backup)
    with pytest.raises(SimulatedProcessExit):
        deployer.deploy(root, adapter, "default", {"r6/scripts/existing.lua": source})
    monkeypatch.setattr(deployment_manager, "copy_durable", original_copy)
    entry = recovery.unresolved()[0]
    report = recovery.inspect(entry["id"], root, adapter)
    assert report["files"][0]["backup_status"] == "verified"
    result = recovery.recover(entry["id"], root, adapter)
    assert result["status"] == "rolled_back"
    assert destination.read_text() == "before"


def test_staging_failure_leaves_original_and_cleans_private_stage(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, data = make_manager(services)
    destination = root / "r6/scripts/a.lua"
    destination.parent.mkdir(parents=True)
    destination.write_text("original")
    source = tmp_path / "mod"
    source.write_text("replacement")
    original_copy = deployment_manager.copy_durable

    def fail_stage(source_path, destination_path, **kwargs):
        if destination_path.is_relative_to(data / "journals"):
            raise OSError("injected staging failure")
        return original_copy(source_path, destination_path, **kwargs)

    monkeypatch.setattr(deployment_manager, "copy_durable", fail_stage)
    with pytest.raises(DeploymentError, match="did not complete"):
        deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": source})
    assert destination.read_text() == "original"
    assert not list((data / "journals").glob(".stage-*"))
    assert recovery.unresolved() == []


def test_mid_deployment_exception_restores_first_replacement(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, _ = make_manager(services)
    old = root / "r6/scripts/a.lua"
    old.parent.mkdir(parents=True)
    old.write_text("old")
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("new")
    second.write_text("new second")
    original = deployment_manager.replace_at
    count = 0

    def fail_after_first(*args, **kwargs):
        nonlocal count
        original(*args, **kwargs)
        count += 1
        if count == 1:
            raise OSError("injected mid-apply failure")

    monkeypatch.setattr(deployment_manager, "replace_at", fail_after_first)
    with pytest.raises(DeploymentError, match="original filesystem state was restored"):
        deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": first, "r6/scripts/b.lua": second})
    assert old.read_text() == "old"
    assert not (root / "r6/scripts/b.lua").exists()
    assert recovery.unresolved() == []


def test_backup_failure_does_not_change_game_and_is_journaled(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, data = make_manager(services)
    dest = root / "r6/scripts/a.lua"
    dest.parent.mkdir(parents=True)
    dest.write_text("old")
    source = tmp_path / "mod"
    source.write_text("new")
    original = deployment_manager.copy_durable

    def fail_backup(src, dst, **kwargs):
        if dst.is_relative_to(data / "backups"):
            raise OSError("injected backup failure")
        return original(src, dst, **kwargs)

    monkeypatch.setattr(deployment_manager, "copy_durable", fail_backup)
    with pytest.raises(DeploymentError, match="original filesystem state was restored"):
        deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": source})
    assert dest.read_text() == "old"
    assert recovery.unresolved() == []


def test_hash_verification_failure_recovers(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, _ = make_manager(services)
    dest = root / "r6/scripts/a.lua"
    dest.parent.mkdir(parents=True)
    dest.write_text("old")
    source = tmp_path / "mod"
    source.write_text("new")
    original = deployment_manager.hash_at
    calls = 0

    def fail_post_hash(game_root, rel):
        nonlocal calls
        result = original(game_root, rel)
        if rel == "r6/scripts/a.lua":
            calls += 1
            if calls == 4:
                return "0" * 64
        return result

    monkeypatch.setattr(deployment_manager, "hash_at", fail_post_hash)
    with pytest.raises(DeploymentError, match="original filesystem state was restored"):
        deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": source})
    assert dest.read_text() == "old"
    assert recovery.unresolved() == []


def test_database_finalization_failure_is_visible_and_reconcilable(services, tmp_path, monkeypatch):
    db, deployer, recovery, root, adapter, _ = make_manager(services)
    source = tmp_path / "mod"
    source.write_text("deployed")
    original_connect = db.connect
    failure_active = False

    class ConnectionProxy:
        def __init__(self, connection): self.connection = connection
        def __getattr__(self, name): return getattr(self.connection, name)
        def execute(self, sql, parameters=()):
            nonlocal failure_active
            if "UPDATE deployments SET status='success'" in sql:
                failure_active = True
                raise sqlite3.OperationalError("injected database finalization failure")
            if failure_active and "UPDATE deployments SET status=?" in sql:
                raise sqlite3.OperationalError("injected database finalization failure")
            return self.connection.execute(sql, parameters)

    @contextmanager
    def fail_finalization():
        with original_connect() as connection:
            yield ConnectionProxy(connection)

    monkeypatch.setattr(db, "connect", fail_finalization)
    with pytest.raises(DeploymentError, match="inspect Recovery"):
        deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": source})
    deployment_id = (deployer.journals / next(deployer.journals.glob("*.json")).name).stem
    assert (root / "r6/scripts/a.lua").read_text() == "deployed"
    monkeypatch.setattr(db, "connect", original_connect)
    result = recovery.recover(deployment_id, root, adapter)
    assert result["status"] == "success"
    with db.connect() as connection:
        assert connection.execute("SELECT status FROM deployments WHERE id=?", (deployment_id,)).fetchone()[0] == "success"


def test_recovery_refuses_parent_symlink_swap(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, _ = make_manager(services)
    source = tmp_path / "mod"
    source.write_text("mod")
    original = deployment_manager.replace_at

    def interrupt(*args, **kwargs):
        original(*args, **kwargs)
        raise SimulatedProcessExit()

    monkeypatch.setattr(deployment_manager, "replace_at", interrupt)
    with pytest.raises(SimulatedProcessExit):
        deployer.deploy(root, adapter, "default", {"r6/scripts/new.lua": source})
    monkeypatch.setattr(deployment_manager, "replace_at", original)
    target_dir = root / "r6/scripts"
    outside = tmp_path / "outside"
    outside.mkdir()
    (target_dir / "new.lua").unlink()
    target_dir.rmdir()
    target_dir.symlink_to(outside, target_is_directory=True)
    deployment_id = recovery.unresolved()[0]["id"]
    with pytest.raises(DeploymentError):
        recovery.recover(deployment_id, root, adapter, confirmed=True)
    assert list(outside.iterdir()) == []


def test_database_v1_reopen_preserves_existing_schema(services):
    db, *_ = services
    from modforge.database import Database
    reopened = Database(db.path)
    with reopened.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM deployments").fetchone()[0] == 0


def test_recover_crash_after_existing_replacement(services, tmp_path, monkeypatch):
    db, deployer, recovery, root, adapter, _ = make_manager(services)
    destination = root / "r6/scripts/existing.lua"
    destination.parent.mkdir(parents=True)
    destination.write_text("before")
    source = tmp_path / "mod.lua"
    source.write_text("after")
    original_replace = deployment_manager.replace_at

    def interrupt_after_replace(*args, **kwargs):
        original_replace(*args, **kwargs)
        raise SimulatedProcessExit()

    monkeypatch.setattr(deployment_manager, "replace_at", interrupt_after_replace)
    with pytest.raises(SimulatedProcessExit):
        deployer.deploy(root, adapter, "default", {"r6/scripts/existing.lua": source})
    monkeypatch.setattr(deployment_manager, "replace_at", original_replace)
    deployment_id = recovery.unresolved()[0]["id"]
    result = recovery.recover(deployment_id, root, adapter)
    assert result["status"] == "rolled_back"
    assert destination.read_text() == "before"
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM deployments WHERE id=?", (deployment_id,)).fetchone()[0] == "rolled_back"


def test_recover_crash_after_new_file_creation(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, _ = make_manager(services)
    source = tmp_path / "new.lua"
    source.write_text("created by mod")
    original_replace = deployment_manager.replace_at

    def interrupt_after_replace(*args, **kwargs):
        original_replace(*args, **kwargs)
        raise SimulatedProcessExit()

    monkeypatch.setattr(deployment_manager, "replace_at", interrupt_after_replace)
    with pytest.raises(SimulatedProcessExit):
        deployer.deploy(root, adapter, "default", {"r6/scripts/new.lua": source})
    monkeypatch.setattr(deployment_manager, "replace_at", original_replace)
    deployment_id = recovery.unresolved()[0]["id"]
    recovery.recover(deployment_id, root, adapter)
    assert not (root / "r6/scripts/new.lua").exists()


def test_recover_midway_across_existing_and_new_files(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, _ = make_manager(services)
    existing = root / "r6/scripts/a.lua"
    existing.parent.mkdir(parents=True)
    existing.write_text("old")
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("new a")
    b.write_text("new b")
    original_replace = deployment_manager.replace_at
    count = 0

    def interrupt_first(*args, **kwargs):
        nonlocal count
        original_replace(*args, **kwargs)
        count += 1
        if count == 1:
            raise SimulatedProcessExit()

    monkeypatch.setattr(deployment_manager, "replace_at", interrupt_first)
    with pytest.raises(SimulatedProcessExit):
        deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": a, "r6/scripts/b.lua": b})
    monkeypatch.setattr(deployment_manager, "replace_at", original_replace)
    deployment_id = recovery.unresolved()[0]["id"]
    recovery.recover(deployment_id, root, adapter)
    assert existing.read_text() == "old"
    assert not (root / "r6/scripts/b.lua").exists()


def test_external_modification_needs_confirmation_and_is_preserved_when_new(services, tmp_path, monkeypatch):
    _, deployer, recovery, root, adapter, _ = make_manager(services)
    source = tmp_path / "file"
    source.write_text("mod")
    original_replace = deployment_manager.replace_at

    def interrupt(*args, **kwargs):
        original_replace(*args, **kwargs)
        raise SimulatedProcessExit()

    monkeypatch.setattr(deployment_manager, "replace_at", interrupt)
    with pytest.raises(SimulatedProcessExit):
        deployer.deploy(root, adapter, "default", {"r6/scripts/new.lua": source})
    monkeypatch.setattr(deployment_manager, "replace_at", original_replace)
    target = root / "r6/scripts/new.lua"
    target.write_text("user data")
    deployment_id = recovery.unresolved()[0]["id"]
    with pytest.raises(RecoveryError, match="ambiguous"):
        recovery.recover(deployment_id, root, adapter)
    with pytest.raises(RecoveryError, match="unowned file"):
        recovery.recover(deployment_id, root, adapter, confirmed=True)
    assert target.read_text() == "user data"


def test_corrupt_backup_and_journal_are_reported(services, tmp_path):
    _, deployer, recovery, root, adapter, data = make_manager(services)
    dest = root / "r6/scripts/a.lua"
    dest.parent.mkdir(parents=True)
    dest.write_text("old")
    source = tmp_path / "a"
    source.write_text("mod")
    deployment_id = deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": source})
    journal_path = data / "journals" / f"{deployment_id}.json"
    journal = json.loads(journal_path.read_text())
    journal["status"] = "recovery_required"
    journal_path.write_text(json.dumps(journal))
    backup = next((data / "backups" / deployment_id).rglob("a.lua"))
    backup.write_text("corrupt")
    dest.write_text("external")
    with pytest.raises(RecoveryError, match="backup is corrupt"):
        recovery.recover(deployment_id, root, adapter, confirmed=True)
    journal = data / "journals" / "broken.json"
    journal.write_text("{")
    assert any(item["corrupt"] and item["id"] == "broken" for item in recovery.unresolved())


def test_backup_path_escape_is_rejected(services, tmp_path):
    _, deployer, recovery, root, adapter, data = make_manager(services)
    dest = root / "r6/scripts/a.lua"
    dest.parent.mkdir(parents=True)
    dest.write_text("old")
    source = tmp_path / "a"
    source.write_text("mod")
    deployment_id = deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": source})
    journal_path = data / "journals" / f"{deployment_id}.json"
    journal = json.loads(journal_path.read_text())
    journal["status"] = "recovery_required"
    journal["files"][0]["backup_path"] = str(tmp_path / "outside-backup")
    journal_path.write_text(json.dumps(journal))
    dest.write_text("external")
    with pytest.raises(RecoveryError, match="backup is unsafe_path"):
        recovery.recover(deployment_id, root, adapter, confirmed=True)
    assert dest.read_text() == "external"


def test_interrupted_rollback_resumes_safely(services, tmp_path, monkeypatch):
    db, deployer, recovery, root, adapter, _ = make_manager(services)
    dest = root / "r6/scripts/a.lua"
    dest.parent.mkdir(parents=True)
    dest.write_text("old")
    source = tmp_path / "a"
    source.write_text("mod")
    deployment_id = deployer.deploy(root, adapter, "default", {"r6/scripts/a.lua": source})
    original_replace = recovery_manager.restore_at

    def interrupt(*args, **kwargs):
        original_replace(*args, **kwargs)
        raise SimulatedProcessExit()

    monkeypatch.setattr(recovery_manager, "restore_at", interrupt)
    with pytest.raises(SimulatedProcessExit):
        recovery.recover(deployment_id, root, adapter, rollback_committed=True)
    monkeypatch.setattr(recovery_manager, "restore_at", original_replace)
    assert dest.read_text() == "old"
    journal = Path(db.path).parent / "journals"
    resumed = RecoveryManager(db, journal.parent / "backups", journal, journal.parent / "locks")
    resumed.recover(deployment_id, root, adapter)
    assert dest.read_text() == "old"
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM deployments WHERE id=?", (deployment_id,)).fetchone()[0] == "rolled_back"


def test_overlapping_redeploy_and_repeated_rollback(services, tmp_path):
    db, deployer, _, root, adapter, data = make_manager(services)
    source = tmp_path / "mod"
    source.write_text("mod")
    path = "r6/scripts/a.lua"
    deployment_id = deployer.deploy(root, adapter, "default", {path: source})
    with pytest.raises(DeploymentError, match="earlier deployment"):
        deployer.deploy(root, adapter, "other-profile", {path: source})
    rollback = RollbackManager(db, data / "locks")
    rollback.rollback(deployment_id, root, adapter)
    rollback.rollback(deployment_id, root, adapter)
    assert not (root / path).exists()


def test_legacy_schema_less_journal_loads(services, tmp_path):
    _, _, recovery, _, _, _ = make_manager(services)
    old = {"id": "legacy", "root": str(tmp_path), "profile_id": "default", "status": "deploying",
           "updated_at": "2025-01-01T00:00:00+00:00", "files": []}
    path = recovery.journals / "legacy.json"
    path.write_text(json.dumps(old))
    payload = recovery._load(path)
    assert payload["schema"] == 1
    assert payload["status"] == "applying"


def test_previous_database_schema_opens_without_loss(services):
    db, *_ = services
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT name FROM profiles WHERE id='default'").fetchone()[0] == "Default"
    reopened = type(db)(db.path)
    with reopened.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
