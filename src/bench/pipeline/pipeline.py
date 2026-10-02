"""Esecuzione delle fasi sulle celle, con salto degli output già validi (SPEC §7.10)."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from bench.config.schema import ExperimentConfig
from bench.domain.errors import InvariantViolation, MissingInputError
from bench.pipeline.observers import StageObserver, StageRun
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.artifact_store import ArtifactStore
from bench.store.manifest import ProvenanceCollector

logger = logging.getLogger(__name__)


def producers_from_registry() -> dict[str, str]:
    """Mappa ``kind`` → nome della fase registrata che lo produce."""
    result: dict[str, str] = {}
    for name in STAGES.names():
        for kind in STAGES.get(name).output_kinds:
            result[kind] = name
    return result


@dataclass
class PipelineReport:
    """Esiti di un lancio della pipeline."""

    runs: list[StageRun] = field(default_factory=list)

    @property
    def ran(self) -> int:
        """Numero di coppie (fase, cella) eseguite."""
        return sum(r.status == "RAN" for r in self.runs)

    @property
    def skipped(self) -> int:
        """Numero di coppie (fase, cella) saltate."""
        return sum(r.status == "SKIPPED" for r in self.runs)

    def summary(self) -> str:
        """Riepilogo su una riga per ogni coppia (fase, cella)."""
        lines = [f"{r.status:<8} {r.stage} [{r.cell}] {r.duration_s:.2f}s" for r in self.runs]
        lines.append(f"total: {self.ran} ran, {self.skipped} skipped")
        return "\n".join(lines)


class Pipeline:
    """Catena di fasi (Chain of Responsibility, SPEC §6).

    Args:
        stages: fasi, nell'ordine di esecuzione.
        store: repository degli artefatti.
        observers: osservatori notificati per ogni coppia (fase, cella).
        config: configurazione validata, passata ai contesti delle fasi.
        provenance: raccoglitore della provenienza per i manifest.
    """

    def __init__(
        self,
        stages: Sequence[Stage],
        store: ArtifactStore,
        observers: Sequence[StageObserver],
        *,
        config: ExperimentConfig,
        provenance: ProvenanceCollector,
    ) -> None:
        self.stages = list(stages)
        self.store = store
        self.observers = list(observers)
        self.config = config
        self.provenance = provenance

    def run(self, cells: Sequence[Cell], force: bool = False) -> PipelineReport:
        """Esegue ogni fase su ogni cella.

        Se tutti gli output esistono e sono validi e ``force=False`` la coppia viene
        saltata. Gli errori di fase interrompono il lancio (i fallimenti dei singoli
        campioni sono stati, non eccezioni).

        Raises:
            MissingInputError: se manca un input; il messaggio indica la fase da lanciare.
            InvariantViolation: se la fase non scrive i suoi output o viola I1.
        """
        report = PipelineReport()
        for stage in self.stages:
            for cell in cells:
                report.runs.append(self._run_one(stage, cell, force))
        return report

    def _run_one(self, stage: Stage, cell: Cell, force: bool) -> StageRun:
        outputs = stage.outputs(cell)
        if not force and outputs and all(self.store.exists(ref) for ref in outputs):
            for obs in self.observers:
                obs.on_skip(stage, cell)
            return StageRun(stage.name, cell.key(), "SKIPPED", 0.0)

        for ref in stage.inputs(cell):
            if not self.store.exists(ref):
                producer = self.store.producers.get(ref.kind)
                raise MissingInputError(str(ref.path), producer)

        ctx = StageContext(self.config, self.store, self.provenance, stage.name, cell)
        for obs in self.observers:
            obs.on_start(stage, cell)
        start = time.perf_counter()
        try:
            stage.run(cell, ctx)
            missing = [str(ref.path) for ref in outputs if not self.store.exists(ref)]
            if missing:
                raise InvariantViolation(f"stage {stage.name} did not write {missing}")
            run = StageRun(stage.name, cell.key(), "RAN", time.perf_counter() - start)
            for obs in self.observers:
                obs.on_end(stage, cell, run)
        except BaseException as exc:
            for obs in self.observers:
                obs.on_error(stage, cell, exc)
            raise
        return run
