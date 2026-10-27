"""Seconda chiave di T3.1 (ri-marcatura con k2; ADR-014 §3; decisioni dell'utente del 27 ottobre
2026). La ri-marcatura vera e propria è l'attacco ``T3.1`` della fase ``attack``; qui le fasi che
servono a rilevare e valutare la seconda chiave, tutte con la configurazione **congelata** del
metodo per k1 (``freeze/<metodo>.json``) su ogni parte:

- ``detect_k2``: rilevazione con k2. Celle senza attacco: sulla parte dev i negativi umani di L1 e
  L2 e l'integrazione combinata (celle L2 dev), come la calibrazione definitiva di k1; sulla parte
  di test solo i negativi umani (verifica dell'FPR). Celle con attacco: i campioni attaccati
  (T3.1). Output ``detection/<metodo>/<modello>/<cfg>/k2/<soggetto>/...``;
- ``calibrate_k2``: soglia di k2 sui negativi umani di sviluppo L1+L2, stessa procedura di k1.
  Output ``thresholds/<metodo>/<modello>/<lang>/L1L2/k2/<cfg>.json``;
- ``freeze_k2``: congelamento delle soglie di k2 prima di rilevare il test (I3),
  ``freeze/<metodo>.k2.json``; dopo, ``calibrate_k2`` rifiuta il metodo;
- ``metrics_remark``: per le celle T3.1, le quattro categorie per campione (solo k1, solo k2,
  entrambi, nessuno) con le soglie congelate delle due chiavi, TPR e AUROC con k2, FPR dei negativi
  umani di test con le due chiavi, frazioni di ``UNCHANGED``, ``NOT_APPLICABLE`` e
  ``TOOL_ERROR``, le due soglie; per ACW la sovrapposizione fra i sottoinsiemi di regole di k1 e
  k2 e le categorie per numero di regole applicate da k2 che stanno anche nel sottoinsieme di k1.
  TPR con k1, AUROC, Pass@1 e ΔPass@1 restano nella fase ``metrics`` della cella (metrica
  principale). Output ``metrics_<livelli>/remark/<attacco>/<params_hash>/<metodo>/...``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, ClassVar

import pandas as pd

from bench.config.resources import ResourceClass
from bench.domain.errors import ConfigError
from bench.domain.models import MetricValue, ThresholdSet
from bench.hpo.freeze import (
    FINAL_LEVELS,
    FreezeRecord,
    FrozenCell,
    data_sha256,
    freeze_ref,
    load_freeze,
)
from bench.metrics.stats import auroc, auroc_interval, clopper_pearson, ratio_interval, wilson
from bench.metrics.uncertainty import bootstrap_seed
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.calibrate import (
    CalibrateStage,
    levels_tag,
    primary_view,
    threshold_ref,
)
from bench.pipeline.stages.detect import DetectStage, detection_ref
from bench.pipeline.stages.metrics import MetricsStage, _attack_info, _detected, _scores
from bench.pipeline.stages.watermark import watermarked_ref
from bench.registry import STAGES
from bench.store.artifact_store import ArtifactStore
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

ALT_KEY_ID = "k2"
REMARK_ATTACK = "T3.1"
CATEGORIES = ("only_k1", "only_k2", "both", "none")
# ACW: categorie per numero di regole applicate da k2 che stanno anche nel sottoinsieme di k1.
OVERLAP_BINS = ("0", "1", "2", "3+")


def frozen_config_hash(store: ArtifactStore, method: str, model_id: str, language: str) -> str:
    """Configurazione congelata (k1) di (metodo, modello, linguaggio).

    Raises:
        ConfigError: metodo non congelato o linguaggio non applicabile.
    """
    record = load_freeze(store, method)
    frozen = record.cell(model_id, language) if record is not None else None
    if frozen is None or frozen.config_hash is None:
        raise ConfigError(f"{method} {model_id} {language}: no frozen configuration (ADR-013)")
    return frozen.config_hash


def remark_metrics_ref(
    method: str,
    model_id: str,
    cfg_hash: str,
    level: str,
    language: str,
    split: str,
    threshold_levels: list[str],
    attack: tuple[str, str],
) -> ArtifactRef:
    """Metriche della ri-marcatura di una cella T3.1."""
    tag = levels_tag(threshold_levels)
    folder = "metrics" + (f"_{tag}" if tag else "")
    return ArtifactRef.of(
        "metrics_remark",
        f"{folder}/remark/{attack[0]}/{attack[1]}/{method}/{model_id}/{cfg_hash}/"
        f"{level}_{language}_{split}.parquet",
    )


class _FrozenConfig:
    """Configurazione congelata di k1 anche fuori dalla parte di test (dev)."""

    config: Any

    def _frozen(self, cell: Cell) -> Cell:
        if cell.config_hash is not None or not (cell.method and cell.model_id and cell.language):
            return cell
        store = ArtifactStore(self.config.paths.artifacts)
        h = frozen_config_hash(store, cell.method, cell.model_id, str(cell.language))
        return replace(cell, config_hash=h)


@STAGES.register("detect_k2")
class DetectK2Stage(_FrozenConfig, DetectStage):
    """Rilevazione con la seconda chiave (negativi umani e campioni T3.1)."""

    name: ClassVar[str] = "detect_k2"
    output_kinds: ClassVar[frozenset[str]] = frozenset({f"detection_{ALT_KEY_ID}"})
    sweep_roles: ClassVar[tuple[str, ...]] = ()
    key_id: ClassVar[str] = ALT_KEY_ID

    def cell_config(self, cell: Cell) -> tuple[dict[str, Any], str]:
        return super().cell_config(self._frozen(cell))

    def subjects(self, method: str, level: str = "L1", split: str = "dev") -> tuple[str, ...]:
        """Negativi umani; sulla parte dev anche l'integrazione combinata (celle L2)."""
        if split != "dev":
            return ("neg_human",)
        return ("neg_human", "neg_integration") if level == "L2" else ("neg_human",)

    def _sample_refs(self, cell: Cell) -> dict[str, list[ArtifactRef]]:
        refs = super()._sample_refs(cell)
        if cell.attack_id:
            return refs
        method, _, level, _, split = self._require(cell)
        return {s: refs[s] for s in self.subjects(method, level, split)}


