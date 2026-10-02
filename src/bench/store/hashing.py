"""Funzioni di hashing per file, byte e configurazioni."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from bench.domain.ids import canonical_json

_CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    """SHA-256 esadecimale di una sequenza di byte."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 esadecimale del contenuto di un file, letto a blocchi."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(obj: Any) -> str:
    """SHA-256 del JSON canonico di un oggetto (es. la configurazione risolta)."""
    return sha256_bytes(canonical_json(obj).encode("utf-8"))
