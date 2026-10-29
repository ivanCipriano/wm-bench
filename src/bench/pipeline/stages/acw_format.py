"""Fase ``metrics_acw_format``: ACW al netto delle regole di formattazione (M12, ADR-015 §7).

Decisione dell'utente del 29 ottobre 2026, misure **secondarie** solo per ACW (la principale resta
quella di ``metrics`` e ``metrics_imperceptibility``):

- **rilevamento senza le regole 44 e 45** (autopep8 E30x per le righe vuote, spazi → tab): il
  punteggio è la frazione delle regole di k1 diverse da 44 e 45 rimaste invariate, dall'esito per
  regola già salvato (``rule_unchanged`` in ``DetectionRecord.extra``); nessuna nuova rilevazione.
  La soglia si ricalibra sui negativi umani di sviluppo come per le altre misure secondarie
  (stesse parti della fase ``calibrate`` con L1+L2, stesso calibratore); TPR e AUROC sul test
  pulito e dopo T1.1, accanto a quelli ufficiali (ricalcolati con la soglia congelata, che deve
  coincidere con quella ricalcolata sugli stessi negativi);
- **classificatori sul codice normalizzato** (fasi ``adversarial_*_fmtfree``): AUROC out-of-fold e
  media fra i fold, come in ``metrics_imperceptibility``.

Nomi delle metriche: ``<metrica>_<official|format_free>|<clean|T1.1>`` per il rilevamento,
``adversarial_<variante>_fmtfree_auroc_*`` per i classificatori. Output sotto
``imperceptibility/acw_format/``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from typing import Any, ClassVar

import pandas as pd

from bench.attacks.base import grid_attacks
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.domain.models import MetricValue, ThresholdSet
from bench.metrics.calibration import ThresholdCalibrator
from bench.metrics.stats import auroc, auroc_interval, clopper_pearson, ratio_interval
from bench.metrics.uncertainty import bootstrap_seed
from bench.pipeline.stage import Cell, StageContext
from bench.pipeline.stages.attack import attacked_subject
from bench.pipeline.stages.calibrate import negative_scores, primary_view, threshold_ref
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.imperceptibility import (
    FORMAT_FREE,
    FORMAT_FREE_METHODS,
    VARIANTS,
    adversarial_metrics,
    adversarial_ref,
    references,
)
from bench.pipeline.stages.metrics import MetricsStage, _detected, _scores
from bench.pipeline.stages.prepare_data import native_ref
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

FORMAT_RULES = frozenset({"44", "45"})  # regole di formattazione di ACW (one_rule_format.py)
FORMAT_ATTACK = "T1.1"
DEV_PARTS = ("L1_native", "L2_native", "integration_L1L2")  # come ``CalibrateStage._parts``
View = Callable[[pd.DataFrame], pd.DataFrame]  # tabella di rilevazione → punteggio in ``score``


def format_free_score(extra: dict[str, Any]) -> float | None:
    """Frazione delle regole di k1 diverse da 44 e 45 rimaste invariate; ``None`` senza regole."""
    kept = [bool(v) for r, v in (extra.get("rule_unchanged") or {}).items()
            if str(r) not in FORMAT_RULES]  # fmt: skip
    return sum(kept) / len(kept) if kept else None


def format_free_view(table: pd.DataFrame) -> pd.DataFrame:
    """La tabella di rilevazione con il punteggio senza regole di formattazione in ``score``;
    senza valore la rilevazione conta come non riuscita (come ``with_secondary``)."""
    values = [
        format_free_score(json.loads(raw) if isinstance(raw, str) and raw else {})
        if status == "OK"
        else None
        for raw, status in zip(table["extra_json"], table["status"], strict=True)
    ]
    out = table.copy()
    out["score"] = [math.nan if v is None else v for v in values]
    out["status"] = [
        s if v is not None else ("FAILED" if s == "OK" else s)
        for s, v in zip(table["status"], values, strict=True)
    ]
    return out


def k1_rules(table: pd.DataFrame) -> set[str]:
    """Regole del sottoinsieme di k1, dagli esiti per regola delle rilevazioni."""
    rules: set[str] = set()
    for raw in table["extra_json"]:
        extra = json.loads(raw) if isinstance(raw, str) and raw else {}
        rules |= {str(r) for r in (extra.get("rule_unchanged") or {})}
    return rules


def acw_format_ref(
    method: str, model_id: str, cfg_hash: str, level: str, language: str, split: str
) -> ArtifactRef:
    """Metriche di ACW al netto della formattazione di una cella."""
    return ArtifactRef.of(
        "acw_format_metrics",
        f"imperceptibility/acw_format/{method}/{model_id}/{cfg_hash}/{level}_{language}_{split}"
        ".parquet",
    )


@STAGES.register("metrics_acw_format")
class AcwFormatMetricsStage(MetricsStage):
    """Rilevamento senza regole di formattazione e classificatori sul codice normalizzato."""

    name: ClassVar[str] = "metrics_acw_format"
    output_kinds: ClassVar[frozenset[str]] = frozenset({"acw_format_metrics"})

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        if cell.method not in FORMAT_FREE_METHODS or cell.attack_id:
            return None
        return cell

    @staticmethod
    def _format_attack(language: str) -> str:
        hashes = list(grid_attacks(FORMAT_ATTACK, language=language))
        if len(hashes) != 1:
            raise ConfigError(f"{FORMAT_ATTACK}: expected one grid point, got {len(hashes)}")
        return hashes[0]

    def _refs(self, cell: Cell) -> dict[str, ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(cell)
        if "L2" not in self.levels():
            raise ConfigError(f"{self.name} needs detection.threshold_levels=[L1,L2]")

        def det(subject: str, lvl: str = level, part: str = split) -> ArtifactRef:
            return detection_ref(method, model_id, cfg_hash, subject, lvl, language, part)

        refs = {
            "threshold": threshold_ref(method, model_id, language, cfg_hash, self.levels()),
            "positives": det("positives"),
            "neg_human": det("neg_human"),
            "attacked_det": det(attacked_subject(FORMAT_ATTACK, self._format_attack(language))),
            "dev_L1_native": det("neg_human", "L1", "dev"),
            "dev_L2_native": det("neg_human", "L2", "dev"),
            "dev_integration_L1L2": det("neg_integration", "L2", "dev"),
            "natives": native_ref(Language(language)),
        }
        for variant in VARIANTS:
            for reference in references(method):
                refs[f"adv_{variant}_{reference}"] = adversarial_ref(
                    variant + FORMAT_FREE, method, model_id, cfg_hash, reference, level,
                    language, split,
                )  # fmt: skip
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        return [acw_format_ref(method, model_id, self.config_hash(cell), level, language, split)]

    def _dev_negatives(self, ctx: StageContext, refs: dict[str, ArtifactRef]) -> pd.DataFrame:
        """Negativi di sviluppo L1+L2 per parte, come ``CalibrateStage._negatives``."""
        natives = set(ctx.store.read_table(refs["natives"])["sample_id"])
        frames = []
        for part in DEV_PARTS:
            table = ctx.store.read_table(refs[f"dev_{part}"])
            if part == "L1_native":  # neg_human di L1 dev = nativi + integrazione di L1
                table = table[table["sample_id"].isin(natives)]
            frames.append(table.assign(part=part))
        return pd.concat(frames, ignore_index=True)

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(cell)
        refs = self._refs(cell)
        thresholds = ctx.store.read_model(refs["threshold"], ThresholdSet)
        self._check_test(ctx, cell, refs["threshold"], thresholds)
        adapter = self.adapter(method)
        n_boot = cfg.detection.n_bootstrap
        info = {"method": method, "model_id": model_id, "level": level, "language": language,
                "split": split, "config_hash": cfg_hash}  # fmt: skip
        rows: list[MetricValue] = []

        def add(name: str, value: float, ci: tuple[float, float], how: str, n: int) -> None:
            rows.append(MetricValue(
                name=name, value=float(value),
                ci_low=None if math.isnan(ci[0]) else ci[0],
                ci_high=None if math.isnan(ci[1]) else ci[1],
                ci_method=how, n=int(n), cell=dict(info),
            ))  # fmt: skip

        def seed(name: str) -> int:
            return bootstrap_seed(cfg.global_seed, name, cell.key())

        # soglie: ufficiale congelata (e ricalcolata, per controllo) e senza formattazione
        dev = self._dev_negatives(ctx, refs)
        calibrator = ThresholdCalibrator(
            target_fpr=cfg.detection.target_fpr, min_negatives=cfg.detection.min_negatives
        )
        recomputed = calibrator.fit(negative_scores(primary_view(dev, adapter)))
        fitted = calibrator.fit(negative_scores(format_free_view(dev)))
        nan = (math.nan, math.nan)
        add("threshold_official", thresholds.threshold, nan, "frozen", 1)
        add("threshold_official_recomputed", recomputed.threshold, nan, "dev_negatives",
            recomputed.n_negatives)  # fmt: skip
        add("threshold_format_free", fitted.threshold, nan, "dev_negatives", fitted.n_negatives)
        add("achieved_fpr_dev_format_free", fitted.achieved_fpr, nan, "dev_negatives",
            fitted.n_negatives)  # fmt: skip
        if recomputed.threshold != thresholds.threshold:
            logger.warning("%s: recomputed official threshold %s differs from the frozen %s",
                           cell.key(), recomputed.threshold, thresholds.threshold)  # fmt: skip

        positives = ctx.store.read_table(refs["positives"])
        rules = k1_rules(positives)
        add("n_rules_k1", len(rules), nan, "count", len(rules))
        add("n_format_rules_in_k1", len(rules & FORMAT_RULES), nan, "count", len(rules))

        views: dict[str, tuple[View, float]] = {
            "official": (lambda t: primary_view(t, adapter), thresholds.threshold),
            "format_free": (format_free_view, fitted.threshold),
        }
        self._detection(ctx, refs, views, add, seed, n_boot)
        self._classifiers(ctx, refs, method, add, seed, n_boot)
        self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=False)

    @staticmethod
    def _detection(
        ctx: StageContext, refs: dict[str, ArtifactRef], views: dict[str, tuple[View, float]],
        add: Callable[..., None], seed: Callable[[str], int], n_boot: int,
    ) -> None:  # fmt: skip
        """FPR sui negativi umani di test, TPR e AUROC sul test pulito e dopo T1.1, per ogni
        punteggio (ufficiale e senza formattazione) con la sua soglia."""
        neg_test = ctx.store.read_table(refs["neg_human"])
        for variant, (view, tau) in views.items():
            negatives = view(neg_test)
            k = int(_detected(negatives, tau).sum())
            add(f"fpr_human_test_{variant}", k / len(negatives),
                clopper_pearson(k, len(negatives)), "clopper_pearson", len(negatives))  # fmt: skip
        for scope, key in (("clean", "positives"), (FORMAT_ATTACK, "attacked_det")):
            table = ctx.store.read_table(refs[key])
            table = table[table["status"] != "NOT_APPLICABLE"]
            for variant, (view, tau) in views.items():
                pos = view(table)
                hits = pos.assign(hit=_detected(pos, tau).astype(float))
                name = f"tpr_at_fpr_{variant}|{scope}"
                add(name, float(hits["hit"].mean()),
                    ratio_interval(hits, "hit", seed(name), n_boot), "cluster_bootstrap",
                    len(hits))  # fmt: skip
                pos_scores = pos.assign(score=_scores(pos, with_embed=True))
                neg_scores = _scores(view(neg_test))
                name = f"auroc_{variant}|{scope}"
                add(name, auroc(pos_scores["score"].to_numpy(dtype=float), neg_scores),
                    auroc_interval(pos_scores, neg_scores, seed(name), n_boot),
                    "cluster_bootstrap+negative_resampling",
                    len(pos_scores) + len(neg_scores))  # fmt: skip

    @staticmethod
    def _classifiers(
        ctx: StageContext, refs: dict[str, ArtifactRef], method: str, add: Callable[..., None],
        seed: Callable[[str], int], n_boot: int,
    ) -> None:  # fmt: skip
        """AUROC dei classificatori sul codice normalizzato."""
        for variant in VARIANTS:
            for i, reference in enumerate(references(method)):
                suffix = "" if i == 0 else f"_{reference}"
                ref = refs[f"adv_{variant}_{reference}"]
                manifest = ctx.store.read_manifest(ref)
                fold_auc = list((manifest.extra or {}).get("fold_auroc", [])) if manifest else []
                name = f"adversarial_{variant}{FORMAT_FREE}"

                def add_suffixed(metric: str, *args: Any, sfx: str = suffix) -> None:
                    add(f"{metric}{sfx}", *args)

                adversarial_metrics(add_suffixed, ctx.store.read_table(ref), fold_auc, name,
                                    seed(f"{name}{suffix}"), n_boot)  # fmt: skip


__all__ = [
    "FORMAT_RULES",
    "AcwFormatMetricsStage",
    "acw_format_ref",
    "format_free_score",
    "format_free_view",
    "k1_rules",
]