@STAGES.register("calibrate_k2")
class CalibrateK2Stage(_FrozenConfig, CalibrateStage):
    """Soglia della seconda chiave sui negativi umani di sviluppo L1+L2."""

    name: ClassVar[str] = "calibrate_k2"
    output_kinds: ClassVar[frozenset[str]] = frozenset({f"threshold_{ALT_KEY_ID}"})
    sweep_roles: ClassVar[tuple[str, ...]] = ()
    key_id: ClassVar[str] = ALT_KEY_ID

    def config_hash(self, cell: Cell | str) -> str:
        if isinstance(cell, str):
            cell = Cell(method=cell)
        return super().config_hash(self._frozen(cell))

    def levels(self) -> list[str]:
        return list(FINAL_LEVELS)  # come le soglie definitive di k1 (ADR-011)


@STAGES.register("freeze_k2")
class FreezeK2Stage(Stage):
    """Congela le soglie della seconda chiave di un metodo congelato (I3, prima del test)."""

    name: ClassVar[str] = "freeze_k2"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({f"freeze_{ALT_KEY_ID}"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method",)

    def __init__(self, config: Any = None) -> None:
        self.config = config

    @classmethod
    def create(cls, config: Any) -> Stage:
        return cls(config)

    @staticmethod
    def _require(cell: Cell) -> str:
        if not cell.method:
            raise ConfigError(f"freeze_k2 needs method: {cell.key()}")
        return cell.method

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        """I controlli sono in ``run`` (elenco completo di quello che manca)."""
        self._require(cell)
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [freeze_ref(self._require(cell), ALT_KEY_ID)]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        method = self._require(cell)
        record = load_freeze(ctx.store, method)
        if record is None:
            raise ConfigError(f"{method} is not frozen for k1: freeze it first (ADR-013)")
        if load_freeze(ctx.store, method, ALT_KEY_ID) is not None:
            raise ConfigError(
                f"{method}: k2 thresholds already frozen (a new freeze is a deviation)"
            )
        problems: list[str] = []
        cells: list[FrozenCell] = []
        refs: list[ArtifactRef] = []
        for frozen in record.cells:
            if frozen.not_applicable or frozen.config_hash is None:
                cells.append(frozen)
                continue
            ref = threshold_ref(
                method, frozen.model_id, frozen.language, frozen.config_hash, FINAL_LEVELS,
                ALT_KEY_ID,
            )  # fmt: skip
            name = f"{frozen.model_id} {frozen.language}"
            if not ctx.store.exists(ref):
                problems.append(f"{name}: no k2 threshold {ref.path} (run calibrate_k2)")
                continue
            thresholds = ctx.store.read_model(ref, ThresholdSet)
            if thresholds.provisional or sorted(thresholds.levels) != list(FINAL_LEVELS):
                problems.append(f"{name}: k2 threshold is provisional ({thresholds.levels})")
            refs.append(ref)
            cells.append(
                frozen.model_copy(
                    update={
                        "threshold": thresholds.threshold,
                        "threshold_sha256": data_sha256(ctx.store, ref),
                    }
                )
            )
        if problems:
            raise ConfigError(
                f"{method}: k2 cannot be frozen:\n" + "\n".join(f"  - {p}" for p in problems)
            )
        out = FreezeRecord(
            method=method,
            frozen_at=datetime.now(UTC),
            levels=list(FINAL_LEVELS),
            grid_sha256=record.grid_sha256,
            candidate_hashes=record.candidate_hashes,
            cells=cells,
        )
        ref = freeze_ref(method, ALT_KEY_ID)
        ctx.store.write_model(
            ref,
            out,
            ctx.make_manifest(
                ref,
                inputs=refs,
                n_rows_in=None,
                n_rows_out=len(cells),
                n_rows_expected=len(cells),
                extra={"key_id": ALT_KEY_ID, "levels": list(FINAL_LEVELS), "n_cells": len(cells)},
            ),  # fmt: skip
        )
        logger.info("%s: k2 thresholds frozen, %d cells", method, len(cells))


@STAGES.register("metrics_remark")
class MetricsRemarkStage(MetricsStage):
    """Categorie k1/k2 della ri-marcatura e metriche della seconda chiave (celle T3.1)."""

    name: ClassVar[str] = "metrics_remark"
    output_kinds: ClassVar[frozenset[str]] = frozenset({"metrics_remark"})

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        return cell if cell.attack_id == REMARK_ATTACK else None

    def _refs(self, cell: Cell) -> dict[str, ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        if not cell.attack_id:
            raise ConfigError(f"metrics_remark needs a T3.1 cell: {cell.key()}")
        attack_id, params_hash = cell.attack_id, str(cell.attack_params_hash)
        cfg_hash = self.config_hash(cell)
        levels = self.levels()

        def det(subject: str, key_id: str) -> ArtifactRef:
            return detection_ref(
                method, model_id, cfg_hash, subject, level, language, split, key_id
            )

        subject = attacked_subject(attack_id, params_hash)
        refs = {
            "threshold": threshold_ref(method, model_id, language, cfg_hash, levels),
            "threshold_k2": threshold_ref(method, model_id, language, cfg_hash, levels, ALT_KEY_ID),
            "freeze_k2": freeze_ref(method, ALT_KEY_ID),
            "attacked_det": det(subject, "k1"),
            "attacked_det_k2": det(subject, ALT_KEY_ID),
            "neg_human": det("neg_human", "k1"),
            "neg_human_k2": det("neg_human", ALT_KEY_ID),
            "attacked": attacked_ref(
                attack_id, params_hash, method, model_id, cfg_hash, level, language, split
            ),
        }
        if method == "promptmark":  # ciclo di correttezza nel manifest (D27)
            refs["watermarked"] = watermarked_ref(
                method, model_id, cfg_hash, level, language, split
            )
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        attack = (str(cell.attack_id), str(cell.attack_params_hash))
        return [
            remark_metrics_ref(
                method,
                model_id,
                self.config_hash(cell),
                level,
                language,
                split,
                self.levels(),
                attack,
            )  # fmt: skip
        ]

    def _check_k2(
        self, ctx: StageContext, cell: Cell, ref: ArtifactRef, thresholds: ThresholdSet
    ) -> None:
        """Soglia di k2 definitiva e congelata prima del test (I3)."""
        if thresholds.provisional:
            raise ConfigError(f"{cell.key()}: k2 threshold is provisional")
        record = load_freeze(ctx.store, str(cell.method), ALT_KEY_ID)
        frozen = record.cell(str(cell.model_id), str(cell.language)) if record else None
        if frozen is None or frozen.threshold_sha256 != data_sha256(ctx.store, ref):
            raise ConfigError(f"{cell.key()}: k2 threshold {ref.path} is not the frozen one")

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(cell)
        refs = self._refs(cell)
        thresholds = ctx.store.read_model(refs["threshold"], ThresholdSet)
        self._check_test(ctx, cell, refs["threshold"], thresholds)
        thresholds_k2 = ctx.store.read_model(refs["threshold_k2"], ThresholdSet)
        self._check_k2(ctx, cell, refs["threshold_k2"], thresholds_k2)
        tau1, tau2 = thresholds.threshold, thresholds_k2.threshold
        adapter = self.adapter(method)
        n_boot = cfg.detection.n_bootstrap
        info: dict[str, Any] = {
            "method": method, "model_id": model_id, "level": level, "language": language,
            "split": split, "config_hash": cfg_hash, **_attack_info(cell),
        }  # fmt: skip
        rows: list[MetricValue] = []

        def add(name: str, value: float, ci: tuple[float, float], how: str, n: int) -> None:
            rows.append(
                MetricValue(
                    name=name,
                    value=float(value),
                    ci_low=None if math.isnan(ci[0]) else ci[0],
                    ci_high=None if math.isnan(ci[1]) else ci[1],
                    ci_method=how,
                    n=n,
                    cell=dict(info),
                )  # fmt: skip
            )

        det1 = primary_view(ctx.store.read_table(refs["attacked_det"]), adapter)
        det2 = primary_view(ctx.store.read_table(refs["attacked_det_k2"]), adapter)
        det1 = det1[det1["status"] != "NOT_APPLICABLE"]
        if det1.empty:
            self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=True)
            return
        det2 = det2.set_index("sample_id").loc[det1["sample_id"]].reset_index()
        # k2: l'inserimento che conta non è quello di k1 (embed_status del campione marcato).
        det2 = det2.assign(embed_status=None)
        hit1 = _detected(det1, tau1).to_numpy(dtype=bool)
        hit2 = _detected(det2, tau2).to_numpy(dtype=bool)
        table = pd.DataFrame(
            {
                "problem_key": det1["problem_key"].to_numpy(),
                "sample_id": det1["sample_id"].to_numpy(),
                "only_k1": (hit1 & ~hit2).astype(float),
                "only_k2": (~hit1 & hit2).astype(float),
                "both": (hit1 & hit2).astype(float),
                "none": (~hit1 & ~hit2).astype(float),
                "hit_k2": hit2.astype(float),
            }
        )

        def seed(name: str) -> int:
            return bootstrap_seed(cfg.global_seed, name, cell.key())

        for category in CATEGORIES:
            add(f"remark_{category}", float(table[category].mean()),
                ratio_interval(table, category, seed(f"remark_{category}"), n_boot),
                "cluster_bootstrap", len(table))  # fmt: skip
        add("tpr_at_fpr_k2", float(table["hit_k2"].mean()),
            ratio_interval(table, "hit_k2", seed("tpr_at_fpr_k2"), n_boot), "cluster_bootstrap",
            len(table))  # fmt: skip
        neg2 = primary_view(ctx.store.read_table(refs["neg_human_k2"]), adapter)
        pos2 = det2.assign(score=_scores(det2))
        neg2_scores = _scores(neg2)
        add("auroc_k2", auroc(pos2["score"].to_numpy(dtype=float), neg2_scores),
            auroc_interval(pos2, neg2_scores, seed("auroc_k2"), n_boot),
            "cluster_bootstrap+negative_resampling", len(pos2) + len(neg2_scores))  # fmt: skip
        for name, key, tau in (("fpr_human_test_k1", "neg_human", tau1),
                               ("fpr_human_test_k2", "neg_human_k2", tau2)):  # fmt: skip
            negatives = primary_view(ctx.store.read_table(refs[key]), adapter)
            k = int(_detected(negatives, tau).sum())
            add(name, k / len(negatives), clopper_pearson(k, len(negatives)), "clopper_pearson",
                len(negatives))  # fmt: skip
        for name, value in (("threshold_k1", tau1), ("threshold_k2", tau2)):
            add(name, value, (math.nan, math.nan), "frozen", 1)
        samples = ctx.store.read_table(refs["attacked"])
        for status in ("UNCHANGED", "NOT_APPLICABLE", "TOOL_ERROR"):
            k = int((samples["attack_status"] == status).sum())
            add(f"{status.lower()}_fraction", k / len(samples), wilson(k, len(samples)), "wilson",
                len(samples))  # fmt: skip
        if method == "acw":
            self._acw_overlap(add, seed, n_boot, table, det1, samples)
        self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=False)

    @staticmethod
    def _acw_overlap(
        add: Callable[..., None],
        seed: Callable[[str], int],
        n_boot: int,
        table: pd.DataFrame,
        det1: pd.DataFrame,
        samples: pd.DataFrame,
    ) -> None:
        """ACW: regole in comune fra i sottoinsiemi di k1 (dalla rilevazione con k1) e di k2
        (dal dettaglio dell'attacco) e categorie per numero di regole applicate da k2 che stanno
        anche nel sottoinsieme di k1 (0, 1, 2, 3+)."""
        k1_rules: set[str] = set()
        for raw in det1["extra_json"]:
            extra = json.loads(raw) if isinstance(raw, str) and raw else {}
            k1_rules |= set((extra.get("rule_unchanged") or {}).keys())
        details = {
            sid: json.loads(raw) if isinstance(raw, str) and raw else {}
            for sid, raw in zip(samples["sample_id"], samples["attack_detail"], strict=True)
        }
        k2_rules = {str(r) for d in details.values() for r in d.get("rules") or []}
        common = k1_rules & k2_rules
        add("acw_rule_overlap", float(len(common)), (math.nan, math.nan), "count", len(k2_rules))
        counts = []
        for sid in table["sample_id"]:  # righe della rilevazione: id dei campioni attaccati
            applied = {str(r) for r in details.get(sid, {}).get("applicable_rules") or []}
            counts.append(len(applied & k1_rules))
        bins = [OVERLAP_BINS[min(c, len(OVERLAP_BINS) - 1)] for c in counts]
        binned = table.assign(overlap_bin=bins)
        for label in OVERLAP_BINS:
            part = binned[binned["overlap_bin"] == label]
            add(f"acw_overlap_fraction|{label}", len(part) / len(binned),
                wilson(len(part), len(binned)), "wilson", len(binned))  # fmt: skip
            for category in CATEGORIES:
                value = float(part[category].mean()) if len(part) else math.nan
                ci = (
                    ratio_interval(part, category, seed(f"remark_{category}|{label}"), n_boot)
                    if len(part)
                    else (math.nan, math.nan)
                )
                add(f"remark_{category}|overlap={label}", value, ci, "cluster_bootstrap", len(part))


__all__ = [
    "ALT_KEY_ID",
    "CalibrateK2Stage",
    "DetectK2Stage",
    "FreezeK2Stage",
    "MetricsRemarkStage",
    "frozen_config_hash",
    "remark_metrics_ref",
]
