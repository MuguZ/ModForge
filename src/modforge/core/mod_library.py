from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import stat
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

    def mappings(self, game_id: str, mod_id: str) -> dict[str, str]:
        """Return source-relative to destination-relative paths for a game."""
        with self.db.connect() as db:
            sources = [row[0] for row in db.execute(
                "SELECT relative_path FROM mod_files WHERE mod_id=? ORDER BY relative_path", (mod_id,))]
            saved = {row["source_path"]: row["destination_path"] for row in db.execute(
                "SELECT source_path,destination_path FROM mod_mappings WHERE game_id=? AND mod_id=?",
                (game_id, mod_id))}
        if not sources:
            raise ValueError("Mod has no imported files")
        return {source: saved.get(source, source) for source in sources}

    def set_mappings(self, game_id: str, mod_id: str, mapping: dict[str, str], adapter) -> None:
        from datetime import datetime, timezone
        normalized = {}
        destinations = set()
        with self.db.connect() as db:
            sources = {row[0] for row in db.execute(
                "SELECT relative_path FROM mod_files WHERE mod_id=?", (mod_id,))}
            if not sources:
                raise ValueError("Mod has no imported files")
            if set(mapping) != sources:
                raise ValueError("Mapping must include every imported file exactly once")
            for source, destination in mapping.items():
                destination = adapter.validate_relative_path(destination)
                key = destination.casefold()
                if key in destinations:
                    raise ValueError(f"Two files map to the same destination: {destination}")
                destinations.add(key)
                normalized[source] = destination
            if not db.execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
                raise ValueError("Target game registration does not exist")
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.execute("DELETE FROM mod_mappings WHERE game_id=? AND mod_id=?", (game_id, mod_id))
            db.executemany("INSERT INTO mod_mappings(game_id,mod_id,source_path,destination_path,updated_at) VALUES(?,?,?,?,?)",
                           [(game_id, mod_id, source, destination, stamp) for source, destination in normalized.items()])

    def mapped_manifest(self, profile_id: str, game_id: str) -> dict[str, Path]:
        """Build a mapped deployment manifest, rejecting destination collisions."""
        with self.db.connect() as db:
            rows = db.execute("""SELECT m.id,m.extracted_path,m.name,mf.relative_path,
                COALESCE(mm.destination_path,mf.relative_path) destination_path
                FROM profile_mods pm JOIN profiles p ON p.id=pm.profile_id
                JOIN mods m ON m.id=pm.mod_id JOIN mod_files mf ON mf.mod_id=m.id
                LEFT JOIN mod_mappings mm ON mm.game_id=? AND mm.mod_id=m.id AND mm.source_path=mf.relative_path
                WHERE pm.profile_id=? AND (p.game_id=? OR p.game_id IS NULL) ORDER BY m.name,mf.relative_path""",
                (game_id, profile_id, game_id)).fetchall()
        result = {}
        for row in rows:
            destination = row["destination_path"]
            key = destination.casefold()
            if key in result:
                raise ValueError(f"Multiple enabled files target {destination}")
            source = Path(row["extracted_path"]) / row["relative_path"]
            managed_root = Path(row["extracted_path"]).resolve(strict=True)
            try:
                relative = source.relative_to(Path(row["extracted_path"]))
            except ValueError as exc:
                raise ValueError(f"Imported source path escapes its managed directory: {source}") from exc
            cursor = managed_root
            unsafe = False
            for part in relative.parts:
                cursor = cursor / part
                try:
                    mode = cursor.lstat().st_mode
                except OSError:
                    unsafe = True
                    break
                if stat.S_ISLNK(mode):
                    unsafe = True
                    break
            if unsafe or not cursor.is_file() or not cursor.resolve(strict=True).is_relative_to(managed_root):
                raise ValueError(f"Imported source file is missing or unsafe: {source}")
            result[destination] = source
        return result
