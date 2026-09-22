"""LocalFileStorage: atomic writes, size limits, traversal defence."""

from __future__ import annotations

import io
import stat
from pathlib import Path

import pytest

from app.domain.files.exceptions import EmptyUpload, UploadTooLarge
from app.infrastructure.storage.local import LocalFileStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    s = LocalFileStorage(tmp_path / "store", chunk_size=4)
    s.ensure_ready()
    return s


def test_ensure_ready_creates_private_directories(tmp_path: Path) -> None:
    root = tmp_path / "deep" / "store"
    LocalFileStorage(root).ensure_ready()
    assert root.is_dir() and (root / ".tmp").is_dir()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert list((root / ".tmp").iterdir()) == []  # probe file removed


def test_write_read_roundtrip_with_hash(storage: LocalFileStorage) -> None:
    obj = storage.write(io.BytesIO(b"0123456789"), max_bytes=100)
    assert obj.size_bytes == 10
    assert obj.sha256 == "84d89877f0d4041efb6bf91a16f0248f2fd573e6af05c19f96bedb9f882f7882"
    assert storage.exists(obj.storage_key)
    assert b"".join(storage.open(obj.storage_key)) == b"0123456789"
    assert Path(storage.path_for(obj.storage_key)).read_bytes() == b"0123456789"


def test_keys_are_server_generated_and_sharded(storage: LocalFileStorage) -> None:
    key = storage.write(io.BytesIO(b"x"), max_bytes=10).storage_key
    shard, name = key.split("/")
    assert len(shard) == 2 and name.startswith(shard) and len(name) == 32


def test_no_partial_files_left_behind_on_failure(storage: LocalFileStorage) -> None:
    with pytest.raises(UploadTooLarge):
        storage.write(io.BytesIO(b"too many bytes"), max_bytes=5)
    with pytest.raises(EmptyUpload):
        storage.write(io.BytesIO(b""), max_bytes=5)
    assert list((storage.root / ".tmp").iterdir()) == []
    assert [p for p in storage.root.rglob("*") if p.is_file()] == []


def test_size_limit_enforced_while_streaming(storage: LocalFileStorage) -> None:
    class Endless(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def read(self, n: int = -1) -> bytes:
            return b"a" * (n if n > 0 else 4)

    with pytest.raises(UploadTooLarge):
        storage.write(Endless(), max_bytes=64)  # type: ignore[arg-type]


def test_exact_limit_is_allowed(storage: LocalFileStorage) -> None:
    assert storage.write(io.BytesIO(b"12345"), max_bytes=5).size_bytes == 5


def test_path_for_rejects_escaping_keys(storage: LocalFileStorage) -> None:
    for key in ["../outside", "/etc/passwd", "aa/../../x"]:
        with pytest.raises(ValueError, match="escapes"):
            storage.path_for(key)


def test_delete_is_idempotent(storage: LocalFileStorage) -> None:
    key = storage.write(io.BytesIO(b"x"), max_bytes=10).storage_key
    storage.delete(key)
    storage.delete(key)
    assert not storage.exists(key)


def test_check_detects_unwritable_root(storage: LocalFileStorage) -> None:
    storage.check()
    storage.root.chmod(0o500)
    try:
        with pytest.raises(Exception, match=str(storage.root)):
            storage.check()
    finally:
        storage.root.chmod(0o700)
