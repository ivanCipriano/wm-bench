"""Osservatori della pipeline: log, tempi, invarianti (Observer, SPEC §17)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from bench_contracts import append_jsonl

from bench.domain.errors import InvariantViolation
from bench.pipeline.stage import Cell, Stage
from bench.store.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageRun:
    """Esito di una coppia (fase, cella)."""

    stage: str
    cell: str
    status: Literal["RAN", "SKIPPED"]
    duration_s: float


class StageObserver:
    """Osservatore base: tutti gli hook sono no-op."""

    def on_start(self, stage: Stage, cell: Cell) -> None:
        """Prima di eseguire la fase sulla cella."""

    def on_end(self, stage: Stage, cell: Cell, run: StageRun) -> None:
        """Dopo un'esecuzione riuscita."""

    def on_skip(self, stage: Stage, cell: Cell) -> None:
        """Quando gli output sono già validi e la fase viene saltata."""

    def on_error(self, stage: Stage, cell: Cell, exc: BaseException) -> None:
        """Quando la fase solleva un'eccezione."""


class LoggingObserver(StageObserver):
    """Un file di log per fase e cella in ``<artifacts>/_logs/<fase>/<cella>.log``.

    Args:
        artifacts_root: radice degli artefatti.
        level: livello di logging (``INFO``, ``DEBUG``, ...).
    """

    def __init__(self, artifacts_root: Path, level: str = "INFO") -> None:
        self.root = artifacts_root / "_logs"
        self.level = logging.getLevelName(level)
        self._handler: logging.Handler | None = None

    def on_start(self, stage: Stage, cell: Cell) -> None:
        path = self.root / stage.name / f"{cell.slug()}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(self.level)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        bench_logger = logging.getLogger("bench")
        bench_logger.addHandler(handler)
        if bench_logger.getEffectiveLevel() > self.level:
            bench_logger.setLevel(self.level)
        self._handler = handler
        logger.info("stage %s cell %s: start", stage.name, cell.key())

    def _close(self) -> None:
        if self._handler is not None:
            logging.getLogger("bench").removeHandler(self._handler)
            self._handler.close()
            self._handler = None

    def on_end(self, stage: Stage, cell: Cell, run: StageRun) -> None:
        logger.info("stage %s cell %s: done in %.2fs", stage.name, cell.key(), run.duration_s)
        self._close()

    def on_skip(self, stage: Stage, cell: Cell) -> None:
        logger.info("stage %s cell %s: SKIPPED (outputs already complete)", stage.name, cell.key())

    def on_error(self, stage: Stage, cell: Cell, exc: BaseException) -> None:
        logger.error("stage %s cell %s: failed: %s", stage.name, cell.key(), exc)
        self._close()


class TimingObserver(StageObserver):
    """Registra i tempi per fase e cella (SPEC §13.7), in memoria e in JSONL.

    Args:
        artifacts_root: radice degli artefatti (``None`` = solo in memoria).
    """

    def __init__(self, artifacts_root: Path | None = None) -> None:
        self.path = artifacts_root / "_timing" / "timings.jsonl" if artifacts_root else None
        self.runs: list[StageRun] = []

    def on_end(self, stage: Stage, cell: Cell, run: StageRun) -> None:
        self.runs.append(run)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            append_jsonl(
                self.path,
                {
                    "stage": run.stage,
                    "cell": run.cell,
                    "duration_s": run.duration_s,
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )


class InvariantObserver(StageObserver):
    """Controlla l'invariante I1 dopo ogni fase e solleva ``InvariantViolation``.

    Per ogni output il manifest deve dichiarare ``n_rows_out`` uguale a
    ``n_rows_expected``; per le tabelle Parquet si confronta anche il numero reale di righe.

    Args:
        store: repository degli artefatti.
    """

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def on_end(self, stage: Stage, cell: Cell, run: StageRun) -> None:
        for ref in stage.outputs(cell):
            manifest = self.store.read_manifest(ref)
            if manifest is None:
                raise InvariantViolation(f"{stage.name}/{cell.key()}: no manifest for {ref.path}")
            if (manifest.n_rows_out is None) != (manifest.n_rows_expected is None):
                raise InvariantViolation(
                    f"{stage.name}/{cell.key()}: {ref.path} must declare both "
                    "n_rows_out and n_rows_expected (I1)"
                )
            if manifest.n_rows_out != manifest.n_rows_expected:
                raise InvariantViolation(
                    f"I1 violated by {stage.name}/{cell.key()}: {ref.path} has "
                    f"{manifest.n_rows_out} rows, expected {manifest.n_rows_expected}"
                )
            actual = self.store.count_rows(ref)
            if actual is not None and actual != manifest.n_rows_out:
                raise InvariantViolation(
                    f"I1 violated by {stage.name}/{cell.key()}: {ref.path} contains {actual} "
                    f"rows but the manifest declares {manifest.n_rows_out}"
                )
