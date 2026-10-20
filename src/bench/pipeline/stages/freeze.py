"""Fase ``freeze``: congelamento della configurazione definitiva di un metodo (ADR-013, M10).

Una cella = un metodo; i modelli e i linguaggi sono quelli della configurazione (i linguaggi non
supportati dal metodo si registrano come ``not_applicable``). Prima di scrivere
``freeze/<metodo>.json`` controlla, per ogni (modello, linguaggio):

- la scelta definitiva ``hpo/selected_L1L2/...`` con livelli L1+L2 e non provvisoria;
- la soglia L1+L2 della configurazione scelta, non provvisoria;
- che i candidati della scelta (``hpo/candidates_L1L2/...``) siano quelli della griglia attuale.

Se un controllo fallisce la fase si ferma con l'elenco di quello che manca e non scrive niente.
Il record è idempotente: la pipeline lo salta se esiste già (``force`` lo rifiuta, ADR-013).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import SelectedConfig, ThresholdSet
from bench.hpo.freeze import (
    FINAL_LEVELS,
    FreezeRecord,
    FrozenCell,
    data_sha256,
    file_sha256,
    freeze_ref,
    load_freeze,
)
from bench.hpo.grid import grid_path
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.calibrate import threshold_ref
from bench.pipeline.stages.tune import TuneStage, candidates_ref, selected_ref
from bench.pipeline.stages.watermark import make_adapter
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)


@STAGES.register("freeze")
class FreezeStage(Stage):
    """Congela la scelta definitiva di un metodo prima della parte di test."""

    name: ClassVar[str] = "freeze"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"freeze"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method",)

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._adapters: dict[str, Any] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("freeze needs the experiment configuration")
        return self.config

    def adapter(self, method: str) -> Any:
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return self._adapters[method]

    @staticmethod
    def _require(cell: Cell) -> str:
        if not cell.method:
            raise ConfigError(f"freeze needs method: {cell.key()}")
        return cell.method

    def _pairs(self) -> list[tuple[str, str]]:
        cfg = self._cfg()
        return [(m, str(lang)) for m in cfg.models for lang in cfg.languages]

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        """Nessun input obbligatorio per la pipeline: i controlli sono in ``run``, così un
        congelamento impossibile elenca **tutto** quello che manca invece del primo file."""
        self._require(cell)
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [freeze_ref(self._require(cell))]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method = self._require(cell)
        if load_freeze(ctx.store, method) is not None:
            raise ConfigError(f"{method} is already frozen: a new freeze is a deviation (ADR-013)")
        adapter = self.adapter(method)
        tune = TuneStage(cfg)
        expected = sorted(h for h, _ in tune.candidates(method))  # tutte le celle
        problems: list[str] = []
        cells: list[FrozenCell] = []
        refs: list[ArtifactRef] = []
        for model_id, language in self._pairs():
            if not adapter.supports(language):
                cells.append(FrozenCell(model_id=model_id, language=language, not_applicable=True))
                continue
            local = sorted(h for h, _ in tune.candidates(method, model_id, language))
            frozen = self._cell(ctx, method, model_id, language, local, problems, refs)
            if frozen is not None:
                cells.append(frozen)
        if problems:
            raise ConfigError(
                f"{method} cannot be frozen:\n" + "\n".join(f"  - {p}" for p in problems)
            )
        record = FreezeRecord(
            method=method,
            frozen_at=datetime.now(UTC),
            levels=list(FINAL_LEVELS),
            grid_sha256=file_sha256(grid_path(method)),
            candidate_hashes=expected,
            cells=cells,
        )
        ref = freeze_ref(method)
        ctx.store.write_model(
            ref,
            record,
            ctx.make_manifest(
                ref,
                inputs=refs,
                n_rows_in=None,
                n_rows_out=len(cells),
                n_rows_expected=len(cells),
                extra={"levels": list(FINAL_LEVELS), "n_cells": len(cells)},
            ),
        )
        logger.info("%s frozen: %d cells", method, len(cells))

    def _cell(
        self,
        ctx: StageContext,
        method: str,
        model_id: str,
        language: str,
        expected: list[str],
        problems: list[str],
        refs: list[ArtifactRef],
    ) -> FrozenCell | None:
        """Cella congelata di (modello, linguaggio); i problemi trovati vanno in ``problems``."""
        name = f"{model_id} {language}"
        sel_ref = selected_ref(method, model_id, language, FINAL_LEVELS)
        cand_ref = candidates_ref(method, model_id, language, FINAL_LEVELS)
        refs.extend([sel_ref, cand_ref])
        if not ctx.store.exists(sel_ref):
            problems.append(f"{name}: no final selection {sel_ref.path} (run tune on L1+L2)")
            return None
        selected = ctx.store.read_model(sel_ref, SelectedConfig)
        if sorted(selected.levels) != list(FINAL_LEVELS) or selected.provisional:
            problems.append(f"{name}: selection is provisional (levels {selected.levels})")
        if selected.not_applicable or selected.config_hash is None:
            problems.append(f"{name}: selection is NOT_APPLICABLE but the method supports it")
            return None
        if ctx.store.exists(cand_ref):
            table = ctx.store.read_table(cand_ref)
            got = sorted({str(h) for h in table["config_hash"]})
            if got != expected:
                problems.append(
                    f"{name}: the HPO grid changed after tune ({len(got)} candidates "
                    f"in the selection, {len(expected)} in configs/hpo)"
                )
        else:
            problems.append(f"{name}: no candidates table {cand_ref.path}")
        thr_ref = threshold_ref(method, model_id, language, selected.config_hash, FINAL_LEVELS)
        if not ctx.store.exists(thr_ref):
            problems.append(f"{name}: no final threshold {thr_ref.path}")
            return None
        thresholds = ctx.store.read_model(thr_ref, ThresholdSet)
        if thresholds.provisional or sorted(thresholds.levels) != list(FINAL_LEVELS):
            problems.append(f"{name}: threshold is provisional (levels {thresholds.levels})")
        refs.append(thr_ref)
        return FrozenCell(
            model_id=model_id,
            language=language,
            config_hash=selected.config_hash,
            hparams=dict(selected.hparams),
            selected_sha256=data_sha256(ctx.store, sel_ref),
            threshold=thresholds.threshold,
            threshold_sha256=data_sha256(ctx.store, thr_ref),
            constraint_satisfied=selected.constraint_satisfied,
        )
