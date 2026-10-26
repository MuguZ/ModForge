"""Directory-descriptor based no-follow operations for game-root paths."""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import shutil
import fcntl
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


class UnsafePathError(OSError):
    pass


@contextmanager
def locked_file(path: Path, acquire: bool = True):
    """Acquire an advisory lock without following a pre-existing symlink."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if acquire:
            fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "a") as stream:
            yield stream
    finally:
        if acquire:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _open_root(root: Path) -> int:
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def open_parent(root: Path, relative: str, create: bool = False) -> tuple[int, str]:
    """Open a destination's parent beneath root without following any symlink."""
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or ".." in parts:
        raise UnsafePathError(errno.EPERM, "unsafe relative path", relative)
    current = _open_root(root)
    try:
        for component in parts[:-1]:
            try:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o755, dir_fd=current)
                os.fsync(current)
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        return current, parts[-1]
    except Exception:
        os.close(current)
        raise


def hash_at(root: Path, relative: str) -> str | None:
    try:
        parent, name = open_parent(root, relative)
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise UnsafePathError(errno.EPERM, "destination is not a regular file", relative)
            digest = hashlib.sha256()
            with os.fdopen(fd, "rb", closefd=False) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def replace_at(root: Path, relative: str, source: Path, create_parents: bool = True,
               source_root: Path | None = None, source_relative: str | None = None) -> None:
    parent, name = open_parent(root, relative, create=create_parents)
    source_parent = None
    try:
        if source_root is not None:
            if source_relative is None:
                raise ValueError("source_relative is required with source_root")
            source_parent, source_name = open_parent(source_root, source_relative)
            source_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent)
            try:
                if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                    raise UnsafePathError(errno.EPERM, "replacement source is not a regular file", source_relative)
            finally:
                os.close(source_fd)
            os.replace(source_name, name, src_dir_fd=source_parent, dst_dir_fd=parent)
        else:
            os.replace(source, name, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if source_parent is not None:
            os.close(source_parent)
        os.close(parent)


def copy_into_root(source: Path, root: Path, relative: str,
                   source_root: Path | None = None, source_relative: str | None = None) -> None:
    """Durably copy a regular file to a path opened beneath a trusted root."""
    if source_root is not None:
        if source_relative is None:
            raise ValueError("source_relative is required with source_root")
        source_parent, source_name = open_parent(source_root, source_relative)
        try:
            source_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent)
        finally:
            os.close(source_parent)
    else:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    source_info = os.fstat(source_fd)
    if not stat.S_ISREG(source_info.st_mode):
        os.close(source_fd)
        raise UnsafePathError(errno.EPERM, "copy source is not a regular file", str(source))
    try:
        parent, name = open_parent(root, relative, create=True)
    except Exception:
        os.close(source_fd)
        raise
    temp_name = f".{name}.{os.urandom(12).hex()}.tmp"
    temp_created = False
    try:
        try:
            temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              stat.S_IMODE(source_info.st_mode), dir_fd=parent)
            temp_created = True
        except Exception:
            os.close(source_fd)
            raise
        with os.fdopen(source_fd, "rb") as incoming, os.fdopen(temp_fd, "wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temp_name, name, src_dir_fd=parent, dst_dir_fd=parent)
        temp_created = False
        os.fsync(parent)
    finally:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=parent)
                os.fsync(parent)
            except FileNotFoundError:
                pass
        os.close(parent)


def unlink_at(root: Path, relative: str, expected_hash: str | None = None) -> None:
    parent, name = open_parent(root, relative)
    try:
        if expected_hash is not None:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or _hash_fd(fd) != expected_hash:
                    raise UnsafePathError(errno.EIO, "refusing to remove a file with unexpected contents", relative)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise UnsafePathError(errno.EAGAIN, "destination changed before removal", relative)
            finally:
                os.close(fd)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def restore_at(root: Path, relative: str, source: Path, temp_name: str, expected_hash: str,
               source_root: Path | None = None, source_relative: str | None = None) -> None:
    """Copy a backup to a journal-named sibling and atomically install it."""
    if "/" in temp_name or temp_name in {".", ".."}:
        raise UnsafePathError(errno.EPERM, "invalid restore temporary name", temp_name)
    parent, name = open_parent(root, relative, create=True)
    try:
        try:
            temp_fd = os.open(temp_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            temp_fd = -1
        if temp_fd >= 0:
            try:
                info = os.fstat(temp_fd)
                if not stat.S_ISREG(info.st_mode):
                    raise UnsafePathError(errno.EPERM, "restore temporary is not a regular file", temp_name)
                actual = _hash_fd(temp_fd)
                if actual != expected_hash:
                    raise UnsafePathError(errno.EIO, "restore temporary has unexpected contents", temp_name)
            finally:
                os.close(temp_fd)
        else:
            if source_root is not None:
                if source_relative is None:
                    raise ValueError("source_relative is required with source_root")
                source_parent, source_name = open_parent(source_root, source_relative)
                try:
                    source_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent)
                finally:
                    os.close(source_parent)
            else:
                source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            source_info = os.fstat(source_fd)
            if not stat.S_ISREG(source_info.st_mode):
                os.close(source_fd)
                raise UnsafePathError(errno.EPERM, "restore source is not a regular file", str(source))
            try:
                temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  stat.S_IMODE(source_info.st_mode), dir_fd=parent)
            except Exception:
                os.close(source_fd)
                raise
            try:
                with os.fdopen(source_fd, "rb") as incoming, os.fdopen(temp_fd, "wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
            except Exception:
                raise
            temp_fd = os.open(temp_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if _hash_fd(temp_fd) != expected_hash:
                    raise UnsafePathError(errno.EIO, "copied restore temporary failed hash verification", temp_name)
            finally:
                os.close(temp_fd)
        os.replace(temp_name, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def remove_restore_temp(root: Path, relative: str, temp_name: str, expected_hash: str) -> None:
    if "/" in temp_name or temp_name in {".", ".."}:
        raise UnsafePathError(errno.EPERM, "invalid restore temporary name", temp_name)
    parent, _ = open_parent(root, relative)
    try:
        try:
            fd = os.open(temp_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or _hash_fd(fd) != expected_hash:
                raise UnsafePathError(errno.EIO, "refusing to remove unverified restore temporary", temp_name)
        finally:
            os.close(fd)
        os.unlink(temp_name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def _hash_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    with os.fdopen(os.dup(fd), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
