"""Durable dispatch claims, independent of a framework's post-tool checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Protocol, Union
from uuid import UUID


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate attempt fields")
    return value


@dataclass(frozen=True)
class RuntimeAttempt:
    approval_id: str
    action_id: str
    request_commitment: str

    def document(self) -> dict[str, str]:
        if (not isinstance(self.approval_id, str) or not isinstance(self.action_id, str)
                or str(UUID(self.approval_id)) != self.approval_id
                or str(UUID(self.action_id)) != self.action_id
                or not isinstance(self.request_commitment, str)
                or not re.fullmatch(r"[a-f0-9]{64}", self.request_commitment)):
            raise ValueError("Invalid runtime attempt identity")
        return {"approvalId": self.approval_id, "actionId": self.action_id,
                "requestCommitment": self.request_commitment}


class RuntimeAttemptStore(Protocol):
    def claim(self, attempt: RuntimeAttempt) -> bool:
        """Durably and atomically claim before IO. Only the first claim returns True.

        Matching prior attempts return False. Unreadable/conflicting state raises;
        implementations must never reset an attempt or silently retry dispatch.
        """
        ...


class FileRuntimeAttemptStore:
    """Private single-host POSIX markers. Preserve this directory across restarts.

    Markers contain only two IDs and a commitment, never arguments or credentials.
    Shared multi-host runtimes need their own durable atomic claim implementation.
    """

    def __init__(self, directory: Union[str, Path]) -> None:
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise ValueError("Attempt state requires an absolute host-owned directory")
        if os.name != "posix":
            raise ValueError("FileRuntimeAttemptStore requires a POSIX filesystem")

    @staticmethod
    def _private(info: os.stat_result) -> bool:
        return info.st_uid == os.getuid() and info.st_mode & 0o077 == 0

    def claim(self, attempt: RuntimeAttempt) -> bool:
        if not isinstance(attempt, RuntimeAttempt):
            raise ValueError("A RuntimeAttempt identity is required")
        document = attempt.document()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or not self._private(info):
            raise ValueError("Attempt directory must be private, nonsymlinked and runtime-owned")
        path = self.directory / (attempt.approval_id + ".json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            try:
                # Nonblocking prevents a malformed FIFO marker from hanging inspection.
                existing = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(existing)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or not self._private(info):
                        raise ValueError("Invalid attempt file")
                    value = json.loads(os.read(existing, 4097), object_pairs_hook=_unique_object)
                    if not isinstance(value, dict) or value != document:
                        raise ValueError("Conflicting attempt")
                finally:
                    os.close(existing)
            except (OSError, TypeError, ValueError) as exc:
                raise ValueError("Existing attempt is unreadable or conflicts; inspect without retrying dispatch") from exc
            return False
        # A partial marker stays present on every error. Never remove it to retry.
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # New directories (including concurrent mkdir) need their parent entries
        # persisted too. Resolve /tmp aliases, then sync every physical ancestor.
        directory = self.directory.resolve(strict=True)
        while True:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            if directory.parent == directory:
                break
            directory = directory.parent
        return True
