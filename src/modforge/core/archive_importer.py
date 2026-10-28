"""Inspect and safely extract untrusted ZIP archives."""
from __future__ import annotations
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class ArchiveLimits:
    files: int = 20_000
    total_size: int = 8 * 1024**3
    file_size: int = 2 * 1024**3
    compression_ratio: int = 1_000
    nesting_depth: int = 64


@dataclass(frozen=True)
class ImportedFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ImportResult:
    id: str
    name: str
    archive: Path
    archive_hash: str
    extracted_path: Path
    files: tuple[ImportedFile, ...]


@dataclass(frozen=True)
class ArchivePreview:
    name: str
    archive: Path
    archive_size: int
    archive_hash: str
    files: tuple[tuple[str, int], ...]


class ArchiveError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArchiveImporter:
    def __init__(self, archive_dir: Path, mods_dir: Path, limits: ArchiveLimits = ArchiveLimits()):
        self.archive_dir, self.mods_dir, self.limits = archive_dir, mods_dir, limits
        archive_dir.mkdir(parents=True, exist_ok=True)
        mods_dir.mkdir(parents=True, exist_ok=True)

    def inspect_zip(self, source: Path) -> ArchivePreview:
        """Validate ZIP metadata and return a bounded preview without extraction."""
        source = source.expanduser().resolve(strict=True)
        archive_hash = sha256_file(source)
        try:
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if not infos:
                    raise ArchiveError("The archive is empty")
                if len(infos) > self.limits.files:
                    raise ArchiveError("Archive contains too many entries")
                seen: dict[str, tuple[str, bool]] = {}
                files = []
                total = 0
                for info in infos:
                    raw = info.filename.replace("\\", "/")
                    parts = PurePosixPath(raw).parts
                    if (not raw or raw.startswith("/") or ".." in parts or
                            (parts and ":" in parts[0]) or "\x00" in raw):
                        raise ArchiveError(f"Unsafe archive path: {info.filename!r}")
                    clean_parts = [part for part in parts if part not in ("", ".")]
                    if not clean_parts:
                        continue
                    if len(clean_parts) > self.limits.nesting_depth:
                        raise ArchiveError(f"Archive path is nested too deeply: {info.filename!r}")
                    rel = PurePosixPath(*clean_parts).as_posix()
                    is_dir = info.is_dir()
                    key = rel.casefold()
                    if key in seen:
                        raise ArchiveError(f"Duplicate or case-colliding path: {rel}")
                    for parent_index in range(1, len(clean_parts)):
                        parent = PurePosixPath(*clean_parts[:parent_index]).as_posix().casefold()
                        if parent in seen and not seen[parent][1]:
                            raise ArchiveError(f"File is used as a directory: {seen[parent][0]}")
                    if not is_dir:
                        prefix = key + "/"
                        if any(existing.startswith(prefix) for existing in seen):
                            raise ArchiveError(f"Directory path conflicts with an existing file: {rel}")
                    seen[key] = (rel, is_dir)
                    mode = info.external_attr >> 16
                    file_type = stat.S_IFMT(mode)
                    if stat.S_ISLNK(mode) or (file_type and file_type not in (stat.S_IFREG, stat.S_IFDIR)):
                        raise ArchiveError(f"Special file is not allowed: {rel}")
                    if info.file_size > self.limits.file_size:
                        raise ArchiveError(f"Entry is too large: {rel}")
                    total += info.file_size
                    if total > self.limits.total_size:
                        raise ArchiveError("Archive expands beyond the configured size limit")
                    if info.file_size and info.compress_size == 0:
                        raise ArchiveError(f"Suspicious compression ratio: {rel}")
                    if info.compress_size and info.file_size / info.compress_size > self.limits.compression_ratio:
                        raise ArchiveError(f"Suspicious compression ratio: {rel}")
                    if not is_dir:
                        files.append((rel, info.file_size))
                if not files:
                    raise ArchiveError("The archive contains no files")
                return ArchivePreview(source.stem, source, source.stat().st_size, archive_hash, tuple(files))
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise ArchiveError(f"Could not inspect archive: {exc}") from exc

    def import_zip(self, source: Path) -> ImportResult:
        source = source.expanduser().resolve(strict=True)
        # Revalidate at the extraction boundary as callers may skip the UI preview.
        preview = self.inspect_zip(source)
        archive_hash = preview.archive_hash
        uid = str(uuid.uuid4())
        final = self.mods_dir / uid
        stage = Path(tempfile.mkdtemp(prefix=".import-", dir=self.mods_dir))
        try:
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if not infos:
                    raise ArchiveError("The archive is empty")
                if len(infos) > self.limits.files:
                    raise ArchiveError("Archive contains too many entries")
                seen: set[str] = set()
                normalized: list[tuple[zipfile.ZipInfo, str]] = []
                total = 0
                for info in infos:
                    raw = info.filename.replace("\\", "/")
                    parts = PurePosixPath(raw).parts
                    if (not raw or raw.startswith("/") or ".." in parts or
                            (parts and ":" in parts[0]) or "\x00" in raw):
                        raise ArchiveError(f"Unsafe archive path: {info.filename!r}")
                    if len([part for part in parts if part not in ("", ".")]) > self.limits.nesting_depth:
                        raise ArchiveError(f"Archive path is nested too deeply: {info.filename!r}")
                    rel = PurePosixPath(*[part for part in parts if part not in ("", ".")]).as_posix()
                    if not rel or rel == ".":
                        continue
                    key = rel.casefold()
                    if key in seen:
                        raise ArchiveError(f"Duplicate or case-colliding path: {rel}")
                    seen.add(key)
                    mode = info.external_attr >> 16
                    file_type = stat.S_IFMT(mode)
                    if stat.S_ISLNK(mode) or (file_type and file_type not in (stat.S_IFREG, stat.S_IFDIR)):
                        raise ArchiveError(f"Special file is not allowed: {rel}")
                    if info.file_size > self.limits.file_size:
                        raise ArchiveError(f"Entry is too large: {rel}")
                    total += info.file_size
                    if total > self.limits.total_size:
                        raise ArchiveError("Archive expands beyond the configured size limit")
                    if info.file_size and info.compress_size == 0:
                        raise ArchiveError(f"Suspicious compression ratio: {rel}")
                    if info.compress_size and info.file_size / info.compress_size > self.limits.compression_ratio:
                        raise ArchiveError(f"Suspicious compression ratio: {rel}")
                    normalized.append((info, rel))
                files: list[ImportedFile] = []
                for info, rel in normalized:
                    target = stage.joinpath(*PurePosixPath(rel).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if info.is_dir():
                        target.mkdir(exist_ok=True)
                        continue
                    # Exclusive creation prevents collisions and never follows a pre-existing symlink.
                    with archive.open(info) as incoming, target.open("xb") as outgoing:
                        digest, copied = hashlib.sha256(), 0
                        while chunk := incoming.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > info.file_size or copied > self.limits.file_size:
                                raise ArchiveError(f"Entry expanded beyond its declared size: {rel}")
                            digest.update(chunk)
                            outgoing.write(chunk)
                    if copied != info.file_size:
                        raise ArchiveError(f"Entry size mismatch: {rel}")
                    files.append(ImportedFile(rel, copied, digest.hexdigest()))
            if not files:
                raise ArchiveError("The archive contains no files")
            stored_archive = self.archive_dir / f"{uid}-{source.name}"
            shutil.copy2(source, stored_archive)
            if sha256_file(stored_archive) != archive_hash:
                stored_archive.unlink(missing_ok=True)
                raise ArchiveError("Source archive changed while it was being imported")
            os.replace(stage, final)
            return ImportResult(uid, source.stem, stored_archive, archive_hash, final, tuple(files))
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise ArchiveError(f"Could not import archive: {exc}") from exc
        finally:
            if stage.exists():
                shutil.rmtree(stage)
