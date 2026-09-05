from __future__ import annotations

import ctypes
from dataclasses import dataclass
import datetime as dt
import os
from pathlib import Path
import re
import stat
import struct
import time

from fileblade_inventory import watch_path
from fileblade_mutations import Snapshot, MAX_CONTENT


@dataclass(frozen=True)
class ReadResult:
    data: bytes | None
    error: str | None = None


def creation_time(path: Path) -> str:
    try:
        statx = ctypes.CDLL(None, use_errno=True).statx
        statx.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p,
        ]
        statx.restype = ctypes.c_int
        result = ctypes.create_string_buffer(256)
        if statx(-100, os.fsencode(path), 0x100, 0x800, ctypes.byref(result)) != 0:
            return ""
        mask = struct.unpack_from("I", result.raw, 0)[0]
        seconds = struct.unpack_from("q", result.raw, 80)[0]
        return dt.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M") if mask & 0x800 and seconds > 0 else ""
    except (AttributeError, OSError, struct.error, ValueError):
        return ""


def artifact_metrics(path: Path, data: bytes) -> dict[str, object]:
    try:
        target = path.resolve(strict=True)
        metadata = target.stat(follow_symlinks=False)
    except (OSError, RuntimeError):
        return {}
    if not stat.S_ISREG(metadata.st_mode):
        return {}
    text = data.decode("utf-8", "replace")
    return {
        "updated": dt.datetime.fromtimestamp(metadata.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "created": creation_time(target),
        "bytes": len(data),
        "characters": len(text),
        "words": len(re.findall(r"\w+", text, re.UNICODE)),
        "tokens": (len(data) + 3) // 4,
    }


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds
        self.truncated = False

    def check(self) -> None:
        if time.monotonic() > self.end:
            raise TimeoutError("inventory deadline exceeded")


def _open_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open("/", flags)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def bounded_read(
    path: Path,
    limit: int,
    *,
    required_owner_uid: int | None = None,
    reject_group_or_world_writable: bool = False,
) -> ReadResult:
    watch_path(path)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    parent_descriptor = -1
    descriptor = -1
    try:
        absolute = Path(os.path.abspath(os.fspath(path))).resolve(strict=True)
        parent_descriptor = _open_directory_nofollow(absolute.parent)
        descriptor = os.open(absolute.name, flags, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return ReadResult(None, "not-regular")
        if required_owner_uid is not None and metadata.st_uid != required_owner_uid:
            return ReadResult(None, "insecure-owner")
        if reject_group_or_world_writable and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return ReadResult(None, "insecure-mode")
        if metadata.st_size > limit:
            return ReadResult(None, "oversized")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            return ReadResult(None, "oversized")
        return ReadResult(data)
    except FileNotFoundError:
        return ReadResult(None, "missing")
    except (OSError, RuntimeError):
        return ReadResult(None, "unreadable")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def safe_relative_file(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str):
        return None
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    current = root
    try:
        for part in path.parts[:-1]:
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                return None
    except OSError:
        return None
    return root.joinpath(*path.parts)


def bounded_directories(root: Path, maximum: int, deadline: Deadline) -> list[Path]:
    watch_path(root, directory=True)
    result: list[Path] = []
    descriptor = -1
    try:
        descriptor = _open_directory_nofollow(root)
        with os.scandir(descriptor) as entries:
            for index, entry in enumerate(entries):
                deadline.check()
                if index >= maximum:
                    deadline.truncated = True
                    break
                if entry.is_dir(follow_symlinks=False):
                    result.append(root / entry.name)
    except OSError:
        return []
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return sorted(result, key=lambda path: path.name)


def bounded_files(root: Path, maximum: int, deadline: Deadline) -> list[Path]:
    watch_path(root, directory=True)
    result: list[Path] = []
    descriptor = -1
    try:
        descriptor = _open_directory_nofollow(root)
        with os.scandir(descriptor) as entries:
            for index, entry in enumerate(entries):
                deadline.check()
                if index >= maximum:
                    deadline.truncated = True
                    break
                if entry.is_file(follow_symlinks=False):
                    result.append(root / entry.name)
    except OSError:
        return []
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return sorted(result, key=lambda path: path.name)


def atomic_write(path: Path, data: bytes, *, snapshot: Snapshot | None = None) -> str | None:
    try:
        (snapshot or Snapshot.read(path, MAX_CONTENT)).write(data)
        return None
    except (OSError, RuntimeError) as error:
        return str(error)
