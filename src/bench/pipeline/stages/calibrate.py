"""Fase ``calibrate``: soglie congelate sui negativi umani di sviluppo (SPEC §13.2, I3).

Una cella = (metodo, modello, linguaggio): una soglia per configurazione, calcolata sui negativi
umani della parte **dev** dei livelli della configurazione (``levels``).

- **Solo L1** (M7, M8): nativi e integrazione di L1 (``neg_human`` delle celle L1 dev). La soglia è
  **provvisoria** (``provisional=True``, D24) e la fase ``metrics`` non la usa sul test. Output:
  ``thresholds/<metodo>/<modello>/<lang>/<cfg>.json``.
- **L1+L2** (M9, soglie definitive, ADR-011): nativi di sviluppo di L1 (le righe dei nativi di
  ``neg_human`` delle celle L1 dev: i punteggi dell'integrazione di L1 non si usano), nativi di L2
  (``neg_human`` delle celle L2 dev) e integrazione combinata L1+L2 (``neg_integration``, TODO 3,
  opzione 2). Output su un percorso proprio,
  ``thresholds/<metodo>/<modello>/<lang>/L1L2/<cfg>.json``, così le soglie provvisorie restano e
  non vengono saltate per errore. Il manifest riporta l'FPR di sviluppo per parte (nativi L1,
  nativi L2, integrazione), come chiesto dall'utente il 13 ottobre 2026.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.domain.models import ThresholdSet
from bench.hpo.grid import resolve
from bench.metrics.calibration import MIN_SCORE, ThresholdCalibrator
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.prepare_data import native_ref
from bench.pipeline.stages.watermark import make_adapter
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

CALIBRATION_SPLIT = "dev"
FULL_LEVELS = frozenset({"L1", "L2"})  # livelli previsti dalla SPEC per le soglie definitive


def levels_tag(levels: Sequence[str]) -> str | None:
    """Etichetta dei livelli di calibrazione nei percorsi: nessuna per il solo L1 (percorsi della
    M7 e della M8), altrimenti i livelli concatenati (``L1L2``)."""
    ordered = sorted({str(level) for level in levels})
    if not ordered:
        raise ConfigError("calibration needs at least one level")
    return None if ordered == ["L1"] else "".join(ordered)


def threshold_ref(
    method: str, model_id: str, language: str, cfg_hash: str, levels: Sequence[str] = ("L1",)
) -> ArtifactRef:
    """Soglia congelata di (metodo, modello, linguaggio, configurazione) calibrata su ``levels``."""
    tag = levels_tag(levels)
    folder = f"thresholds/{method}/{model_id}/{language}" + (f"/{tag}" if tag else "")
    return ArtifactRef.of("threshold", f"{folder}/{cfg_hash}.json")


def json_threshold(value: float) -> float | str:
    """Soglia per i manifest: ``"Infinity"`` invece di ``null`` per la soglia irraggiungibile."""
    return value if math.isfinite(value) else "Infinity"


def primary_view(table: pd.DataFrame, adapter: Any) -> pd.DataFrame:
    """La tabella di rilevazione con il punteggio principale del metodo in ``score``.

    Se il metodo prende il principale da ``extra`` (``primary_score``, es. STONE, D16), il punteggio
    nativo si sposta in ``extra`` con il nome ``native_score_name`` (secondario) e ``score`` diventa
    il principale; senza valore (NaN) la rilevazione conta come non riuscita. Le rilevazioni salvate
    non cambiano: la scelta vale per calibrazione, metriche e HPO.
    """
    key = getattr(adapter, "primary_score", None)
    if key is None:
        return table
    native = pd.to_numeric(table["score"], errors="coerce")
    extras = []
    for raw, value in zip(table["extra_json"], native, strict=True):
        extra = json.loads(raw) if isinstance(raw, str) and raw else {}
        extra[adapter.native_score_name] = None if pd.isna(value) else float(value)
        extras.append(json.dumps(extra, sort_keys=True))
    out = table.copy()
    out["extra_json"] = extras
    values = [
        json.loads(raw).get(key) if status == "OK" else None
        for raw, status in zip(out["extra_json"], out["status"], strict=True)
    ]
    # Lo stato resta quello della rilevazione: un punteggio mancante conta già come non riuscito
    # (``negative_scores``, ``detected``) e il nativo resta usabile come secondario.
    out["score"] = [math.nan if v is None else float(v) for v in values]
    return out


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


def fpr_by_part(table: pd.DataFrame, scores: list[float], threshold: float) -> dict[str, Any]:
    """Negativi e FPR alla soglia per ogni valore di ``part`` della tabella."""
    out: dict[str, Any] = {}
    parts = table["part"].tolist()
    for part in dict.fromkeys(parts):
        values = [s for s, p in zip(scores, parts, strict=True) if p == part]
        hits = sum(1 for s in values if s >= threshold)
        out[part] = {
            "n_negatives": len(values),
            "false_positives": hits,
            "fpr": round(hits / len(values), 6) if values else None,
        }
    return out


@STAGES.register("calibrate")
class CalibrateStage(Stage):
    """Soglia all'FPR obiettivo sui negativi umani di sviluppo."""

    name: ClassVar[str] = "calibrate"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"threshold"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "language")
    sweep_roles: ClassVar[tuple[str, ...]] = ("candidate",)

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

    def config_hash(self, cell: Cell | str) -> str:
        """Hash della configurazione della cella (con un nome di metodo: il default)."""
        if isinstance(cell, str):
            cell = Cell(method=cell)
        key = f"{cell.method}:{cell.config_hash}"
        if key not in self._hashes:
            adapter = make_adapter(self._cfg(), str(cell.method))
            self._hashes[key] = resolve(self._cfg(), adapter, cell.config_hash)[1]
        return self._hashes[key]

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str]:
        if not (cell.method and cell.model_id and cell.language):
            raise ConfigError(f"calibrate needs method, model_id, language: {cell.key()}")
        return cell.method, cell.model_id, cell.language

    def levels(self) -> list[str]:
        return sorted(str(level) for level in self._cfg().levels)

    def _parts(self, cell: Cell) -> dict[str, ArtifactRef]:
        """Negativi di sviluppo per parte: ``L1`` (solo L1, provvisorio) oppure ``L1_native``,
        ``L2_native`` e ``integration_L1L2`` (calibrazione con L2)."""
        method, model_id, language = self._require(cell)
        cfg_hash = self.config_hash(cell)
        levels = self.levels()

        def det(subject: str, level: str) -> ArtifactRef:
            return detection_ref(
                method, model_id, cfg_hash, subject, level, language, CALIBRATION_SPLIT
            )

        if "L2" not in levels:
            return {level: det("neg_human", level) for level in levels}
        parts = {f"{level}_native": det("neg_human", level) for level in levels}
        parts["integration_L1L2"] = det("neg_integration", "L2")
        return parts

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        _, _, language = self._require(cell)
        refs = list(self._parts(cell).values())
        if "L1_native" in self._parts(cell):
            refs.append(native_ref(Language(language)))  # per tenere i soli nativi di L1
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, language = self._require(cell)
        return [threshold_ref(method, model_id, language, self.config_hash(cell), self.levels())]

    def _negatives(self, ctx: StageContext, cell: Cell) -> pd.DataFrame:
        """Tabella dei negativi di sviluppo con la colonna ``part``."""
        _, _, language = self._require(cell)
        frames = []
        for part, ref in self._parts(cell).items():
            table = ctx.store.read_table(ref)
            if part == "L1_native":
                # neg_human di L1 dev = nativi + integrazione di L1: si tengono i soli nativi.
                natives = ctx.store.read_table(native_ref(Language(language)))
                table = table[table["sample_id"].isin(set(natives["sample_id"]))]
            frames.append(table.assign(part=part))
        return pd.concat(frames, ignore_index=True)

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, language = self._require(cell)
        cfg_hash = self.config_hash(cell)
        adapter: Any = make_adapter(cfg, method)
        table = primary_view(self._negatives(ctx, cell), adapter)
        calibrator = ThresholdCalibrator(
            target_fpr=cfg.detection.target_fpr, min_negatives=cfg.detection.min_negatives
        )
        scores = negative_scores(table)
        result = calibrator.fit(scores)
        secondary: dict[str, dict[str, float]] = {}
        for name in adapter.secondary_scores:
            fitted = calibrator.fit(negative_scores(with_secondary(table, adapter, name)))
            secondary[name] = {
                "threshold": fitted.threshold,
                "achieved_fpr_dev": fitted.achieved_fpr,
            }
        levels = self.levels()
        provisional = not set(levels) >= FULL_LEVELS
        by_part = fpr_by_part(table, scores, result.threshold)
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
            # FPR di sviluppo alla soglia, separato per parte (L1 e L2, decisione del 13 ottobre).
            "fpr_dev_by_part": by_part,
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
