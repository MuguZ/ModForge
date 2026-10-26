"""SQLite schema and compact data access layer."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1


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

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,value))
