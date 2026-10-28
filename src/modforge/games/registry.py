"""Persistent registrations for game installations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from modforge.games.generic import GenericFileAdapter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GameRegistry:
    def __init__(self, database):
        self.db = database
        # Phase-1 registrations predate game-scoped profiles. Add a default profile
        # without changing their IDs or existing profile membership.
        now = _now()
        with self.db.connect() as db:
            for game in db.execute("SELECT id FROM games"):
                exists = db.execute("SELECT 1 FROM profiles WHERE game_id=? AND is_default=1", (game["id"],)).fetchone()
                if not exists:
                    profile_id = "default" if game["id"] == "legacy-cyberpunk2077" else f"default-{game['id']}"
                    profile_name = "Default" if profile_id == "default" else f"Default ({game['id'][:8]})"
                    db.execute("INSERT OR IGNORE INTO profiles(id,name,created_at,game_id,is_default) VALUES(?,?,?,?,1)",
                               (profile_id, profile_name, now, game["id"]))

    def list_games(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM games ORDER BY name COLLATE NOCASE, root")]

    def get(self, game_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
            return dict(row) if row else None

    def register(self, name: str, root: Path | str, *, executable: str | None = None,
                 launcher: str | None = None, platform: str = "linux", source: str = "manual",
                 strategy: str = "copy", adapter_id: str | None = None) -> str:
        name = " ".join(name.split())
        if not name or len(name) > 160:
            raise ValueError("Enter a game name between 1 and 160 characters")
        if strategy not in {"copy", "symlink", "hardlink", "adapter"}:
            raise ValueError("Unsupported deployment strategy")
        path = Path(root).expanduser()
        if not path.is_absolute():
            raise ValueError("Installation directory must be an absolute path")
        normalized = str(path.resolve(strict=False))
        now = _now()
        with self.db.connect() as db:
            duplicate = db.execute("SELECT id,name FROM games WHERE root=?", (normalized,)).fetchone()
            if duplicate:
                raise ValueError(f"This installation is already registered as {duplicate['name']}")
            game_id = str(uuid.uuid4())
            adapter_id = adapter_id or GenericFileAdapter.game_id
            db.execute("""INSERT INTO games(id,name,root,executable,launcher,platform,source,strategy,
                       adapter_id,validation_status,validation_message,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,'unvalidated','',?,?)""",
                       (game_id,name,normalized,executable,launcher,platform,source,strategy,adapter_id,now,now))
            profile_id = f"default-{game_id}"
            db.execute("INSERT INTO profiles(id,name,created_at,game_id,is_default) VALUES(?,?,?,?,1)",
                       (profile_id, f"Default ({game_id[:8]})", now, game_id))
        self.validate(game_id)
        return game_id

    def edit(self, game_id: str, **changes) -> None:
        allowed = {"name", "root", "executable", "launcher", "platform", "strategy", "adapter_id"}
        if not changes or set(changes) - allowed:
            raise ValueError("Invalid game registration fields")
        if "name" in changes:
            changes["name"] = " ".join(str(changes["name"]).split())
            if not changes["name"] or len(changes["name"]) > 160:
                raise ValueError("Enter a valid game name")
        if "root" in changes:
            path = Path(changes["root"]).expanduser()
            if not path.is_absolute():
                raise ValueError("Installation directory must be an absolute path")
            changes["root"] = str(path.resolve(strict=False))
        if "strategy" in changes and changes["strategy"] not in {"copy", "symlink", "hardlink", "adapter"}:
            raise ValueError("Unsupported deployment strategy")
        with self.db.connect() as db:
            row = db.execute("SELECT id FROM games WHERE id=?", (game_id,)).fetchone()
            if not row:
                raise ValueError("Game registration no longer exists")
            if "root" in changes:
                duplicate = db.execute("SELECT id FROM games WHERE root=? AND id<>?", (changes["root"],game_id)).fetchone()
                if duplicate:
                    raise ValueError("This installation is already registered")
            assignments = ",".join(f"{key}=?" for key in changes)
            db.execute(f"UPDATE games SET {assignments},updated_at=? WHERE id=?", (*changes.values(),_now(),game_id))
        self.validate(game_id)

    def validate(self, game_id: str) -> tuple[bool, str]:
        game = self.get(game_id)
        if game is None:
            raise ValueError("Game registration no longer exists")
        adapter = GenericFileAdapter()
        if game["adapter_id"] == "cyberpunk2077":
            from modforge.games.cyberpunk2077 import Cyberpunk2077Adapter
            adapter = Cyberpunk2077Adapter()
        valid, message = adapter.validate_root(Path(game["root"]))
        with self.db.connect() as db:
            db.execute("UPDATE games SET validation_status=?,validation_message=?,last_scanned_at=?,updated_at=? WHERE id=?",
                       ("valid" if valid else "missing_or_invalid",message,_now(),_now(),game_id))
        return valid, message

    def remove(self, game_id: str) -> None:
        with self.db.connect() as db:
            # Keep profile, mapping, deployment, and recovery relationships intact.
            db.execute("UPDATE games SET enabled=0,updated_at=? WHERE id=?", (_now(), game_id))

    def restore(self, game_id: str) -> None:
        with self.db.connect() as db:
            db.execute("UPDATE games SET enabled=1,updated_at=? WHERE id=?", (_now(), game_id))

    def adapter_for(self, game_id: str):
        game = self.get(game_id)
        if not game:
            raise ValueError("Select a registered game")
        return self.adapter_by_id(game["adapter_id"])

    @staticmethod
    def adapter_by_id(adapter_id: str | None):
        if adapter_id == "cyberpunk2077":
            from modforge.games.cyberpunk2077 import Cyberpunk2077Adapter
            return Cyberpunk2077Adapter()
        return GenericFileAdapter()
