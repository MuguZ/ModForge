"""Journaled deployment transactions with conservative recovery semantics."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from modforge.core.journal import fsync_directory, touch_journal, write_json_durable
from modforge.core.safe_fs import copy_into_root, hash_at, locked_file, replace_at
from modforge.database import Database
from modforge.games.base import GameAdapter

log = logging.getLogger(__name__)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise DeploymentError(f"Cannot hash non-regular file: {path}")
    with os.fdopen(fd, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_durable(source: Path, destination: Path, *, managed_root: Path | None = None,
                 managed_relative: str | None = None, source_root: Path | None = None,
                 source_relative: str | None = None) -> None:
    """Create a private copy and flush it before publishing its final name."""
    if managed_root is not None:
        if managed_relative is None:
            raise ValueError("managed_relative is required with managed_root")
        copy_into_root(source, managed_root, managed_relative, source_root, source_relative)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            os.close(source_fd)
            raise DeploymentError(f"Source is not a regular file: {source}")
        with os.fdopen(source_fd, "rb") as incoming, temp.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        shutil.copystat(source, temp, follow_symlinks=False)
        metadata_fd = os.open(temp, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(metadata_fd)
        finally:
            os.close(metadata_fd)
        os.replace(temp, destination)
        fsync_directory(destination.parent)
    finally:
        temp.unlink(missing_ok=True)


class DeploymentError(RuntimeError):
    pass


class DeploymentManager:
    def __init__(self, db: Database, backups: Path, journals: Path, lock_dir: Path):
        self.db, self.backups, self.journals, self.lock_dir = db, backups, journals, lock_dir
        for path in (backups, journals, lock_dir):
            if path.is_symlink():
                raise DeploymentError(f"Managed application directory cannot be a symlink: {path}")
            path.mkdir(parents=True, exist_ok=True)
        self.backups = backups.resolve()
        self.journals = journals.resolve()
        self.lock_dir = lock_dir.resolve()

    def deploy(self, root: Path, adapter: GameAdapter, profile_id: str,
               manifest: dict[str, Path], game_id: str | None = None,
               owners: dict[str, list[str]] | None = None) -> str:
        ok, reason = adapter.validate_root(root)
        if not ok:
            raise DeploymentError(reason)
        root = root.resolve(strict=True)
        if game_id:
            with self.db.connect() as db:
                game = db.execute("SELECT root,enabled FROM games WHERE id=?", (game_id,)).fetchone()
                profile = db.execute("SELECT game_id FROM profiles WHERE id=?", (profile_id,)).fetchone()
                if not game or not game["enabled"] or Path(game["root"]).resolve(strict=False) != root:
                    raise DeploymentError("Selected game registration does not match the deployment root")
                if not profile or profile["game_id"] not in (game_id, None):
                    raise DeploymentError("Profile is not associated with the selected game")
        paths: dict[str, Path] = {}
        folded: set[str] = set()
        for raw, source in manifest.items():
            try:
                rel = adapter.validate_relative_path(raw)
            except ValueError as exc:
                raise DeploymentError(str(exc)) from exc
            if rel.casefold() in folded:
                raise DeploymentError(f"Destination collision: {rel}")
            folded.add(rel.casefold())
            source = Path(source)
            if source.is_symlink():
                raise DeploymentError(f"Symbolic link sources are not supported: {source}")
            source = source.resolve(strict=True)
            if not source.is_file():
                raise DeploymentError(f"Invalid source file: {source}")
            paths[rel] = source
        lock_path = self._lock_path(root)
        with locked_file(lock_path):
            normalized_owners = {adapter.validate_relative_path(path): ids for path, ids in (owners or {}).items()}
            return self._deploy_locked(root, adapter, profile_id, paths, game_id, normalized_owners)

    def apply_profile(self, root: Path, adapter: GameAdapter, profile_id: str,
                      manifest: dict[str, Path], game_id: str,
                      owners: dict[str, list[str]] | None = None) -> str | None:
        """Safely replace this game's active ModForge deployment with a profile.

        Existing successful transactions for the same registered game are rolled
        back in reverse order, then the requested manifest is deployed. If the new
        deployment fails, the previous effective manifest is re-applied.
        """
        ok, reason = adapter.validate_root(root)
        if not ok:
            raise DeploymentError(reason)
        root = root.resolve(strict=True)
        paths = {}
        normalized_owners = {}
        folded = set()
        for raw, source in manifest.items():
            try:
                relative = adapter.validate_relative_path(raw)
            except ValueError as exc:
                raise DeploymentError(str(exc)) from exc
            if relative.casefold() in folded:
                raise DeploymentError(f"Destination collision: {relative}")
            folded.add(relative.casefold())
            source = Path(source)
            if source.is_symlink() or not source.is_file():
                raise DeploymentError(f"Invalid or symbolic-link source file: {source}")
            paths[relative] = source
            mod_ids=(owners or {}).get(raw, [])
            if not isinstance(mod_ids,list) or any(not isinstance(mod_id,str) for mod_id in mod_ids):
                raise DeploymentError(f"Invalid mod ownership list for {relative}")
            normalized_owners[relative] = list(mod_ids)
        with self.db.connect() as db:
            game = db.execute("SELECT root,enabled FROM games WHERE id=?", (game_id,)).fetchone()
            profile = db.execute("SELECT game_id FROM profiles WHERE id=?", (profile_id,)).fetchone()
            if not game or not game["enabled"] or Path(game["root"]).resolve(strict=False) != root:
                raise DeploymentError("Selected game registration does not match the deployment root")
            if not profile or profile["game_id"] != game_id:
                raise DeploymentError("Profile is not associated with the selected game")
            profile_mods={row[0] for row in db.execute("SELECT mod_id FROM profile_mods WHERE profile_id=?",(profile_id,))}
            for relative,mod_ids in normalized_owners.items():
                if relative not in paths or not set(mod_ids).issubset(profile_mods):
                    raise DeploymentError(f"Ownership metadata does not match enabled mods for {relative}")
            unfinished = db.execute("SELECT id,status FROM deployments WHERE game_id=? AND status NOT IN ('success','rolled_back','failed_recovered')",
                                    (game_id,)).fetchall()
            if unfinished:
                raise DeploymentError(f"Game has an unfinished deployment ({unfinished[0]['id']}, {unfinished[0]['status']}); inspect Recovery first")
            rows = db.execute("SELECT id,journal,profile_id FROM deployments WHERE game_id=? AND status='success' ORDER BY started_at DESC",
                              (game_id,)).fetchall()
        old_manifest = {}
        old_owners = {}
        transactions = []
        old_profile = rows[0]["profile_id"] if rows else profile_id
        # Validate all recovery material and source files before the first live change.
        for row in rows:
            journal = self.journals / f"{row['id']}.json"
            if journal.is_symlink() or not journal.is_file() or not journal.resolve().is_relative_to(self.journals):
                raise DeploymentError(f"Missing or unsafe journal for active deployment {row['id']}")
            try:
                payload = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DeploymentError(f"Cannot read active deployment journal {row['id']}: {exc}") from exc
            if payload.get("id") != row["id"] or Path(payload.get("root", "")).resolve(strict=False) != root:
                raise DeploymentError(f"Active deployment {row['id']} has inconsistent journal identity or root")
            if payload.get("status") != "committed":
                raise DeploymentError(f"Active deployment {row['id']} is not in a committed state; inspect Recovery")
            for record in payload.get("files", []):
                relative = adapter.validate_relative_path(record["path"])
                source = Path(record.get("source", ""))
                if source.is_symlink() or not source.is_file():
                    raise DeploymentError(f"Cannot transition profile; previous source is missing: {source}")
                if relative in old_manifest:
                    raise DeploymentError(f"Previous deployments overlap at {relative}; inspect Recovery")
                old_manifest[relative] = source
                old_owners[relative] = list(record.get("mod_ids", []))
            transactions.append(row["id"])
        lock_path = self._lock_path(root)
        with locked_file(lock_path):
            all_previous_rolled_back = False
            try:
                from modforge.core.recovery_manager import RecoveryManager
                recovery = RecoveryManager(self.db, self.backups, self.journals, self.lock_dir)
                for deployment_id in transactions:
                    recovery.recover(deployment_id, root, adapter, rollback_committed=True, lock_held=True)
                all_previous_rolled_back = True
                if not paths:
                    return None
                return self._deploy_locked(root, adapter, profile_id, paths, game_id, normalized_owners)
            except Exception as exc:
                safely_reapply = all_previous_rolled_back and "inspect Recovery" not in str(exc)
                if safely_reapply and old_manifest:
                    try:
                        self._deploy_locked(root, adapter, old_profile or profile_id, old_manifest, game_id, old_owners)
                    except Exception as restore_exc:
                        raise DeploymentError(
                            f"Profile transition failed ({exc}); restoring the previous profile also failed ({restore_exc}). Inspect Recovery."
                        ) from restore_exc
                if safely_reapply:
                    raise DeploymentError(
                        f"Profile transition failed ({exc}); the previous deployed files were restored."
                    ) from exc
                raise DeploymentError(
                    f"Profile transition stopped ({exc}); no automatic overwrite was attempted. Inspect Recovery."
                ) from exc

    def _lock_path(self, root: Path) -> Path:
        return self.lock_dir / (hashlib.sha256(str(root).encode()).hexdigest() + ".lock")

    def _deploy_locked(self, root: Path, adapter: GameAdapter, profile_id: str,
                       manifest: dict[str, Path], game_id: str | None = None,
                       owners: dict[str, list[str]] | None = None) -> str:
        with self.db.connect() as db:
            for rel in manifest:
                rows = db.execute("""SELECT df.relative_path FROM deployment_files df
                    JOIN deployments d ON d.id=df.deployment_id
                    WHERE d.status NOT IN ('rolled_back','failed_recovered')
                    AND (d.game_id IS NULL OR ? IS NULL OR d.game_id=?)
                    AND lower(df.relative_path)=lower(?)""", (game_id, game_id, rel)).fetchall()
                if rows:
                    raise DeploymentError(f"{rel} is managed by an earlier deployment; roll it back first")

        deployment_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc).isoformat()
        backup_root = self.backups / deployment_id
        stage_root = self.journals / f".stage-{deployment_id}"
        journal_path = self.journals / f"{deployment_id}.json"
        records: list[dict] = []

        # Capture the complete intended transaction before creating staging or backups.
        for rel, source in manifest.items():
            destination = root.joinpath(*PurePosixPath(rel).parts)
            self._assert_safe_destination(root, destination)
            original_hash = hash_at(root, rel)
            exists = original_hash is not None
            records.append({
                "path": rel,
                "source": str(source),
                "existed": exists,
                "original_hash": original_hash,
                "backup_path": str(backup_root.joinpath(*PurePosixPath(rel).parts)) if exists else None,
                "deployed_hash": file_hash(source),
                "mod_ids": list((owners or {}).get(rel, [])),
                "status": "planned",
            })

        payload = {
            "schema": 1,
            "id": deployment_id,
            "root": str(root),
            "profile_id": profile_id,
            "game_id": game_id,
            "adapter_id": adapter.game_id,
            "started_at": started,
            "updated_at": started,
            "status": "planned",
            "stage_root": str(stage_root),
            "backup_root": str(backup_root),
            "files": records,
            "recovery_history": [],
            "message": "",
        }
        write_json_durable(journal_path, payload)
        try:
            with self.db.connect() as db:
                db.execute("INSERT INTO deployments(id,profile_id,game_id,started_at,status,journal) VALUES(?,?,?,?,?,?)",
                           (deployment_id, profile_id, game_id, started, "planned", str(journal_path)))
                db.executemany("INSERT INTO deployment_files VALUES(?,?,?,?,?,?,?)", [
                    (deployment_id, r["path"], int(r["existed"]), r["original_hash"],
                     r["backup_path"], r["deployed_hash"], "planned") for r in records
                ])

            payload["status"] = "staging"
            self._persist(journal_path, payload, db_status="staging")
            stage_root.mkdir(mode=0o700)
            fsync_directory(self.journals)
            for record in records:
                staged = stage_root.joinpath(*PurePosixPath(record["path"]).parts)
                staged.parent.mkdir(parents=True, exist_ok=True)
                copy_durable(Path(record["source"]), staged, managed_root=stage_root,
                             managed_relative=record["path"])
                if hash_at(stage_root, record["path"]) != record["deployed_hash"]:
                    raise DeploymentError(f"Staging hash verification failed: {record['path']}")
                record["staged_path"] = str(staged)
                record["status"] = "staged"
                self._persist(journal_path, payload)

            payload["status"] = "backing_up"
            self._persist(journal_path, payload, db_status="backing_up")
            backup_root.mkdir(mode=0o700)
            fsync_directory(self.backups)
            for record in records:
                if record["existed"]:
                    destination = root.joinpath(*PurePosixPath(record["path"]).parts)
                    self._assert_safe_destination(root, destination)
                    if hash_at(root, record["path"]) != record["original_hash"]:
                        raise DeploymentError(f"Destination changed during backup: {record['path']}")
                    backup = Path(record["backup_path"])
                    self._assert_managed_path(backup, backup_root)
                    copy_durable(destination, backup, managed_root=self.backups,
                                 managed_relative=backup.relative_to(self.backups).as_posix(),
                                 source_root=root, source_relative=record["path"])
                    if hash_at(self.backups, backup.relative_to(self.backups).as_posix()) != record["original_hash"]:
                        raise DeploymentError(f"Backup verification failed: {record['path']}")
                    record["backup_verified"] = True
                record["status"] = "backed_up"
                self._persist(journal_path, payload)

            payload["status"] = "applying"
            self._persist(journal_path, payload, db_status="applying")
            for record in records:
                rel = record["path"]
                destination = root.joinpath(*PurePosixPath(rel).parts)
                self._assert_safe_destination(root, destination)
                # Refuse an intervening modification immediately before replacement.
                live_hash = hash_at(root, rel)
                if live_hash != record["original_hash"]:
                    raise DeploymentError(f"Destination changed before apply: {rel}")
                record["status"] = "apply_started"
                self._persist(journal_path, payload)
                replace_at(root, rel, Path(record["staged_path"]), create_parents=True,
                           source_root=stage_root, source_relative=rel)
                record["status"] = "applied"
                self._persist(journal_path, payload)

            payload["status"] = "verifying"
            self._persist(journal_path, payload, db_status="verifying")
            for record in records:
                destination = root.joinpath(*PurePosixPath(record["path"]).parts)
                self._assert_safe_destination(root, destination)
                if hash_at(root, record["path"]) != record["deployed_hash"]:
                    raise DeploymentError(f"Post-deployment verification failed: {record['path']}")
                record["status"] = "verified"
                self._persist(journal_path, payload)

            # Journal is the durable source of truth if the following SQLite commit fails.
            payload["status"] = "committed"
            payload["finished_at"] = datetime.now(timezone.utc).isoformat()
            self._persist(journal_path, payload)
            with self.db.connect() as db:
                db.execute("UPDATE deployments SET status='success',finished_at=? WHERE id=?",
                           (payload["finished_at"], deployment_id))
                db.execute("UPDATE deployment_files SET status='deployed' WHERE deployment_id=?", (deployment_id,))
                if game_id:
                    stamp = datetime.now(timezone.utc).isoformat()
                    db.executemany("""INSERT OR REPLACE INTO deployed_file_owners
                        (game_id,relative_path,deployment_id,profile_id,mod_ids,fingerprint,status,updated_at)
                        VALUES(?,?,?,?,?,?,'active',?)""",
                        [(game_id, record["path"], deployment_id, profile_id,
                          json.dumps(record.get("mod_ids", [])), record["deployed_hash"], stamp)
                         for record in records if record.get("mod_ids")])
            try:
                self._safe_cleanup_stage(stage_root)
            except OSError:
                log.exception("Deployment committed but staging cleanup failed: %s", stage_root)
            return deployment_id
        except Exception as exc:
            # Do not erase the journal or backup if automatic recovery is incomplete.
            payload["message"] = str(exc)
            if payload["status"] != "committed":
                payload["status"] = "recovery_required"
            try:
                self._persist(journal_path, payload, db_status=payload["status"])
            except Exception:
                log.exception("Could not persist failed deployment state for %s", deployment_id)
            recovery_status = None
            try:
                from modforge.core.recovery_manager import RecoveryManager
                recovery = RecoveryManager(self.db, self.backups, self.journals, self.lock_dir).recover(
                    deployment_id, root, adapter, confirmed=False, lock_held=True)
                recovery_status = recovery.get("status")
                if recovery.get("status") == "success":
                    return deployment_id
            except Exception:
                log.exception("Automatic recovery failed for deployment %s", deployment_id)
            final_status = payload.get("status")
            if final_status == "rolled_back" or recovery_status == "rolled_back":
                detail = "original filesystem state was restored"
            else:
                detail = "inspect Recovery; journal and backups were retained"
            raise DeploymentError(
                f"Deployment {deployment_id} did not complete: {exc}; {detail}."
            ) from exc

    def _persist(self, journal_path: Path, payload: dict, db_status: str | None = None) -> None:
        touch_journal(payload)
        write_json_durable(journal_path, payload)
        with self.db.connect() as db:
            if db_status:
                db.execute("UPDATE deployments SET status=?,message=? WHERE id=?",
                           (db_status, payload.get("message", ""), payload["id"]))
            for record in payload.get("files", []):
                db.execute("UPDATE deployment_files SET status=? WHERE deployment_id=? AND relative_path=?",
                           (record.get("status", payload.get("status")), payload["id"], record["path"]))

    @staticmethod
    def _assert_managed_path(path: Path, managed_root: Path) -> None:
        root = managed_root.resolve()
        if not path.absolute().is_relative_to(root):
            raise DeploymentError(f"Path escapes managed data directory: {path}")
        cursor = root
        for part in path.absolute().relative_to(root).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise DeploymentError(f"Symlink in managed data path: {cursor}")

    @staticmethod
    def _assert_safe_destination(root: Path, dest: Path) -> None:
        try:
            relative = dest.relative_to(root)
        except ValueError as exc:
            raise DeploymentError(f"Destination escapes game root: {dest}") from exc
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise DeploymentError(f"Symlink destination component refused: {cursor}")
        if not dest.resolve(strict=False).is_relative_to(root):
            raise DeploymentError(f"Destination escapes game root: {dest}")

    def _safe_cleanup_stage(self, stage_root: Path) -> None:
        self._assert_managed_path(stage_root, self.journals)
        if stage_root.name.startswith(".stage-") and stage_root.exists() and not stage_root.is_symlink():
            shutil.rmtree(stage_root)
            fsync_directory(self.journals)
