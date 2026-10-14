"""Detect image type from magic bytes; the client's Content-Type is ignored."""

from __future__ import annotations

import importlib.util

_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def heif_supported() -> bool:
    return importlib.util.find_spec("pillow_heif") is not None


def sniff(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "image/heic" if data[8:12] != b"mif1" else "image/heif"
    return None


def accepted_mime(data: bytes) -> str | None:
    mime = sniff(data)
    if mime in ("image/heic", "image/heif") and not heif_supported():
        return None
    return mime
