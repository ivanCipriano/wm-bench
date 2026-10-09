"""Fase ``calibrate``: soglie congelate sui negativi umani di sviluppo (SPEC §13.2, I3).

Una cella = (metodo, modello, linguaggio): una soglia per la configurazione di default del metodo,
calcolata sui punteggi ``neg_human`` della parte **dev** di tutti i livelli della configurazione.
La SPEC usa L1+L2; finché manca L2 (M9) la soglia è **provvisoria** (``provisional=True``,
``levels`` = livelli usati; decisione dell'utente del 9 ottobre 2026) e la fase ``metrics`` non la
usa sul test. Output: ``thresholds/<metodo>/<modello>/<lang>/<cfg>.json`` (``ThresholdSet``).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import Any, ClassVar

import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import ThresholdSet
from bench.metrics.calibration import MIN_SCORE, ThresholdCalibrator
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.watermark import make_adapter
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

CALIBRATION_SPLIT = "dev"
FULL_LEVELS = frozenset({"L1", "L2"})  # livelli previsti dalla SPEC per le soglie definitive


def threshold_ref(method: str, model_id: str, language: str, cfg_hash: str) -> ArtifactRef:
    """Soglia congelata di (metodo, modello, linguaggio, configurazione)."""
    return ArtifactRef.of("threshold", f"thresholds/{method}/{model_id}/{language}/{cfg_hash}.json")


def json_threshold(value: float) -> float | str:
    """Soglia per i manifest: ``"Infinity"`` invece di ``null`` per la soglia irraggiungibile."""
    return value if math.isfinite(value) else "Infinity"


def with_secondary(table: pd.DataFrame, adapter: Any, name: str) -> pd.DataFrame:
    """La tabella di rilevazione con il punteggio secondario ``name`` al posto di ``score``;
    senza valore la rilevazione conta come non riuscita (punteggio minimo)."""
    values = [
        adapter.secondary_score(name, json.loads(raw)) if status == "OK" else None
        for raw, status in zip(table["extra_json"], table["status"], strict=True)
    ]
    out = table.copy()
    out["score"] = [math.nan if v is None else v for v in values]
    out["status"] = [
        s if v is not None else ("FAILED" if s == "OK" else s)
        for s, v in zip(table["status"], values, strict=True)
    ]
    return out


def negative_scores(table: pd.DataFrame) -> list[float]:
    """Punteggi dei negativi: ``MIN_SCORE`` per le rilevazioni non riuscite (SPEC §13.2)."""
    ok = table["status"] == "OK"
    scores = pd.to_numeric(table["score"], errors="coerce")
    return [
        float(s) if good and pd.notna(s) else MIN_SCORE for good, s in zip(ok, scores, strict=True)
    ]


@STAGES.register("calibrate")
class CalibrateStage(Stage):
    """Soglia all'FPR obiettivo sui negativi umani di sviluppo."""

    name: ClassVar[str] = "calibrate"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"threshold"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "language")

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._hashes: dict[str, str] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("calibrate needs the experiment configuration")
        return self.config

    def config_hash(self, method: str) -> str:
        if method not in self._hashes:
            adapter = make_adapter(self._cfg(), method)
            self._hashes[method] = str(adapter.config_hash(adapter.default_hparams()))
        return self._hashes[method]

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str]:
        if not (cell.method and cell.model_id and cell.language):
            raise ConfigError(f"calibrate needs method, model_id, language: {cell.key()}")
        return cell.method, cell.model_id, cell.language

    def levels(self) -> list[str]:
        return sorted(str(level) for level in self._cfg().levels)

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, language = self._require(cell)
        cfg_hash = self.config_hash(method)
        return [
            detection_ref(
                method, model_id, cfg_hash, "neg_human", level, language, CALIBRATION_SPLIT
            )
            for level in self.levels()
        ]

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, language = self._require(cell)
        return [threshold_ref(method, model_id, language, self.config_hash(method))]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, language = self._require(cell)
        cfg_hash = self.config_hash(method)
        frames = [ctx.store.read_table(ref) for ref in self.inputs(cell)]
        table = pd.concat(frames, ignore_index=True)
        calibrator = ThresholdCalibrator(
            target_fpr=cfg.detection.target_fpr, min_negatives=cfg.detection.min_negatives
        )
        scores = negative_scores(table)
        result = calibrator.fit(scores)
        adapter: Any = make_adapter(cfg, method)
        secondary: dict[str, dict[str, float]] = {}
        for name in adapter.secondary_scores:
            fitted = calibrator.fit(negative_scores(with_secondary(table, adapter, name)))
            secondary[name] = {
                "threshold": fitted.threshold,
                "achieved_fpr_dev": fitted.achieved_fpr,
            }
        levels = self.levels()
        provisional = not set(levels) >= FULL_LEVELS
        thresholds = ThresholdSet(
            method=method,
            model_id=model_id,
            language=language,
            config_hash=cfg_hash,
            target_fpr=calibrator.target_fpr,
            threshold=result.threshold,
            achieved_fpr_dev=result.achieved_fpr,
            n_negatives=result.n_negatives,
            underpowered=result.underpowered,
            created_at=datetime.now(UTC),
            negatives_ref=";".join(str(ref.path) for ref in self.inputs(cell)),
            provisional=provisional,
            levels=levels,
            secondary=secondary,
        )
        ref = self.outputs(cell)[0]
        n_failed = sum(1 for s in scores if s == MIN_SCORE)
        extra: dict[str, Any] = {
            "threshold": json_threshold(result.threshold),
            "achieved_fpr_dev": result.achieved_fpr,
            "n_negatives": result.n_negatives,
            "n_failed_negatives": n_failed,
            "underpowered": result.underpowered,
            "provisional": provisional,
            "levels": levels,
            "unreachable": result.threshold == float("inf"),
            "secondary": {
                k: {**v, "threshold": json_threshold(v["threshold"])} for k, v in secondary.items()
            },
            # Linguaggio non supportato dal metodo: tutti NOT_APPLICABLE, soglia senza significato.
            "not_applicable": bool((table["status"] == "NOT_APPLICABLE").all()),
        }
        ctx.store.write_model(
            ref,
            thresholds,
            ctx.make_manifest(
                ref,
                inputs=self.inputs(cell),
                n_rows_in=len(table),
                n_rows_out=1,
                n_rows_expected=1,
                config_hash=cfg_hash,
                extra=extra,
            ),
        )
        logger.info(
            "%s: threshold %s, achieved FPR %.4f on %d negatives (%d failed)%s%s",
            cell.key(),
            result.threshold,
            result.achieved_fpr,
            result.n_negatives,
            n_failed,
            ", UNDERPOWERED" if result.underpowered else "",
            ", provisional" if provisional else "",
        )
