"""API semplice per CLI e notebook (Facade, SPEC §7.10).

La facade compone le dipendenze (store, provenienza, osservatori, pipeline) e le inietta
nelle fasi (Dependency Injection). I metodi nominati della SPEC (``prepare_data``,
``generate_baseline``, ...) si aggiungono man mano che le fasi reali vengono implementate.
"""

from __future__ import annotations

from collections.abc import Sequence

import bench.pipeline.stages  # noqa: F401  (popola STAGES)
from bench.config.schema import ExperimentConfig
from bench.doctor import Doctor, DoctorReport, build_doctor_context
from bench.pipeline.observers import (
    InvariantObserver,
    LoggingObserver,
    StageObserver,
    TimingObserver,
)
from bench.pipeline.pipeline import Pipeline, PipelineReport, producers_from_registry
from bench.pipeline.stage import Cell, CellPlanner
from bench.registry import STAGES
from bench.store.artifact_store import ArtifactStore
from bench.store.manifest import ProvenanceCollector


class BenchmarkFacade:
    """Punto d'ingresso unico per eseguire fasi e diagnostica.

    Args:
        cfg: configurazione validata.
        extra_observers: osservatori aggiuntivi (es. nei test).
    """

    def __init__(
        self, cfg: ExperimentConfig, extra_observers: Sequence[StageObserver] = ()
    ) -> None:
        self.cfg = cfg
        self.store = ArtifactStore(cfg.paths.artifacts, producers_from_registry())
        self.provenance = ProvenanceCollector(cfg.paths.repo)
        self.timing = TimingObserver(cfg.paths.artifacts)
        self.observers: list[StageObserver] = [
            LoggingObserver(cfg.paths.artifacts, cfg.log_level),
            self.timing,
            InvariantObserver(self.store),
            *extra_observers,
        ]

    def run_stage(
        self, name: str, cells: Sequence[Cell] | None = None, force: bool | None = None
    ) -> PipelineReport:
        """Esegue una fase registrata sulle celle date (default: pianificate dalla config).

        Raises:
            ConfigError: se la fase non è registrata.
        """
        stage_cls = STAGES.get(name)
        planned = list(cells) if cells is not None else CellPlanner(self.cfg).cells_for(stage_cls)
        pipeline = Pipeline(
            [stage_cls.create(self.cfg)],
            self.store,
            self.observers,
            config=self.cfg,
            provenance=self.provenance,
        )
        return pipeline.run(planned, force=self.cfg.force if force is None else force)

    def doctor(self, gpu: bool = False) -> DoctorReport:
        """Esegue la diagnostica dell'installazione (``bench doctor``)."""
        return Doctor(build_doctor_context(self.cfg, gpu=gpu)).run()
