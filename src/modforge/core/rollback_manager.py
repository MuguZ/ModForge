"""Rollback entry point sharing the durable recovery implementation."""
from pathlib import Path

from modforge.core.deployment_manager import DeploymentError
from modforge.core.recovery_manager import RecoveryManager
from modforge.database import Database
from modforge.games.base import GameAdapter


class RollbackManager:
    def __init__(self, db: Database, lock_dir: Path):
        self.db = db
        self.lock_dir = lock_dir

    def rollback(self, deployment_id: str, root: Path, adapter: GameAdapter,
                 confirm_external_changes: bool = False) -> None:
        with self.db.connect() as db:
            deployment = db.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
            if deployment is None:
                raise DeploymentError("Deployment was not found")
            if deployment["status"] == "rolled_back":
                return
            if deployment["status"] not in {"success", "planned", "staging", "backing_up", "applying", "verifying", "rolling_back", "recovery_required"}:
                raise DeploymentError(f"Deployment status {deployment['status']} is not eligible for rollback")
            later = db.execute("""SELECT COUNT(*) FROM deployment_files older
                JOIN deployment_files newer ON lower(newer.relative_path)=lower(older.relative_path)
                JOIN deployments d ON d.id=newer.deployment_id
                JOIN deployments old_deployment ON old_deployment.id=older.deployment_id
                WHERE older.deployment_id=? AND d.started_at>(SELECT started_at FROM deployments WHERE id=?)
                AND (old_deployment.game_id IS NULL OR d.game_id IS NULL OR old_deployment.game_id=d.game_id)
                AND d.status NOT IN ('rolled_back','failed_recovered')""", (deployment_id, deployment_id)).fetchone()[0]
        if later:
            raise DeploymentError("A later deployment overlaps these files; roll back newer deployments first")
        journal = Path(deployment["journal"])
        # ModForge stores journals and backups as siblings under the data directory.
        backups = journal.parent.parent / "backups"
        journals = journal.parent
        manager = RecoveryManager(self.db, backups, journals, self.lock_dir)
        manager.recover(deployment_id, root, adapter, confirmed=confirm_external_changes,
                        rollback_committed=True)
