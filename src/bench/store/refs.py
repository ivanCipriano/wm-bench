"""Riferimenti agli artefatti, relativi alla radice ``artifacts/``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

MANIFEST_SUFFIX = ".manifest.json"


@dataclass(frozen=True)
class ArtifactRef:
    """Riferimento a un artefatto.

    Attributes:
        kind: tipo di artefatto (``"problems"``, ``"baseline"``, ...): permette di
            risalire alla fase che lo produce.
        path: percorso relativo alla radice degli artefatti, con ``/`` come separatore.
    """

    kind: str
    path: PurePosixPath

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if not self.kind:
            raise ValueError("ArtifactRef.kind must not be empty")
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"ArtifactRef.path must be relative and inside the root: {path}")
        if path.name.endswith(MANIFEST_SUFFIX):
            raise ValueError(f"ArtifactRef.path must not be a manifest: {path}")
        object.__setattr__(self, "path", path)

    def manifest_path(self) -> PurePosixPath:
        """Percorso del manifest associato: ``<file>.manifest.json``."""
        return self.path.with_name(self.path.name + MANIFEST_SUFFIX)

    @classmethod
    def of(cls, kind: str, path: str) -> ArtifactRef:
        """Costruisce un riferimento da una stringa di percorso."""
        return cls(kind, PurePosixPath(path))
