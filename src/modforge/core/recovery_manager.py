"""Inspection and explicit, conservative recovery for deployment journals."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from modforge.core.deployment_manager import DeploymentError, DeploymentManager
from modforge.core.journal import fsync_directory, touch_journal, write_json_durable
from modforge.core.safe_fs import hash_at, locked_file, remove_restore_temp, restore_at, unlink_at
from modforge.database import Database
from modforge.games.base import GameAdapter

log = logging.getLogger(__name__)
TERMINAL = {"committed", "rolled_back"}


class RecoveryError(DeploymentError):
    pass


class RecoveryManager:
    def __init__(self, db: Database, backups: Path, journals: Path, lock_dir: Path):
        self.db = db
        self.backups = backups.resolve()
        self.journals = journals.resolve()
        self.lock_dir = lock_dir.resolve()
        for directory in (backups, journals, lock_dir):
            if directory.is_symlink():
                raise RecoveryError(f"Managed application directory cannot be a symlink: {directory}")
            directory.mkdir(parents=True, exist_ok=True)
        for directory in (self.backups, self.journals, self.lock_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def unresolved(self, configured_root: Path | None = None) -> list[dict]:
        """List unresolved, corrupt, orphaned, and database/journal-mismatched operations."""
        items: list[dict] = []
        seen: set[str] = set()
        for path in sorted(self.journals.glob("*.json")):
            deployment_id = path.stem
            seen.add(deployment_id)
            try:
                payload = self._load(path)
                root = Path(payload["root"])
                mismatch = configured_root is not None and root.resolve() != configured_root.resolve()
                with self.db.connect() as db:
                    row = db.execute("SELECT status FROM deployments WHERE id=?", (deployment_id,)).fetchone()
                db_status = row["status"] if row else None
                missing_backups = []
                for record in payload.get("files", []):
                    backup_should_exist = payload["status"] == "committed" or record.get("backup_verified")
                    if record.get("existed") and record.get("backup_path") and backup_should_exist:
                        try:
                            if not self._validated_backup(payload, record).is_file():
                                missing_backups.append(record.get("path", "unknown"))
                        except Exception:
                            missing_backups.append(record.get("path", "unknown"))
                db_mismatch = (payload["status"] == "committed" and db_status != "success") or (
                    payload["status"] == "rolled_back" and db_status != "rolled_back")
                if payload["status"] not in TERMINAL or mismatch or db_mismatch or missing_backups:
                    items.append({
                        "id": deployment_id,
                        "started_at": payload.get("started_at", "unknown"),
                        "root": str(root),
                        "status": payload.get("status", "unknown"),
                        "files": [r.get("path", "<invalid path>") for r in payload.get("files", [])],
                        "database_status": db_status or "missing database record",
                        "issue": "configured game root differs" if mismatch else (
                            "journal/database state mismatch" if db_mismatch else (
                                "missing or unsafe backups: " + ", ".join(missing_backups)
                                if missing_backups else "incomplete journal")),
                        "journal": str(path),
                        "corrupt": False,
                    })
            except Exception as exc:
                items.append({"id": deployment_id, "started_at": "unknown", "root": "unknown",
                              "status": "journal_corrupt", "files": [], "database_status": "unknown",
                              "issue": str(exc), "journal": str(path), "corrupt": True})
        for stage in sorted(self.journals.glob(".stage-*")):
            deployment_id = stage.name.removeprefix(".stage-")
            if deployment_id not in seen:
                items.append({"id": deployment_id, "started_at": "unknown", "root": "unknown",
                              "status": "orphaned_staging", "files": [], "database_status": "unknown",
                              "issue": "staging directory has no journal", "journal": "missing", "corrupt": True})
        # Database rows without a journal are actionable too, but cannot be safely restored automatically.
        with self.db.connect() as db:
            rows = db.execute("SELECT id,started_at,status,journal FROM deployments").fetchall()
        for row in rows:
            if row["id"] not in seen:
                items.append({"id": row["id"], "started_at": row["started_at"], "root": "unknown",
                              "status": row["status"], "files": [], "database_status": row["status"],
                              "issue": "journal missing; filesystem state cannot be inferred safely",
                              "journal": row["journal"], "corrupt": True})
        return items

    def inspect(self, deployment_id: str, root: Path, adapter: GameAdapter) -> dict:
        payload, journal_path = self._get_journal(deployment_id)
        root = root.resolve(strict=True)
        self._validate_root(payload, root, adapter)
        results = []
        for record in payload["files"]:
            rel = adapter.validate_relative_path(record["path"])
            dest = root.joinpath(*PurePosixPath(rel).parts)
            DeploymentManager._assert_safe_destination(root, dest)
            current = hash_at(root, rel)
            backup_status = self._backup_status(payload, record)
            if record["existed"] and current == record["original_hash"]:
                condition = "already_original"
            elif not record["existed"] and current is None:
                condition = "already_original"
            elif current == record["deployed_hash"]:
                condition = "deployed_file"
            elif current is None:
                condition = "missing_external_or_not_applied"
            else:
                condition = "externally_modified"
            results.append({"path": rel, "condition": condition, "backup_status": backup_status,
                            "current_hash": current if isinstance(current, str) and len(current) == 64 else None,
                            "original_hash": record.get("original_hash"), "deployed_hash": record.get("deployed_hash")})
        return {"id": deployment_id, "status": payload["status"], "started_at": payload.get("started_at"),
                "root": str(root), "files": results, "journal": str(journal_path)}

    def recover(self, deployment_id: str, root: Path, adapter: GameAdapter,
                confirmed: bool = False, rollback_committed: bool = False,
                lock_held: bool = False) -> dict:
        """Restore original states; ambiguous replacements need explicit confirmation."""
        payload, journal_path = self._get_journal(deployment_id)
        root = root.resolve(strict=True)
        self._validate_root(payload, root, adapter)
        lock_path = self.lock_dir / (hashlib.sha256(str(root).encode()).hexdigest() + ".lock")
        with locked_file(lock_path, acquire=not lock_held):
            if payload["status"] == "committed" and rollback_committed:
                payload["status"] = "rolling_back"
                self._persist(payload, journal_path, db_status="rolling_back")
            elif payload["status"] in {"committed", "rolled_back"}:
                return self._reconcile_terminal(payload, journal_path, root, adapter)
            preview = self.inspect(deployment_id, root, adapter)
            needs_confirmation = [f["path"] for f in preview["files"] if f["condition"] in
                                  {"externally_modified", "missing_external_or_not_applied"}]
            if needs_confirmation and not confirmed:
                raise RecoveryError("Recovery has ambiguous files and needs confirmation: " + ", ".join(needs_confirmation))

            # Validate every backup needed for a restore before the first game-file mutation.
            for record, status in zip(payload["files"], preview["files"]):
                if record["existed"] and status["condition"] != "already_original":
                    if status["backup_status"] != "verified":
                        raise RecoveryError(f"Cannot restore {record['path']}: backup is {status['backup_status']}")
                if not record["existed"] and status["condition"] == "externally_modified":
                    raise RecoveryError(f"Refusing to remove externally modified unowned file: {record['path']}")

            payload.setdefault("recovery_history", []).append({
                "started_at": datetime.now(timezone.utc).isoformat(),
                "action": "restore_original_state",
                "confirmed_ambiguous": confirmed,
                "files": [],
            })
            recovery = payload["recovery_history"][-1]
            payload["status"] = "rolling_back"
            self._persist(payload, journal_path, db_status="rolling_back")
            errors = []
            for record, status in reversed(list(zip(payload["files"], preview["files"]))):
                rel = adapter.validate_relative_path(record["path"])
                dest = root.joinpath(*PurePosixPath(rel).parts)
                try:
                    DeploymentManager._assert_safe_destination(root, dest)
                    prior = next((entry for old in reversed(payload.get("recovery_history", [])[:-1])
                                  for entry in reversed(old.get("files", []))
                                  if entry.get("path") == rel and entry.get("temp_name")), None)
                    temp_name = prior["temp_name"] if prior else f".modforge-restore-{uuid.uuid4().hex}"
                    recovery["files"].append({"path": rel, "state": "restore_started", "temp_name": temp_name})
                    self._persist(payload, journal_path)
                    if status["condition"] == "already_original":
                        if record["existed"]:
                            remove_restore_temp(root, rel, temp_name, record["original_hash"])
                        outcome = "preserved_original"
                    elif record["existed"]:
                        backup = self._validated_backup(payload, record)
                        restore_at(root, rel, backup, temp_name, record["original_hash"],
                                   source_root=self.backups,
                                   source_relative=backup.relative_to(self.backups).as_posix())
                        if hash_at(root, rel) != record["original_hash"]:
                            raise RecoveryError(f"Restored hash verification failed: {rel}")
                        outcome = "restored_backup"
                    elif status["condition"] == "deployed_file":
                        DeploymentManager._assert_safe_destination(root, dest)
                        if hash_at(root, rel) != record["deployed_hash"]:
                            raise RecoveryError(f"Created file changed before removal: {rel}")
                        unlink_at(root, rel, record["deployed_hash"])
                        outcome = "removed_mod_created_file"
                    else:
                        outcome = "left_unresolved"
                        raise RecoveryError(f"Refusing unsafe removal of unknown file: {rel}")
                    recovery["files"][-1]["state"] = outcome
                    self._persist(payload, journal_path)
                except Exception as exc:
                    errors.append(f"{rel}: {exc}")
                    if recovery["files"] and recovery["files"][-1].get("path") == rel:
                        recovery["files"][-1]["state"] = "unresolved"
                        recovery["files"][-1]["error"] = str(exc)
                    self._persist(payload, journal_path)

            if errors:
                payload["status"] = "recovery_required"
                payload["message"] = "; ".join(errors)
                recovery["finished_at"] = datetime.now(timezone.utc).isoformat()
                recovery["errors"] = errors
                self._persist(payload, journal_path, db_status="recovery_required")
                raise RecoveryError("Recovery incomplete; data was retained. " + "; ".join(errors))

            payload["status"] = "rolled_back"
            payload["finished_at"] = datetime.now(timezone.utc).isoformat()
            recovery["finished_at"] = payload["finished_at"]
            recovery["errors"] = []
            self._persist(payload, journal_path)
            self._reconcile_terminal(payload, journal_path, root, adapter)
            return {"id": deployment_id, "status": "rolled_back", "restored": recovery["files"], "errors": []}

    def _reconcile_terminal(self, payload: dict, journal_path: Path, root: Path,
                            adapter: GameAdapter) -> dict:
        expected_status = "success" if payload["status"] == "committed" else "rolled_back"
        for record in payload["files"]:
            dest = root.joinpath(*PurePosixPath(adapter.validate_relative_path(record["path"])).parts)
            DeploymentManager._assert_safe_destination(root, dest)
            expected = record["deployed_hash"] if expected_status == "success" else record.get("original_hash")
            current = hash_at(root, adapter.validate_relative_path(record["path"]))
            if expected is None:
                if current is not None:
                    raise RecoveryError(f"Cannot reconcile {record['path']}: expected the file to be absent")
            elif current != expected:
                raise RecoveryError(f"Cannot reconcile {record['path']}: live file does not match journal")
        with self.db.connect() as db:
            row = db.execute("SELECT id FROM deployments WHERE id=?", (payload["id"],)).fetchone()
            finished = payload.get("finished_at", payload.get("started_at"))
            if not row:
                db.execute("INSERT INTO deployments(id,profile_id,started_at,finished_at,status,journal,message,game_id) VALUES(?,?,?,?,?,?,?,?)",
                           (payload["id"], payload.get("profile_id"), payload.get("started_at", finished),
                            finished, expected_status, str(journal_path), "reconciled from durable journal",payload.get("game_id")))
            else:
                db.execute("UPDATE deployments SET status=?,finished_at=? WHERE id=?",
                           (expected_status, finished, payload["id"]))
            file_status = "deployed" if expected_status == "success" else "rolled_back"
            db.executemany("INSERT OR IGNORE INTO deployment_files VALUES(?,?,?,?,?,?,?)", [
                (payload["id"], record["path"], int(record["existed"]), record.get("original_hash"),
                 record.get("backup_path"), record.get("deployed_hash"), file_status)
                for record in payload["files"]
            ])
            db.execute("UPDATE deployment_files SET status=? WHERE deployment_id=?",
                       (file_status, payload["id"]))
            db.execute("UPDATE deployed_file_owners SET status=?,updated_at=datetime('now') WHERE deployment_id=?",
                       ("active" if expected_status == "success" else "rolled_back", payload["id"]))
            if expected_status == "success" and payload.get("game_id"):
                db.executemany("""INSERT OR REPLACE INTO deployed_file_owners
                    (game_id,relative_path,deployment_id,profile_id,mod_ids,fingerprint,status,updated_at)
                    VALUES(?,?,?,?,?,?,'active',datetime('now'))""",
                    [(payload["game_id"],record["path"],payload["id"],payload.get("profile_id"),
                      json.dumps(record.get("mod_ids",[])),record["deployed_hash"])
                     for record in payload["files"] if record.get("mod_ids")])
        self._cleanup_stage(payload)
        return {"id": payload["id"], "status": expected_status, "restored": [], "errors": []}

    def _backup_status(self, payload: dict, record: dict) -> str:
        if not record.get("existed"):
            return "not_required"
        try:
            backup = self._validated_backup(payload, record)
            if not backup.is_file() or backup.is_symlink():
                return "missing"
            relative = backup.relative_to(self.backups).as_posix()
            return "verified" if hash_at(self.backups, relative) == record.get("original_hash") else "corrupt"
        except Exception:
            return "unsafe_path"

    def _validated_backup(self, payload: dict, record: dict) -> Path:
        backup_root = Path(payload["backup_root"]).absolute()
        backup = Path(record["backup_path"]).absolute()
        if not backup_root.is_relative_to(self.backups) or not backup.is_relative_to(backup_root):
            raise RecoveryError(f"Backup path escapes ModForge backup storage: {backup}")
        cursor = self.backups
        for part in backup.relative_to(self.backups).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise RecoveryError(f"Symlink in backup path: {cursor}")
        return backup

    def _get_journal(self, deployment_id: str) -> tuple[dict, Path]:
        journal_path = (self.journals / f"{deployment_id}.json").resolve()
        if not journal_path.is_relative_to(self.journals) or not journal_path.is_file():
            raise RecoveryError("Deployment journal is missing or outside journal storage")
        payload = self._load(journal_path)
        if payload.get("id") != deployment_id or not isinstance(payload.get("files"), list):
            raise RecoveryError("Journal identity or file list is invalid")
        return payload, journal_path

    def _load(self, path: Path) -> dict:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RecoveryError("Unsupported or invalid journal format")
        # Read journals written by the first ModForge release (schema-less format).
        if "schema" not in payload:
            old_status = payload.get("status", "unknown")
            payload["schema"] = 1
            payload.setdefault("started_at", payload.get("updated_at", "unknown"))
            payload.setdefault("stage_root", str(self.journals / f".stage-{payload.get('id', 'unknown')}"))
            payload.setdefault("backup_root", str(self.backups / str(payload.get("id", "unknown"))))
            payload.setdefault("recovery_history", [])
            payload["status"] = {
                "success": "committed", "failed_recovered": "rolled_back",
                "preparing": "backing_up", "deploying": "applying",
            }.get(old_status, old_status)
        if payload.get("schema") != 1:
            raise RecoveryError("Unsupported or invalid journal format")
        return payload

    def _validate_root(self, payload: dict, root: Path, adapter: GameAdapter) -> None:
        ok, reason = adapter.validate_root(root)
        if not ok:
            raise RecoveryError(reason)
        journal_root = Path(payload["root"]).resolve(strict=True)
        if journal_root != root:
            raise RecoveryError("Configured game root differs from the journal")
        stage_root = Path(payload["stage_root"]).absolute()
        backup_root = Path(payload["backup_root"]).absolute()
        if not stage_root.is_relative_to(self.journals) or not stage_root.name.startswith(".stage-"):
            raise RecoveryError("Staging path escapes journal storage")
        if not backup_root.is_relative_to(self.backups):
            raise RecoveryError("Backup root escapes ModForge data storage")
        for record in payload["files"]:
            adapter.validate_relative_path(record["path"])
            if not isinstance(record.get("existed"), bool):
                raise RecoveryError("Journal has an invalid pre-deployment state")
            if not isinstance(record.get("deployed_hash"), str):
                raise RecoveryError("Journal is missing an expected deployed hash")

    def _persist(self, payload: dict, journal_path: Path, db_status: str | None = None) -> None:
        touch_journal(payload)
        write_json_durable(journal_path, payload)
        if db_status:
            try:
                with self.db.connect() as db:
                    db.execute("UPDATE deployments SET status=?,message=? WHERE id=?",
                               (db_status, payload.get("message", ""), payload["id"]))
                    file_status = "deployed" if db_status == "success" else db_status
                    db.execute("UPDATE deployment_files SET status=? WHERE deployment_id=?",
                               (file_status, payload["id"]))
            except Exception:
                log.exception("Journal state persisted but SQLite state update failed for %s", payload["id"])

    def _cleanup_stage(self, payload: dict) -> None:
        stage = Path(payload["stage_root"]).absolute()
        if not stage.is_relative_to(self.journals) or not stage.name.startswith(".stage-"):
            raise RecoveryError("Refusing unsafe staging cleanup")
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
            fsync_directory(self.journals)
