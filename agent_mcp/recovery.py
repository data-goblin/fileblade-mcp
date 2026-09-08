from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Any

MAX_RECORD_BYTES = 1024 * 1024 + 8192
MAX_RECORDS = 64
RECORD_LIFETIME_SECONDS = 7 * 24 * 60 * 60
IDENTIFIER_CHARACTERS = set("0123456789abcdef")
IDENTIFIER_LENGTH = 32


def valid_identifier(record_id: Any) -> bool:
    return (
        isinstance(record_id, str)
        and len(record_id) == IDENTIFIER_LENGTH
        and set(record_id) <= IDENTIFIER_CHARACTERS
    )


class RecoveryStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def record_path(self, record_id: str) -> Path:
        return self.directory / f"{record_id}.json"

    def write(self, payload: dict[str, Any], definition_id: str) -> str:
        document = {
            "formatVersion": 1,
            "createdAt": int(time.time()),
            "definitionId": str(definition_id),
            "payload": payload,
        }
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("the recovery record exceeds the store size limit")
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.prune()
        record_id = secrets.token_hex(IDENTIFIER_LENGTH // 2)
        descriptor = os.open(
            self.record_path(record_id),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
        return record_id

    def read(self, record_id: str) -> dict[str, Any] | None:
        if not valid_identifier(record_id):
            return None
        try:
            descriptor = os.open(self.record_path(record_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
                return None
            data = os.read(descriptor, MAX_RECORD_BYTES)
        except OSError:
            return None
        finally:
            os.close(descriptor)
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError):
            return None
        if not isinstance(document, dict) or document.get("formatVersion") != 1:
            return None
        created = document.get("createdAt")
        if not isinstance(created, int) or created <= 0 or time.time() - created > RECORD_LIFETIME_SECONDS:
            return None
        return document if isinstance(document.get("payload"), dict) else None

    def find(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            entries = list(self.directory.glob("*.json"))
        except OSError:
            return None
        for entry in sorted(entries)[:MAX_RECORDS]:
            document = self.read(entry.stem)
            if document is not None and document["payload"] == payload:
                return document
        return None

    def discard(self, record_id: str) -> None:
        if not valid_identifier(record_id):
            return
        try:
            self.record_path(record_id).unlink()
        except OSError:
            pass

    def prune(self) -> None:
        try:
            entries = list(self.directory.glob("*.json"))
        except OSError:
            return
        now = time.time()
        surviving: list[tuple[float, Path]] = []
        for entry in entries:
            try:
                info = entry.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or now - info.st_mtime > RECORD_LIFETIME_SECONDS:
                try:
                    entry.unlink()
                except OSError:
                    pass
                continue
            surviving.append((info.st_mtime, entry))
        surviving.sort(key=lambda item: item[0])
        while len(surviving) >= MAX_RECORDS:
            _, entry = surviving.pop(0)
            try:
                entry.unlink()
            except OSError:
                pass
