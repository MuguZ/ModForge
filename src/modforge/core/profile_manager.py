from __future__ import annotations
import uuid
import re
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
        if not name or len(name)>160:
            raise ValueError("Profile name must contain between 1 and 160 characters")
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
            if not value or len(value)>160: raise ValueError("Profile name must contain between 1 and 160 characters")
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

    def export_definition(self, profile_id: str) -> dict:
        with self.db.connect() as db:
            profile=db.execute("SELECT id,name,game_id,is_default FROM profiles WHERE id=?",(profile_id,)).fetchone()
            if not profile: raise ValueError("Profile was not found")
            mods=db.execute("""SELECT m.id,m.name,m.archive_hash FROM profile_mods pm
                JOIN mods m ON m.id=pm.mod_id WHERE pm.profile_id=? ORDER BY m.name""",(profile_id,)).fetchall()
        return {"schema":1,"profile":{"name":"Default" if profile["is_default"] else self._display_name(profile["name"],profile["game_id"]),
                "game_id":profile["game_id"]},"mods":[dict(row) for row in mods]}

    def import_definition(self, definition: dict, game_id: str, name: str | None = None) -> str:
        if not isinstance(definition,dict) or type(definition.get("schema")) is not int or definition.get("schema")!=1:
            raise ValueError("Unsupported profile file format")
        profile_data=definition.get("profile")
        mods=definition.get("mods")
        if not isinstance(profile_data,dict) or not isinstance(mods,list) or len(mods)>10_000:
            raise ValueError("Profile file has invalid fields or too many mods")
        profile_name=name or profile_data.get("name")
        if not isinstance(profile_name,str) or not profile_name.strip() or len(profile_name)>160:
            raise ValueError("Profile file has an invalid name")
        if any(not isinstance(row,dict) or not isinstance(row.get("id"),str) or len(row["id"])>128
               or not isinstance(row.get("archive_hash"),str) or not re.fullmatch(r"[0-9a-f]{64}",row["archive_hash"])
               for row in mods):
            raise ValueError("Profile file contains an invalid mod reference")
        resolved=[]
        with self.db.connect() as db:
            if not db.execute("SELECT 1 FROM games WHERE id=?",(game_id,)).fetchone():
                raise ValueError("Target game installation does not exist")
            for ref in mods:
                row=db.execute("SELECT id FROM mods WHERE id=? AND archive_hash=?",(ref["id"],ref["archive_hash"])).fetchone()
                if not row:
                    matches=db.execute("SELECT id FROM mods WHERE archive_hash=?",(ref["archive_hash"],)).fetchall()
                    if len(matches)!=1:
                        raise ValueError(f"Mod archive is missing or ambiguous: {ref.get('name','Unknown mod')}")
                    row=matches[0]
                if row["id"] in resolved:
                    raise ValueError("Profile file contains duplicate mod references")
                resolved.append(row["id"])
            profile_id=str(uuid.uuid4())
            stored=f"{profile_name.strip()} [{game_id}]"
            db.execute("INSERT INTO profiles(id,name,created_at,game_id,is_default) VALUES(?,?,?,?,0)",
                       (profile_id,stored,datetime.now(timezone.utc).isoformat(),game_id))
            db.executemany("INSERT INTO profile_mods(profile_id,mod_id) VALUES(?,?)",
                           [(profile_id,mod_id) for mod_id in resolved])
        return profile_id

    def compare(self, first_id: str, second_id: str) -> dict:
        with self.db.connect() as db:
            profiles=db.execute("SELECT id,name,game_id,is_default FROM profiles WHERE id IN (?,?)",(first_id,second_id)).fetchall()
            if len(profiles)!=2: raise ValueError("Choose two existing profiles")
            by_id={row["id"]:row for row in profiles}
            if by_id[first_id]["game_id"]!=by_id[second_id]["game_id"]:
                raise ValueError("Profiles from different games cannot be compared")
            first={row[0] for row in db.execute("SELECT mod_id FROM profile_mods WHERE profile_id=?",(first_id,))}
            second={row[0] for row in db.execute("SELECT mod_id FROM profile_mods WHERE profile_id=?",(second_id,))}
            added=second-first; removed=first-second
            ids=added|removed
            names={row["id"]:row["name"] for row in db.execute(
                f"SELECT id,name FROM mods WHERE id IN ({','.join('?' for _ in ids)})",tuple(ids))} if ids else {}
        return {"added":[names[mid] for mid in sorted(added)],"removed":[names[mid] for mid in sorted(removed)]}

    @staticmethod
    def _display_name(name: str, game_id: str | None) -> str:
        suffix=f" [{game_id}]" if game_id else ""
        return name[:-len(suffix)] if suffix and name.endswith(suffix) else name
