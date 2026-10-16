"""Fase ``tune``: selezione degli iperparametri (SPEC §14, Milestone 8).

Una cella = (metodo, modello, linguaggio). Per ogni candidato della griglia
(``configs/hpo/<metodo>.yaml``; senza griglia il solo default) e su tutti i livelli della
configurazione, parte di sviluppo:

- ``tpr_dev``: positivi rilevati alla soglia del candidato (fase ``calibrate``), con il punteggio
  principale del metodo (``primary_view``, D16) e i positivi con inserimento FAILED non rilevati
  (I2);
- ``pass1_dev`` dei campioni marcati e ``pass1_baseline_dev`` della baseline di riferimento: la
  gemella per i metodi con ``twin_baseline`` (D25), altrimenti la baseline LLM (per ACW è la
  baseline da cui deriva il codice trasformato, SPEC §14.6); ``pass1_drop_pp`` in punti
  percentuali, positivo = perdita.

Criterio: :class:`bench.hpo.criterion.ConstrainedTprCriterion` con ``hpo.max_pass1_drop_pp``.

Output:
- ``hpo/candidates/<metodo>/<modello>/<linguaggio>.parquet``: tutti i candidati;
- ``hpo/selected/<metodo>/<modello>/<linguaggio>.json``: la configurazione scelta
  (``SelectedConfig``), ``provisional`` se le soglie lo sono (TODO 23).

Con livelli diversi dal solo L1 (scelta definitiva su L1+L2, M9) le cartelle diventano
``hpo/candidates_L1L2/`` e ``hpo/selected_L1L2/``, con le soglie dello stesso insieme di livelli: la
scelta provvisoria della M8 resta e non fa saltare quella definitiva.

Un linguaggio non supportato dal metodo dà una selezione ``not_applicable`` senza candidati.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, ClassVar

import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import SelectedConfig, ThresholdSet
from bench.hpo.criterion import Candidate, ConstrainedTprCriterion
from bench.hpo.freeze import ensure_not_frozen
from bench.hpo.grid import GridPoint, grid_path, load_grid
from bench.metrics.calibration import detected
from bench.metrics.uncertainty import pass_at_1
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.calibrate import (
    CALIBRATION_SPLIT,
    levels_tag,
    primary_view,
    threshold_ref,
)
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.watermark import make_adapter
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)


def _hpo_folder(kind: str, levels: Sequence[str]) -> str:
    tag = levels_tag(levels)
    return f"hpo/{kind}" + (f"_{tag}" if tag else "")


def candidates_ref(
    method: str, model_id: str, language: str, levels: Sequence[str] = ("L1",)
) -> ArtifactRef:
    folder = _hpo_folder("candidates", levels)
    return ArtifactRef.of("hpo_candidates", f"{folder}/{method}/{model_id}/{language}.parquet")


def selected_ref(
    method: str, model_id: str, language: str, levels: Sequence[str] = ("L1",)
) -> ArtifactRef:
    folder = _hpo_folder("selected", levels)
    return ArtifactRef.of("hpo_selected", f"{folder}/{method}/{model_id}/{language}.json")


def detection_rate(positives: pd.DataFrame, threshold: float) -> tuple[int, int]:
    """(rilevati, positivi) alla soglia; NOT_APPLICABLE escluso, inserimento FAILED = no."""
    table = positives[positives["status"] != "NOT_APPLICABLE"]
    scores = pd.to_numeric(table["score"], errors="coerce")
    embed = table["embed_status"] if "embed_status" in table else pd.Series([None] * len(table))
    hits = sum(
        detected(
            None if pd.isna(s) else float(s),
            str(st),
            None if pd.isna(e) else str(e),
            threshold,
        )
        for s, st, e in zip(scores, table["status"], embed, strict=True)
    )
    return int(hits), len(table)


@STAGES.register("tune")
class TuneStage(Stage):
    """Selezione della configurazione per (metodo, modello, linguaggio)."""

    name: ClassVar[str] = "tune"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"hpo_candidates", "hpo_selected"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "language")

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._adapters: dict[str, Any] = {}
        self._override = False

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("tune needs the experiment configuration")
        return self.config

    def adapter(self, method: str) -> Any:
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return self._adapters[method]

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str]:
        if not (cell.method and cell.model_id and cell.language):
            raise ConfigError(f"tune needs method, model_id, language: {cell.key()}")
        return cell.method, cell.model_id, cell.language

    def levels(self) -> list[str]:
        return sorted(str(level) for level in self._cfg().levels)

    def candidates(self, method: str) -> list[tuple[str, GridPoint]]:
        """(hash, punto) dei candidati: la griglia del metodo, o il solo default."""
        adapter = self.adapter(method)
        if grid_path(method).is_file():
            points = load_grid(method).candidates()
        else:
            points = [GridPoint(adapter.default_hparams())]
        out: list[tuple[str, GridPoint]] = []
        for point in points:
            h = adapter.config_hash(point.hparams)
            if all(h != seen for seen, _ in out):
                out.append((h, point))
        return out

    def _applicable(self, cell: Cell) -> bool:
        return bool(self.adapter(str(cell.method)).supports(str(cell.language)))

    def _refs(self, cell: Cell, cfg_hash: str) -> dict[str, list[ArtifactRef]]:
        method, model_id, language = self._require(cell)
        twin = bool(getattr(self.adapter(method), "twin_baseline", False))
        refs: dict[str, list[ArtifactRef]] = {
            "threshold": [threshold_ref(method, model_id, language, cfg_hash, self.levels())],
            "positives": [],
            "marked": [],
            "reference": [],
        }
        split = CALIBRATION_SPLIT
        for level in self.levels():
            refs["positives"].append(
                detection_ref(method, model_id, cfg_hash, "positives", level, language, split)
            )
            base = Cell(model_id=model_id, level=level, language=language, split=split)
            marked = replace(base, source="llm_watermarked", method=method)
            refs["marked"].append(execution_ref(marked, cfg_hash))
            if twin:
                twin_cell = replace(base, source="baseline_twin", method=method)
                refs["reference"].append(execution_ref(twin_cell, cfg_hash))
            else:
                refs["reference"].append(execution_ref(replace(base, source="llm_baseline"), None))
        return refs

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        method, _, _ = self._require(cell)
        if not self._applicable(cell):
            return []
        refs: list[ArtifactRef] = []
        for cfg_hash, _ in self.candidates(method):
            for group in self._refs(cell, cfg_hash).values():
                refs.extend(r for r in group if r not in refs)
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, language = self._require(cell)
        levels = self.levels()
        return [
            candidates_ref(method, model_id, language, levels),
            selected_ref(method, model_id, language, levels),
        ]

    # ------------------------------------------------------------------ esecuzione
    def _evaluate(
        self, ctx: StageContext, cell: Cell, cfg_hash: str, point: GridPoint
    ) -> tuple[Candidate, ThresholdSet]:
        refs = self._refs(cell, cfg_hash)
        adapter = self.adapter(str(cell.method))
        thresholds = ctx.store.read_model(refs["threshold"][0], ThresholdSet)
        positives = pd.concat(
            [primary_view(ctx.store.read_table(r), adapter) for r in refs["positives"]],
            ignore_index=True,
        )
        n_detected, n_positives = detection_rate(positives, thresholds.threshold)
        marked = pd.concat([ctx.store.read_table(r) for r in refs["marked"]], ignore_index=True)
        reference = pd.concat(
            [ctx.store.read_table(r) for r in refs["reference"]], ignore_index=True
        )
        p_marked, _ = pass_at_1(marked)
        p_reference, _ = pass_at_1(reference)
        candidate = Candidate(
            config_hash=cfg_hash,
            hparams=dict(point.hparams),
            n_positives=n_positives,
            n_detected=n_detected,
            tpr_dev=n_detected / n_positives if n_positives else float("nan"),
            pass1_dev=p_marked,
            pass1_baseline_dev=p_reference,
            pass1_drop_pp=(p_reference - p_marked) * 100.0,
        )
        return candidate, thresholds

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, language = self._require(cell)
        self._override = ensure_not_frozen(
            ctx.store, method, self.name, cfg.hpo.allow_frozen_override
        )
        criterion = ConstrainedTprCriterion(cfg.hpo.max_pass1_drop_pp)
        levels = self.levels()
        now = datetime.now(UTC)
        if not self._applicable(cell):
            selected = SelectedConfig(
                method=method,
                model_id=model_id,
                language=language,
                config_hash=None,
                max_pass1_drop_pp=criterion.max_pass1_drop_pp,
                n_candidates=0,
                levels=levels,
                not_applicable=True,
                created_at=now,
            )
            self._write(ctx, cell, [], [], selected, None)
            return
        evaluated = [self._evaluate(ctx, cell, h, p) for h, p in self.candidates(method)]
        candidates = [c for c, _ in evaluated]
        provisional = any(t.provisional for _, t in evaluated)
        selection = criterion.select(candidates)
        best = selection.candidate
        selected = SelectedConfig(
            method=method,
            model_id=model_id,
            language=language,
            config_hash=best.config_hash,
            hparams=best.hparams,
            tpr_dev=best.tpr_dev,
            pass1_dev=best.pass1_dev,
            pass1_baseline_dev=best.pass1_baseline_dev,
            pass1_drop_pp=best.pass1_drop_pp,
            constraint_satisfied=selection.constraint_satisfied,
            max_pass1_drop_pp=criterion.max_pass1_drop_pp,
            n_candidates=len(candidates),
            n_admissible=selection.n_admissible,
            provisional=provisional,
            levels=levels,
            created_at=now,
        )
        thresholds = [t.threshold for _, t in evaluated]
        self._write(ctx, cell, candidates, thresholds, selected, criterion)
        if not selection.constraint_satisfied:
            logger.warning(
                "%s: no configuration within %.1f pp of Pass@1 loss; chose the smallest loss "
                "(%.1f pp)",
                cell.key(),
                criterion.max_pass1_drop_pp,
                best.pass1_drop_pp,
            )

    def _write(
        self,
        ctx: StageContext,
        cell: Cell,
        candidates: list[Candidate],
        thresholds: list[float],
        selected: SelectedConfig,
        criterion: ConstrainedTprCriterion | None,
    ) -> None:
        rows = [
            {
                "config_hash": c.config_hash,
                "hparams_json": json.dumps(c.hparams, sort_keys=True),
                "threshold": t,
                "n_positives": c.n_positives,
                "n_detected": c.n_detected,
                "tpr_dev": c.tpr_dev,
                "pass1_dev": c.pass1_dev,
                "pass1_baseline_dev": c.pass1_baseline_dev,
                "pass1_drop_pp": c.pass1_drop_pp,
                "admissible": bool(criterion.admissible(c)) if criterion else False,
                "selected": c.config_hash == selected.config_hash,
            }
            for c, t in zip(candidates, thresholds, strict=True)
        ]
        df = pd.DataFrame(
            rows,
            columns=[
                "config_hash",
                "hparams_json",
                "threshold",
                "n_positives",
                "n_detected",
                "tpr_dev",
                "pass1_dev",
                "pass1_baseline_dev",
                "pass1_drop_pp",
                "admissible",
                "selected",
            ],
        )
        cand_ref, sel_ref = self.outputs(cell)
        extra = {
            "provisional": selected.provisional,
            "levels": selected.levels,
            "constraint_satisfied": selected.constraint_satisfied,
            "not_applicable": selected.not_applicable,
            "max_pass1_drop_pp": selected.max_pass1_drop_pp,
            # Nuova scelta per un metodo congelato (ADR-013): deviazione da registrare.
            "frozen_override": self._override,
        }
        inputs = self.inputs(cell)
        ctx.store.write_table(
            cand_ref,
            df,
            ctx.make_manifest(
                cand_ref,
                inputs=inputs,
                n_rows_in=len(candidates),
                n_rows_out=len(df),
                n_rows_expected=len(candidates),
                extra=extra,
            ),
        )
        ctx.store.write_model(
            sel_ref,
            selected,
            ctx.make_manifest(
                sel_ref,
                inputs=inputs,
                n_rows_in=None,
                n_rows_out=None,
                n_rows_expected=None,
                config_hash=selected.config_hash,
                extra=extra,
            ),
        )
        logger.info(
            "%s: %d candidates, selected %s (TPR %s, ΔPass@1 %s pp, constraint %s)",
            cell.key(),
            len(candidates),
            selected.config_hash,
            selected.tpr_dev,
            selected.pass1_drop_pp,
            selected.constraint_satisfied,
        )
