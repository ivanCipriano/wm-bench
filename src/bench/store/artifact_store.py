"""Repository degli artefatti: unico punto di accesso a dati e manifest (SPEC §7.11).

Protocollo di scrittura (atomico rispetto ai crash):

1. si cancella l'eventuale manifest precedente, così un artefatto in riscrittura non
   risulta mai "complete";
2. i dati si scrivono in un file temporaneo nella stessa cartella, con ``fsync``, e si
   rinominano con ``os.replace``;
3. il manifest si scrive allo stesso modo, con ``status=complete``, SHA-256 e dimensione
   dei dati.

Un artefatto esiste solo se il file c'è, il manifest è ``complete`` e la dimensione coincide.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import pandas as pd
import pyarrow.parquet as pq
from pydantic import BaseModel

from bench.domain.errors import MissingInputError
from bench.store.hashing import sha256_file
from bench.store.manifest import Manifest
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


def _fsync_dir(directory: Path) -> None:
    # Su POSIX rende persistente il rename; su Windows aprire una cartella non è possibile.
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ArtifactStore:
    """Lettura e scrittura degli artefatti sotto una radice.

    Args:
        root: radice degli artefatti (``paths.artifacts``).
        producers: mappa ``kind`` → nome della fase che lo produce, usata nei messaggi
            di ``MissingInputError``.
    """

    def __init__(self, root: Path, producers: dict[str, str] | None = None) -> None:
        self.root = root
        self.producers = dict(producers or {})

    # ------------------------------------------------------------------ percorsi
    def path_of(self, ref: ArtifactRef) -> Path:
        """Percorso assoluto del file di dati."""
        return self.root.joinpath(*ref.path.parts)

    def manifest_of(self, ref: ArtifactRef) -> Path:
        """Percorso assoluto del manifest."""
        return self.root.joinpath(*ref.manifest_path().parts)

    # ------------------------------------------------------------------ stato
    def read_manifest(self, ref: ArtifactRef) -> Manifest | None:
        """Legge il manifest, o ``None`` se manca o non è leggibile."""
        path = self.manifest_of(ref)
        if not path.is_file():
            return None
        try:
            return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("unreadable manifest %s: %s", path, exc)
            return None

    def exists(self, ref: ArtifactRef) -> bool:
        """``True`` se file e manifest ``complete`` esistono e la dimensione coincide."""
        data = self.path_of(ref)
        if not data.is_file():
            return False
        manifest = self.read_manifest(ref)
        return (
            manifest is not None
            and manifest.status == "complete"
            and manifest.data_size == data.stat().st_size
        )

    def verify(self, ref: ArtifactRef) -> bool:
        """Come ``exists``, ma ricalcola anche lo SHA-256 dei dati."""
        if not self.exists(ref):
            return False
        manifest = self.read_manifest(ref)
        return manifest is not None and manifest.data_sha256 == sha256_file(self.path_of(ref))

    def count_rows(self, ref: ArtifactRef) -> int | None:
        """Numero reale di righe di un Parquet (dai metadati), ``None`` per altri formati."""
        path = self.path_of(ref)
        if path.suffix != ".parquet" or not path.is_file():
            return None
        return int(pq.ParquetFile(path).metadata.num_rows)

    def _require(self, ref: ArtifactRef) -> Path:
        if not self.exists(ref):
            raise MissingInputError(str(ref.path), self.producers.get(ref.kind))
        return self.path_of(ref)

    # ------------------------------------------------------------------ scrittura
    def _atomic_write(
        self, ref: ArtifactRef, writer: Callable[[Path], None], manifest: Manifest
    ) -> Manifest:
        data_path = self.path_of(ref)
        manifest_path = self.manifest_of(ref)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.unlink(missing_ok=True)

        tmp = data_path.with_name(f".{data_path.name}.tmp-{uuid.uuid4().hex}")
        try:
            writer(tmp)
            with open(tmp, "r+b") as handle:  # "r+b": su Windows fsync richiede la scrittura
                os.fsync(handle.fileno())
            os.replace(tmp, data_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
        _fsync_dir(data_path.parent)

        final = manifest.model_copy(
            update={
                "kind": ref.kind,
                "status": "complete",
                "data_sha256": sha256_file(data_path),
                "data_size": data_path.stat().st_size,
                "finished_at": manifest.finished_at or datetime.now(UTC),
            }
        )
        self._write_text_atomic(manifest_path, final.model_dump_json(indent=2))
        return final

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
        _fsync_dir(path.parent)

    def write_table(self, ref: ArtifactRef, df: pd.DataFrame, manifest: Manifest) -> Manifest:
        """Scrive una tabella Parquet e il suo manifest; restituisce il manifest finale."""
        return self._atomic_write(ref, lambda p: df.to_parquet(p, index=False), manifest)

    def write_model(self, ref: ArtifactRef, obj: BaseModel, manifest: Manifest) -> Manifest:
        """Scrive un modello Pydantic come JSON e il suo manifest."""

        def writer(path: Path) -> None:
            path.write_text(obj.model_dump_json(indent=2), encoding="utf-8")

        return self._atomic_write(ref, writer, manifest)

    # ------------------------------------------------------------------ lettura
    def read_table(self, ref: ArtifactRef) -> pd.DataFrame:
        """Legge una tabella Parquet.

        Raises:
            MissingInputError: se l'artefatto non esiste (o non è completo).
        """
        return pd.read_parquet(self._require(ref))

    def read_model(self, ref: ArtifactRef, cls: type[M]) -> M:
        """Legge un modello Pydantic scritto con ``write_model``.

        Raises:
            MissingInputError: se l'artefatto non esiste (o non è completo).
        """
        return cls.model_validate(json.loads(self._require(ref).read_text(encoding="utf-8")))
