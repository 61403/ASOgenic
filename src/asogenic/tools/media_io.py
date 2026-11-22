"""Binary media sources and Apple signed-upload plumbing.

The MCP surface keeps Base64 for backwards compatibility, but large App Store previews must be
provided as a local file (or by a future hosted staging session).  This module deliberately keeps
the source abstraction independent from any particular App Store resource so screenshots,
previews, and Asset Library media use the same checksum/chunking behavior.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from asogenic import context
from asogenic.errors import asc_state_error, validation_error

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class BinarySource:
    """A seekable source with a stable size and MD5 checksum."""

    _data: bytes | None
    _path: Path | None
    size: int
    checksum: str

    @classmethod
    def from_inputs(
        cls,
        *,
        base64_value: str | None = None,
        file_path: str | None = None,
        field_name: str = "asset",
    ) -> BinarySource:
        if bool(base64_value) == bool(file_path):
            raise validation_error(
                f"provide exactly one of {field_name}_base64 or {field_name}_path",
                field_name=field_name,
            )
        if file_path:
            if not context.get_runtime().allows_local_files:
                raise validation_error(
                    "local media paths are available only in stdio/local deployments; hosted callers must use Base64 within the request limit or run the local stdio server",
                    field_name=f"{field_name}_path",
                )
            path = Path(file_path).expanduser()
            if not path.is_file():
                raise validation_error(f"file does not exist: {path}", field_name=f"{field_name}_path")
            if not os.access(path, os.R_OK):
                raise validation_error(f"file is not readable: {path}", field_name=f"{field_name}_path")
            digest = hashlib.md5()
            size = 0
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if size == 0:
                raise validation_error("media file is empty", field_name=f"{field_name}_path")
            return cls(_data=None, _path=path, size=size, checksum=digest.hexdigest())

        assert base64_value is not None
        try:
            data = base64.b64decode(base64_value, validate=True)
        except Exception as exc:
            raise validation_error(
                f"value is not valid base64: {exc}", field_name=f"{field_name}_base64"
            ) from exc
        if not data:
            raise validation_error("decoded media is empty", field_name=f"{field_name}_base64")
        return cls(_data=data, _path=None, size=len(data), checksum=hashlib.md5(data).hexdigest())

    def _open(self) -> BinaryIO:
        if self._path is None:
            raise AssertionError("in-memory source has no file handle")
        return self._path.open("rb")

    def read_range(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.size:
            raise ValueError("upload operation range is outside the source")
        if self._data is not None:
            return self._data[offset : offset + length]
        with self._open() as stream:
            stream.seek(offset)
            return stream.read(length)


def png_has_alpha(source: BinarySource) -> bool | None:
    """Return PNG alpha-channel presence when the source is a recognizable PNG."""
    header = source.read_range(0, min(source.size, 26))
    if len(header) < 26 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        return None
    return header[25] in {4, 6}


def upload_operations(source: BinarySource, operations: list[dict[str, Any]], *, timeout: float = 120.0) -> None:
    """Upload all Apple-provided signed operations and fail with actionable state detail."""
    if not operations:
        raise asc_state_error(
            "App Store Connect returned no upload operations; the asset was not uploaded",
            code="STATE_ERROR",
        )
    with httpx.Client(timeout=timeout) as upload_http:
        for operation in operations:
            if not isinstance(operation, dict):
                raise asc_state_error(
                    "App Store Connect returned a malformed media upload operation",
                    code="STATE_ERROR",
                )
            try:
                offset = int(operation.get("offset", 0))
                length = int(operation.get("length", source.size - offset))
                url = operation["url"]
                method = operation.get("method", "PUT")
                headers = {
                    str(header["name"]): str(header["value"])
                    for header in operation.get("requestHeaders") or []
                }
                chunk = source.read_range(offset, length)
            except (KeyError, TypeError, ValueError) as exc:
                raise asc_state_error(
                    "App Store Connect returned a malformed media upload operation",
                    code="STATE_ERROR",
                ) from exc
            if not isinstance(url, str) or not url or not isinstance(method, str) or not method:
                raise asc_state_error(
                    "App Store Connect returned a malformed media upload operation",
                    code="STATE_ERROR",
                )
            if len(chunk) != length:
                raise asc_state_error(
                    "media source ended before an Apple upload operation was complete",
                    code="STATE_ERROR",
                )
            try:
                response = upload_http.request(method, url, content=chunk, headers=headers)
            except httpx.HTTPError as exc:
                raise asc_state_error(
                    "uploading media bytes failed before Apple accepted the request",
                    code="STATE_ERROR",
                ) from exc
            if not response.is_success:
                raise asc_state_error(
                    f"uploading media bytes failed (HTTP {response.status_code})",
                    code="STATE_ERROR",
                    detail={"status_code": response.status_code},
                )
