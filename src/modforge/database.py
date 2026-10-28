"""SQLite schema and compact data access layer."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database version {version} is newer than this app")
            if version < 1:
                db.executescript("""
                    CREATE TABLE games(id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT);
                    CREATE TABLE mods(id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT,
                      author TEXT, description TEXT, archive TEXT NOT NULL, archive_hash TEXT NOT NULL UNIQUE,
                      imported_at TEXT NOT NULL, extracted_path TEXT NOT NULL, notes TEXT DEFAULT '');
                    CREATE TABLE mod_files(mod_id TEXT NOT NULL REFERENCES mods(id) ON DELETE CASCADE,
                      relative_path TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                      PRIMARY KEY(mod_id, relative_path));
                    CREATE TABLE profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                    CREATE TABLE profile_mods(profile_id TEXT REFERENCES profiles(id) ON DELETE CASCADE,
                      mod_id TEXT REFERENCES mods(id) ON DELETE CASCADE, PRIMARY KEY(profile_id,mod_id));
                    CREATE TABLE deployments(id TEXT PRIMARY KEY, profile_id TEXT, started_at TEXT NOT NULL,
                      finished_at TEXT, status TEXT NOT NULL, journal TEXT NOT NULL, message TEXT DEFAULT '');
                    CREATE TABLE deployment_files(deployment_id TEXT REFERENCES deployments(id),
                      relative_path TEXT NOT NULL, existed INTEGER NOT NULL, original_hash TEXT,
                      backup_path TEXT, deployed_hash TEXT, status TEXT NOT NULL,
                      PRIMARY KEY(deployment_id,relative_path));
                    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    PRAGMA user_version=1;
                """)
                db.execute("INSERT INTO profiles(id,name,created_at) VALUES('default','Default',datetime('now'))")
                version = 1
            if version < 2:
                # Extend the original game table in place so existing deployments and
                # user registrations retain their IDs and relationships.
                columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
                additions = {
                    "executable": "TEXT", "launcher": "TEXT", "platform": "TEXT NOT NULL DEFAULT 'linux'",
                    "source": "TEXT NOT NULL DEFAULT 'manual'", "strategy": "TEXT NOT NULL DEFAULT 'copy'",
                    "adapter_id": "TEXT", "enabled": "INTEGER NOT NULL DEFAULT 1",
                    "validation_status": "TEXT NOT NULL DEFAULT 'unvalidated'",
                    "validation_message": "TEXT NOT NULL DEFAULT ''", "last_scanned_at": "TEXT",
                    "created_at": "TEXT NOT NULL DEFAULT ''", "updated_at": "TEXT NOT NULL DEFAULT ''",
                }
                for column, declaration in additions.items():
                    if column not in columns:
                        db.execute(f"ALTER TABLE games ADD COLUMN {column} {declaration}")
                db.execute("CREATE INDEX IF NOT EXISTS idx_games_root ON games(root)")
                db.execute("UPDATE games SET created_at=datetime('now') WHERE created_at=''")
                db.execute("UPDATE games SET updated_at=datetime('now') WHERE updated_at=''")
                legacy_root = db.execute("SELECT value FROM settings WHERE key='game_root'").fetchone()
                if legacy_root and legacy_root[0] and not db.execute("SELECT 1 FROM games LIMIT 1").fetchone():
                    db.execute("""INSERT INTO games(id,name,root,platform,source,strategy,adapter_id,
                               validation_status,created_at,updated_at)
                               VALUES('legacy-cyberpunk2077','Cyberpunk 2077',?,'linux','legacy','copy',
                                      'cyberpunk2077','unvalidated',datetime('now'),datetime('now'))""",
                               (legacy_root[0],))
                db.execute("PRAGMA user_version=2")

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,value))
