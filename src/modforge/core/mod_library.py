from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from modforge.core.archive_importer import ArchiveImporter
from modforge.database import Database


class ModLibrary:
    def __init__(self, db: Database, importer: ArchiveImporter):
        self.db, self.importer = db, importer

    def import_archive(self, archive: Path) -> str:
        # Hash first so duplicate imports do not create orphaned extracted copies.
        from modforge.core.archive_importer import sha256_file, ArchiveError
        digest = sha256_file(archive)
        with self.db.connect() as db:
            exists = db.execute("SELECT id FROM mods WHERE archive_hash=?", (digest,)).fetchone()
        if exists:
            raise ArchiveError("This archive is already in the library")
        result = self.importer.import_zip(archive)
        try:
            with self.db.connect() as db:
                db.execute("INSERT INTO mods(id,name,archive,archive_hash,imported_at,extracted_path) VALUES(?,?,?,?,?,?)",
                           (result.id, result.name, str(result.archive), result.archive_hash,
                            datetime.now(timezone.utc).isoformat(), str(result.extracted_path)))
                db.executemany("INSERT INTO mod_files VALUES(?,?,?,?)",
                               [(result.id, item.path, item.size, item.sha256) for item in result.files])
        except Exception:
            import shutil
            shutil.rmtree(result.extracted_path, ignore_errors=True)
            result.archive.unlink(missing_ok=True)
            raise
        return result.id

    def list_mods(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM mods ORDER BY imported_at DESC")]

    def file_manifest(self, profile_id: str) -> dict[str, Path]:
        with self.db.connect() as db:
            rows = db.execute("""SELECT mf.relative_path,m.extracted_path FROM profile_mods pm
                JOIN mods m ON m.id=pm.mod_id JOIN mod_files mf ON mf.mod_id=m.id
                WHERE pm.profile_id=? ORDER BY m.name""", (profile_id,)).fetchall()
        return {row["relative_path"]: Path(row["extracted_path"]) / row["relative_path"] for row in rows}

    def enabled_mod_ids(self, profile_id: str) -> list[str]:
        with self.db.connect() as db:
            return [r[0] for r in db.execute("SELECT mod_id FROM profile_mods WHERE profile_id=?", (profile_id,))]
