"""Local filesystem storage adapter.

Design rules:

* Objects are named by a server-generated random key, never by client input, so
  path traversal is impossible by construction.
* Uploads stream through a temporary file in the same filesystem and are moved
  into place with an atomic rename, so a partially written object is never
  observable under its final key.
* Size is enforced while streaming; a client that lies about ``Content-Length``
  is cut off at the limit rather than filling the disk.
* The root directory is created with owner-only permissions and lives outside
  any static-file mount.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from app.domain.files.entities import StoredObject
from app.domain.files.exceptions import EmptyUpload, UploadTooLarge

logger = logging.getLogger(__name__)


class StorageNotWritable(RuntimeError):
    pass


class LocalFileStorage:
    def __init__(self, root: Path, *, chunk_size: int = 64 * 1024) -> None:
        self._root = root.resolve()
        self._tmp = self._root / ".tmp"
        self._chunk = chunk_size

    @property
    def root(self) -> Path:
        return self._root

    def ensure_ready(self) -> None:
        """Create the directory tree and prove it is writable."""
        for directory in (self._root, self._tmp):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = self._tmp / f".probe-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"ok")
        except OSError as exc:  # pragma: no cover - environment specific
            raise StorageNotWritable(str(self._root)) from exc
        finally:
            probe.unlink(missing_ok=True)

    def check(self) -> None:
        if not os.access(self._root, os.W_OK | os.X_OK):
            raise StorageNotWritable(str(self._root))

    @staticmethod
    def new_key() -> str:
        key = uuid.uuid4().hex
        return f"{key[:2]}/{key}"

    def path_for(self, storage_key: str) -> str:
        path = (self._root / storage_key).resolve()
        if self._root not in path.parents:
            # Defence in depth: keys are generated server-side, so this cannot
            # happen unless the database was tampered with.
            raise ValueError("storage key escapes storage root")
        return str(path)

    def write(self, stream: BinaryIO, *, max_bytes: int) -> StoredObject:
        key = self.new_key()
        final_path = Path(self.path_for(key))
        tmp_path = self._tmp / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = stream.read(self._chunk)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge(max_bytes=max_bytes)
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if size == 0:
                raise EmptyUpload()
            final_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(tmp_path, final_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return StoredObject(storage_key=key, size_bytes=size, sha256=digest.hexdigest())

    def exists(self, storage_key: str) -> bool:
        return Path(self.path_for(storage_key)).is_file()

    def delete(self, storage_key: str) -> None:
        Path(self.path_for(storage_key)).unlink(missing_ok=True)

    def open(self, storage_key: str) -> Iterator[bytes]:
        with open(self.path_for(storage_key), "rb") as fh:
            while chunk := fh.read(self._chunk):
                yield chunk
