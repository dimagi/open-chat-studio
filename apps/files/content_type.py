"""Content-type detection helpers built around python-magic with safe fallbacks."""

from __future__ import annotations

import contextlib
import logging
import mimetypes
import pathlib
from typing import BinaryIO

import magic

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"
_MAGIC_SAMPLE_BYTES = 2048


def _magic_detect(content: bytes) -> str | None:
    """Run python-magic on up to the first 2048 bytes.

    Returns the detected MIME type, or None when detection fails or returns
    the generic ``application/octet-stream`` (treated as "no signal").
    """
    if not content:
        return None
    try:
        detected = magic.from_buffer(content[:_MAGIC_SAMPLE_BYTES], mime=True)
    except Exception:
        logger.exception("magic content-type detection failed")
        return None
    if not detected or detected == DEFAULT_CONTENT_TYPE:
        return None
    return detected


def detect_content_type(
    content: bytes = b"",
    *,
    filename: str = "",
    fallback: str = "",
) -> str:
    """Best-effort MIME detection from raw bytes with cascading fallbacks.

    Detection precedence:
        1. python-magic applied to the first 2048 bytes of ``content``
        2. ``mimetypes.guess_type(filename)`` when ``filename`` is provided
        3. ``fallback`` (e.g. a claimed Content-Type header from an external source)
        4. :data:`DEFAULT_CONTENT_TYPE` (``application/octet-stream``)
    """
    if detected := _magic_detect(content):
        return detected
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed
    return fallback or DEFAULT_CONTENT_TYPE


def detect_content_type_from_file(file_obj: BinaryIO) -> str:
    """Detect MIME from a file-like object.

    Reads up to 2048 bytes from ``file_obj`` (seeking back to 0 afterwards),
    then runs the same cascade as :func:`detect_content_type` using the
    object's ``name`` attribute as the filename fallback.
    """
    content = b""
    with contextlib.suppress(Exception):
        file_obj.seek(0)
        content = file_obj.read(_MAGIC_SAMPLE_BYTES)
        file_obj.seek(0)

    name = getattr(file_obj, "name", "") or ""
    with contextlib.suppress(Exception):
        name = pathlib.Path(name).name

    return detect_content_type(content, filename=name)
