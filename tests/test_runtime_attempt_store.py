"""Real POSIX claim persistence, independent of native cursor flush callbacks."""
import json
import multiprocessing
import os
from pathlib import Path
import stat
from concurrent.futures import ProcessPoolExecutor

import pytest

from praesidia.integrations import FileRuntimeAttemptStore, RuntimeAttempt

ATTEMPT = RuntimeAttempt("00000000-0000-4000-8000-000000000001",
                         "00000000-0000-7000-8000-000000000002", "a" * 64)


def _claim_in_process(directory):
    try:
        return FileRuntimeAttemptStore(directory).claim(ATTEMPT)
    except ValueError:
        # Another process can observe an incomplete marker; fail closed, no retry.
        return False


def test_fresh_processes_admit_only_one_dispatch_claim(tmp_path):
    directory = tmp_path / "private" / "attempts"
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_claim_in_process, [str(directory)] * 9))
    assert results.count(True) == 1
    assert FileRuntimeAttemptStore(directory).claim(ATTEMPT) is False
    marker = directory / (ATTEMPT.approval_id + ".json")
    assert json.loads(marker.read_text()) == ATTEMPT.document()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_new_directory_ancestors_are_durable_before_true(tmp_path, monkeypatch):
    directory = tmp_path / "new" / "nested" / "attempts"
    synced = set()
    actual = os.fsync
    def sync(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            synced.add((info.st_dev, info.st_ino))
        actual(fd)
    monkeypatch.setattr(os, "fsync", sync)
    assert FileRuntimeAttemptStore(directory).claim(ATTEMPT)
    expected = directory.resolve()
    while True:
        info = expected.stat()
        assert (info.st_dev, info.st_ino) in synced
        if expected.parent == expected:
            break
        expected = expected.parent


@pytest.mark.parametrize("failure", ["file-sync", "ancestor-sync", "write"])
def test_incomplete_durability_never_admits_and_retains_blocking_marker(tmp_path, monkeypatch, failure):
    directory = tmp_path / "attempts"
    actual_sync = os.fsync
    def sync(fd):
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (failure == "file-sync" and not is_dir) or (failure == "ancestor-sync" and is_dir):
            raise OSError("injected persistence failure")
        actual_sync(fd)
    def write(*args, **kwargs):
        args[1].write('{"approvalId":')
        raise OSError("injected persistence failure")
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sync)
        if failure == "write":
            patch.setattr(json, "dump", write)
        with pytest.raises(OSError, match="persistence failure"):
            FileRuntimeAttemptStore(directory).claim(ATTEMPT)
    marker = directory / (ATTEMPT.approval_id + ".json")
    assert marker.exists()
    if failure == "write":
        with pytest.raises(ValueError, match="unreadable"):
            FileRuntimeAttemptStore(directory).claim(ATTEMPT)
    else:
        assert FileRuntimeAttemptStore(directory).claim(ATTEMPT) is False


@pytest.mark.parametrize("kind", ["empty", "invalid", "extra", "array", "duplicate", "oversized", "public", "symlink", "fifo", "directory", "conflict"])
def test_existing_bad_markers_never_reset_or_admit(tmp_path, kind):
    tmp_path.chmod(0o700)
    marker = tmp_path / (ATTEMPT.approval_id + ".json")
    if kind == "symlink":
        other = tmp_path / "other"; other.write_text(json.dumps(ATTEMPT.document())); marker.symlink_to(other)
    elif kind == "fifo":
        os.mkfifo(marker, 0o600)
    elif kind == "directory":
        marker.mkdir()
    else:
        values = {"empty": "", "invalid": "[", "extra": json.dumps({**ATTEMPT.document(), "extra": 1}),
                  "array": json.dumps(list(ATTEMPT.document().items())),
                  "duplicate": json.dumps(ATTEMPT.document())[:-1] + ',"approvalId":"other"}',
                  "oversized": " " * 4097, "public": json.dumps(ATTEMPT.document()),
                  "conflict": json.dumps({**ATTEMPT.document(), "requestCommitment": "b" * 64})}
        marker.write_text(values[kind]); marker.chmod(0o644 if kind == "public" else 0o600)
    before = marker.lstat()
    with pytest.raises(ValueError, match="unreadable|conflicts"):
        FileRuntimeAttemptStore(tmp_path).claim(ATTEMPT)
    after = marker.lstat()
    assert (before.st_ino, before.st_size, before.st_mode) == (after.st_ino, after.st_size, after.st_mode)


@pytest.mark.parametrize("kind", ["public", "foreign-owner", "symlink"])
def test_private_owned_directory_is_required(tmp_path, monkeypatch, kind):
    directory = tmp_path / "attempts"
    directory.mkdir(mode=0o700)
    if kind == "public":
        directory.chmod(0o755)
    elif kind == "foreign-owner":
        actual_uid = os.getuid(); monkeypatch.setattr(os, "getuid", lambda: actual_uid + 1)
    else:
        link = tmp_path / "alias"; link.symlink_to(directory); directory = link
    with pytest.raises(ValueError, match="private"):
        FileRuntimeAttemptStore(directory).claim(ATTEMPT)
    assert not list((tmp_path / "attempts").iterdir())


@pytest.mark.parametrize("attempt", [None, RuntimeAttempt("../escape", ATTEMPT.action_id, "a" * 64),
    RuntimeAttempt(ATTEMPT.approval_id, "bad", "a" * 64), RuntimeAttempt(ATTEMPT.approval_id, ATTEMPT.action_id, "x" * 64),
    RuntimeAttempt(1, ATTEMPT.action_id, "a" * 64)])
def test_invalid_identity_never_creates_state(tmp_path, attempt):
    directory = tmp_path / "attempts"
    with pytest.raises((ValueError, TypeError)):
        FileRuntimeAttemptStore(directory).claim(attempt)
    assert not directory.exists()


def test_platform_and_absolute_path_contract(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="absolute"):
        FileRuntimeAttemptStore("relative")
    store = FileRuntimeAttemptStore(tmp_path)
    # Scope the platform probe to this module; do not alter pytest's filesystem.
    import praesidia.integrations.attempt_store as module
    from types import SimpleNamespace
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    with pytest.raises(ValueError, match="POSIX"):
        FileRuntimeAttemptStore(store.directory)
