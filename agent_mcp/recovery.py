from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Any

MAX_RECORD_BYTES = 1024 * 1024 + 8192
MAX_RECORDS = 64
RESTORED_LIFETIME_SECONDS = 7 * 24 * 60 * 60
IDENTIFIER_CHARACTERS = set("0123456789abcdef")
IDENTIFIER_LENGTH = 32
MAX_SCANNED_RECORDS = 512
MAX_STORE_BYTES = 16 * 1024 * 1024


class RecoveryFull(RuntimeError):
    pass


def valid_identifier(record_id: Any) -> bool:
    return (isinstance(record_id, str) and len(record_id) == IDENTIFIER_LENGTH
            and set(record_id) <= IDENTIFIER_CHARACTERS)


def private_file(info: os.stat_result) -> bool:
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) in (0o600, 0o400) and info.st_nlink == 1)


class RecoveryStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def record_path(self, record_id: str) -> Path:
        return self.directory / f"{record_id}.json"

    @contextmanager
    def _directory(self, create: bool = False):
        path = self.directory.absolute()
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for part in path.parts[1:]:
                if part in (".", ".."):
                    raise OSError("recovery directory must have a normalized absolute path")
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=descriptor)
                except FileNotFoundError:
                    if create:
                        raise
                    yield None
                    return
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError("recovery directory must be owned by this user with mode 0700")
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def _locked(self, parent: int):
        descriptor = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             0o600, dir_fd=parent)
        try:
            if not private_file(os.fstat(descriptor)):
                raise OSError("recovery lock is not private")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RecoveryFull("the undo store is busy; retry the operation") from error
            yield
        finally:
            os.close(descriptor)

    def _names(self, parent: int) -> list[str]:
        names = []
        with os.scandir(parent) as entries:
            for entry in entries:
                if len(names) >= MAX_SCANNED_RECORDS:
                    raise RecoveryFull("the undo store exceeds its directory entry limit")
                names.append(entry.name)
        return sorted(names)

    def _read(self, parent: int, record_id: str, limit: int = MAX_RECORD_BYTES) -> dict[str, Any] | None:
        if not valid_identifier(record_id):
            return None
        limit = min(limit, MAX_RECORD_BYTES)
        try:
            descriptor = os.open(f"{record_id}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=parent)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if not private_file(info) or info.st_size > limit:
                raise OSError("recovery record is not a bounded private regular file")
            data = bytearray()
            while len(data) <= limit:
                chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > limit:
                raise OSError("recovery record exceeds its byte limit")
        finally:
            os.close(descriptor)
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as error:
            raise OSError("recovery record is invalid JSON") from error
        if (not isinstance(document, dict) or document.get("formatVersion") != 1
                or type(document.get("createdAt")) is not int or document["createdAt"] <= 0
                or not isinstance(document.get("payload"), dict)
                or not isinstance(document.get("context"), dict)):
            raise OSError("recovery record has an unsupported format")
        document["recordId"] = record_id
        document["_bytes"] = len(data)
        return document

    def read(self, record_id: str) -> dict[str, Any] | None:
        if not valid_identifier(record_id):
            return None
        try:
            with self._directory() as parent:
                return self._read(parent, record_id) if parent is not None else None
        except OSError:
            return None

    def _snapshot(self, parent: int) -> tuple[list[dict[str, Any]], int]:
        found = []
        total = 0
        for name in self._names(parent):
            if name == ".lock":
                continue
            if name.endswith(".staged"):
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not private_file(info) or info.st_size > MAX_RECORD_BYTES:
                    raise OSError("staged recovery record is not a bounded private regular file")
                total += info.st_size
                if total > MAX_STORE_BYTES:
                    raise RecoveryFull("the undo store exceeds its aggregate byte limit")
                continue
            if not name.endswith(".json") or not valid_identifier(name[:-5]):
                raise RecoveryFull("the undo store contains an unrecognized entry")
            record = self._read(parent, name[:-5], MAX_STORE_BYTES - total)
            if record is not None:
                total += record["_bytes"]
                found.append(record)
        return found, total

    def _records(self, parent: int) -> list[dict[str, Any]]:
        return self._snapshot(parent)[0]

    def records(self) -> list[dict[str, Any]]:
        with self._directory() as parent:
            return self._records(parent) if parent is not None else []

    def inventory(self) -> dict[str, Any]:
        try:
            rows = [{"recordId": record["recordId"], "createdAt": record["createdAt"],
                     "restored": "restoredAt" in record, "trackedTransaction": bool(record.get("transactionId"))}
                    for record in self.records()]
            return {"ok": True, "schemaVersion": 1, "records": rows}
        except (OSError, RuntimeError) as error:
            return {"ok": False, "schemaVersion": 1, "records": [], "message": str(error)}

    def live_records(self) -> int:
        return sum("restoredAt" not in record for record in self.records())

    def find(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            matches = [record for record in self.records() if record["payload"] == payload]
        except (OSError, RecoveryFull):
            return None
        return next((record for record in matches if "restoredAt" not in record), matches[0] if matches else None)

    def _encode(self, document: dict) -> bytes:
        encoded = json.dumps({key: value for key, value in document.items() if key not in ("recordId", "_bytes")},
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("the recovery record exceeds the store size limit")
        return encoded

    def _publish(self, parent: int, name: str, encoded: bytes, replace: str = "") -> None:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=parent)
        try:
            written = 0
            while written < len(encoded):
                progress = os.write(descriptor, encoded[written:])
                if progress <= 0:
                    raise OSError("the recovery record could not be written")
                written += progress
            os.fsync(descriptor)
            if replace:
                os.replace(name, replace, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        except (OSError, RuntimeError):
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(descriptor)

    def write(self, payload: dict[str, Any], definition_id: str, context: dict[str, str], transaction_id: str = "") -> str:
        if transaction_id and not valid_identifier(transaction_id):
            raise ValueError("invalid recovery transaction id")
        with self._directory(create=True) as parent, self._locked(parent):
            self._expired(parent)
            records, total = self._snapshot(parent)
            for record in records:
                if (record["payload"] == payload and record["context"] == context
                        and record.get("transactionId", "") == transaction_id and "restoredAt" not in record):
                    return str(record["recordId"])
            if sum("restoredAt" not in record for record in records) >= MAX_RECORDS:
                raise RecoveryFull("the undo store is full; restore or discard earlier removals before removing another")
            document = {"formatVersion": 1, "createdAt": int(time.time()), "definitionId": str(definition_id),
                        "context": dict(context), "payload": payload, "transactionId": transaction_id}
            encoded = self._encode(document)
            if total + len(encoded) > MAX_STORE_BYTES:
                raise RecoveryFull("the undo store is full; its aggregate byte limit would be exceeded")
            record_id = transaction_id or secrets.token_hex(IDENTIFIER_LENGTH // 2)
            if self._read(parent, record_id) is not None:
                raise ValueError("recovery transaction id is already in use")
            self._publish(parent, f"{record_id}.json", encoded)
            return record_id

    def mark_restored(self, record_id: str) -> None:
        try:
            with self._directory() as parent:
                if parent is None:
                    return
                with self._locked(parent):
                    document = self._read(parent, record_id)
                    if document is None or "restoredAt" in document:
                        return
                    document["restoredAt"] = int(time.time())
                    staged = f"{record_id}.{secrets.token_hex(8)}.staged"
                    self._publish(parent, staged, self._encode(document), f"{record_id}.json")
        except (OSError, RecoveryFull):
            return

    def discard(self, record_id: str) -> None:
        if not valid_identifier(record_id):
            raise ValueError("invalid recovery record id")
        with self._directory() as parent:
            if parent is None:
                return
            with self._locked(parent):
                document = self._read(parent, record_id)
                if document is not None:
                    os.unlink(f"{record_id}.json", dir_fd=parent)
                    os.fsync(parent)

    def discard_payload(self, record_id: str, raw_payload: str | None = None) -> dict[str, Any]:
        try:
            payload = json.loads(raw_payload) if raw_payload is not None else None
            if payload is not None and not isinstance(payload, dict):
                raise ValueError("recovery payload must be an object")
            if not valid_identifier(record_id) and payload is None:
                raise ValueError("discard needs a recovery record id or a matching payload")
            with self._directory() as parent:
                if parent is not None:
                    with self._locked(parent):
                        if valid_identifier(record_id):
                            record = self._read(parent, record_id)
                        else:
                            matches = [record for record in self._records(parent) if record["payload"] == payload]
                            record = next((record for record in matches if "restoredAt" not in record),
                                          matches[0] if matches else None)
                        if record is not None:
                            if payload is not None and record["payload"] != payload:
                                raise ValueError("recovery record does not match this payload")
                            os.unlink(f"{record['recordId']}.json", dir_fd=parent)
                            os.fsync(parent)
            return {"ok": True, "schemaVersion": 1}
        except (OSError, ValueError, RuntimeError) as error:
            return {"ok": False, "schemaVersion": 1, "message": str(error)}

    def _expired(self, parent: int) -> None:
        now = time.time()
        completed = []
        records = self._records(parent)
        for name in self._names(parent):
            if name.endswith(".staged"):
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if private_file(info) and now - info.st_mtime > RESTORED_LIFETIME_SECONDS:
                    os.unlink(name, dir_fd=parent)
        for record in records:
            if "restoredAt" not in record:
                continue
            restored = record["restoredAt"]
            if type(restored) is not int:
                raise OSError("recovery completion timestamp is invalid")
            name = f"{record['recordId']}.json"
            if now - restored > RESTORED_LIFETIME_SECONDS:
                os.unlink(name, dir_fd=parent)
            else:
                completed.append((restored, name))
        for _, name in sorted(completed)[:-MAX_RECORDS]:
            os.unlink(name, dir_fd=parent)
        os.fsync(parent)

    def expired(self) -> None:
        with self._directory() as parent:
            if parent is not None:
                with self._locked(parent):
                    self._expired(parent)
