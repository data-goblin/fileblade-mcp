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
RECORD_LIFETIME_SECONDS = 3650 * 24 * 60 * 60
RESTORED_LIFETIME_SECONDS = 7 * 24 * 60 * 60
IDENTIFIER_CHARACTERS = set("0123456789abcdef")
IDENTIFIER_LENGTH = 32
MAX_SCANNED_RECORDS = 512


class RecoveryFull(RuntimeError):
    pass


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

    def write(self, payload: dict[str, Any], definition_id: str, context: dict[str, str]) -> str:
        existing = self.find(payload)
        if existing is not None and existing.get("context") == context and "restoredAt" not in existing:
            return str(existing["recordId"])
        document = {
            "formatVersion": 1,
            "createdAt": int(time.time()),
            "definitionId": str(definition_id),
            "context": dict(context),
            "payload": payload,
        }
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("the recovery record exceeds the store size limit")
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.expired()
        if self.live_records() >= MAX_RECORDS:
            raise RecoveryFull(
                "the undo store is full; restore or discard earlier removals before removing another"
            )
        record_id = secrets.token_hex(IDENTIFIER_LENGTH // 2)
        descriptor = os.open(
            self.record_path(record_id),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            written = 0
            while written < len(encoded):
                progress = os.write(descriptor, encoded[written:])
                if progress <= 0:
                    raise OSError("the recovery record could not be written")
                written += progress
            os.fsync(descriptor)
        except (OSError, RuntimeError):
            os.close(descriptor)
            try:
                self.record_path(record_id).unlink()
            except OSError:
                pass
            raise
        else:
            os.close(descriptor)
        parent = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
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
        if not isinstance(document.get("payload"), dict) or not isinstance(document.get("context"), dict):
            return None
        document["recordId"] = record_id
        return document

    def records(self) -> list[dict[str, Any]]:
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError:
            return []
        found = []
        for entry in entries[:MAX_SCANNED_RECORDS]:
            document = self.read(entry.stem)
            if document is not None:
                found.append(document)
        return found

    def live_records(self) -> int:
        return len([document for document in self.records() if "restoredAt" not in document])

    def mark_restored(self, record_id: str) -> None:
        document = self.read(record_id)
        if document is None or "restoredAt" in document:
            return
        document["restoredAt"] = int(time.time())
        stored = {key: value for key, value in document.items() if key != "recordId"}
        encoded = json.dumps(stored, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        staged = self.directory / f"{record_id}.{secrets.token_hex(8)}.staged"
        try:
            descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError:
            return
        try:
            written = 0
            while written < len(encoded):
                progress = os.write(descriptor, encoded[written:])
                if progress <= 0:
                    raise OSError("the recovery record could not be updated")
                written += progress
            os.fsync(descriptor)
            os.close(descriptor)
            os.replace(staged, self.record_path(record_id))
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                staged.unlink()
            except OSError:
                pass
            return
        parent = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    def find(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        matches = [document for document in self.records() if document["payload"] == payload]
        for document in matches:
            if "restoredAt" not in document:
                return document
        return matches[0] if matches else None

    def discard(self, record_id: str) -> None:
        if not valid_identifier(record_id):
            return
        try:
            self.record_path(record_id).unlink()
        except OSError:
            pass

    def expired(self) -> None:
        try:
            entries = list(self.directory.glob("*"))
        except OSError:
            return
        now = time.time()
        completed: list[tuple[float, Path]] = []
        for entry in entries:
            if not entry.name.endswith(".json") and not entry.name.endswith(".staged"):
                continue
            try:
                info = entry.lstat()
            except OSError:
                continue
            if entry.name.endswith(".staged"):
                if now - info.st_mtime > RESTORED_LIFETIME_SECONDS:
                    try:
                        entry.unlink()
                    except OSError:
                        pass
                continue
            document = self.read(entry.stem)
            restored = document is not None and "restoredAt" in document
            limit = RESTORED_LIFETIME_SECONDS if restored else RECORD_LIFETIME_SECONDS
            if not stat.S_ISREG(info.st_mode) or now - info.st_mtime > limit:
                try:
                    entry.unlink()
                except OSError:
                    pass
                continue
            if restored:
                completed.append((info.st_mtime, entry))
        completed.sort(key=lambda item: item[0])
        while len(completed) > MAX_RECORDS:
            _, entry = completed.pop(0)
            try:
                entry.unlink()
            except OSError:
                pass
