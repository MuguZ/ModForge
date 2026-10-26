from __future__ import annotations
import uuid
from datetime import datetime, timezone
from modforge.database import Database


class ProfileManager:
    def __init__(self, database: Database):
        self.db = database

    def list_profiles(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM profiles ORDER BY name")]

    def create(self, name: str, source_id: str | None = None) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Profile name cannot be empty")
        profile_id = str(uuid.uuid4())
        with self.db.connect() as db:
            db.execute("INSERT INTO profiles VALUES(?,?,?)", (profile_id, name, datetime.now(timezone.utc).isoformat()))
            if source_id:
                db.execute("INSERT INTO profile_mods SELECT ?,mod_id FROM profile_mods WHERE profile_id=?", (profile_id, source_id))
        return profile_id

    def rename(self, profile_id: str, name: str) -> None:
        if profile_id == "default":
            raise ValueError("The default profile cannot be renamed")
        with self.db.connect() as db:
            db.execute("UPDATE profiles SET name=? WHERE id=?", (name.strip(), profile_id))

    def delete(self, profile_id: str, active_id: str) -> None:
        if profile_id == "default":
            raise ValueError("The default profile cannot be deleted")
        if profile_id == active_id:
            raise ValueError("Select another profile before deleting the active profile")
        with self.db.connect() as db:
            db.execute("DELETE FROM profiles WHERE id=?", (profile_id,))

    def set_enabled(self, profile_id: str, mod_id: str, enabled: bool) -> None:
        with self.db.connect() as db:
            if enabled:
                db.execute("INSERT OR IGNORE INTO profile_mods VALUES(?,?)", (profile_id, mod_id))
            else:
                db.execute("DELETE FROM profile_mods WHERE profile_id=? AND mod_id=?", (profile_id, mod_id))
