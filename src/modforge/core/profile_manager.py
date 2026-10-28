from __future__ import annotations
import uuid
from datetime import datetime, timezone
from modforge.database import Database


class ProfileManager:
    def __init__(self, database: Database):
        self.db = database

    def list_profiles(self, game_id: str | None = None) -> list[dict]:
        with self.db.connect() as db:
            if game_id is None:
                rows = db.execute("SELECT * FROM profiles ORDER BY name")
            else:
                rows = db.execute("SELECT * FROM profiles WHERE game_id=? ORDER BY is_default DESC,name", (game_id,))
            profiles = [dict(row) for row in rows]
            for profile in profiles:
                if profile.get("is_default"):
                    profile["name"] = "Default"
                elif profile.get("game_id"):
                    suffix = f" [{profile['game_id']}]"
                    if profile["name"].endswith(suffix):
                        profile["name"] = profile["name"][:-len(suffix)]
            return profiles

    def create(self, name: str, source_id: str | None = None, game_id: str | None = None) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Profile name cannot be empty")
        profile_id = str(uuid.uuid4())
        with self.db.connect() as db:
            if source_id:
                source = db.execute("SELECT game_id FROM profiles WHERE id=?", (source_id,)).fetchone()
                if not source:
                    raise ValueError("Source profile does not exist")
                if game_id is None:
                    game_id = source["game_id"]
                if source["game_id"] != game_id:
                    raise ValueError("Profiles can only be duplicated within the same game")
            if game_id and not db.execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
                raise ValueError("Game installation does not exist")
            stored_name = f"{name} [{game_id}]" if game_id else name
            db.execute("INSERT INTO profiles(id,name,created_at,game_id,is_default) VALUES(?,?,?,?,0)",
                       (profile_id, stored_name, datetime.now(timezone.utc).isoformat(), game_id))
            if source_id:
                db.execute("INSERT INTO profile_mods SELECT ?,mod_id FROM profile_mods WHERE profile_id=?", (profile_id, source_id))
        return profile_id

    def rename(self, profile_id: str, name: str) -> None:
        if profile_id == "default":
            raise ValueError("The default profile cannot be renamed")
        with self.db.connect() as db:
            profile=db.execute("SELECT game_id FROM profiles WHERE id=? AND is_default=0",(profile_id,)).fetchone()
            if not profile:
                raise ValueError("The default profile cannot be renamed or profile was not found")
            value=name.strip()
            if not value: raise ValueError("Profile name cannot be empty")
            if profile["game_id"]: value=f"{value} [{profile['game_id']}]"
            changed = db.execute("UPDATE profiles SET name=? WHERE id=? AND is_default=0", (value, profile_id)).rowcount
            if not changed:
                raise ValueError("The default profile cannot be renamed or profile was not found")

    def delete(self, profile_id: str, active_id: str) -> None:
        with self.db.connect() as db:
            row = db.execute("SELECT is_default FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise ValueError("Profile was not found")
        if row["is_default"] or profile_id == "default":
            raise ValueError("The default profile cannot be deleted")
        if profile_id == active_id:
            raise ValueError("Select another profile before deleting the active profile")
        with self.db.connect() as db:
            db.execute("DELETE FROM profiles WHERE id=?", (profile_id,))

    def set_enabled(self, profile_id: str, mod_id: str, enabled: bool) -> None:
        with self.db.connect() as db:
            if not db.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
                raise ValueError("Profile does not exist")
            if not db.execute("SELECT 1 FROM mods WHERE id=?", (mod_id,)).fetchone():
                raise ValueError("Mod does not exist")
            if enabled:
                db.execute("INSERT OR IGNORE INTO profile_mods VALUES(?,?)", (profile_id, mod_id))
            else:
                db.execute("DELETE FROM profile_mods WHERE profile_id=? AND mod_id=?", (profile_id, mod_id))
