"""Controllo negativo degli attacchi: lo stesso attacco sulla baseline senza watermark (D34).

Misura aggiuntiva rispetto al protocollo (decisione dell'utente del 26 ottobre 2026), per i punti
della griglia con ``negative_control`` (``configs/attack/*.yaml``; oggi solo T2.2 full_rewrite).
Un TPR che resta dopo una riscrittura completa può venire dal watermark sopravvissuto oppure dallo
stile dell'attaccante, se il rilevatore scambia per marcato il codice scritto dall'LLM: la
rilevazione della baseline riscritta li separa. Tre fasi:

- ``attack_baseline``: l'attacco sulla baseline di ogni modello, **una volta** per (modello,
  livello, linguaggio, parte), comune a tutti i metodi. Output
  ``attacked_baseline/<attacco>/<params_hash>/<modello>/<L>_<lang>_<parte>.parquet`` con lo stesso
  formato degli attaccati (fase ``attack``);
- ``detect_attack_control``: rilevazione della baseline riscritta con la configurazione congelata
  del metodo; soggetto ``neg_attacked/<attacco>/<params_hash>`` (messaggio atteso dagli
  identificativi, come per i negativi);
- ``metrics_attack_control``: con la soglia congelata della cella, ``fpr_llm_rewritten`` (baseline
  riscritta sopra soglia, Clopper-Pearson) accanto a ``fpr_llm_original`` (la stessa baseline prima
  dell'attacco) e a ``tpr_at_fpr`` dei marcati riscritti; ``auroc_rewritten`` fra marcati
  riscritti e baseline riscritta (bootstrap per problema sui positivi e ricampionamento dei
  negativi), da leggere accanto all'``auroc`` della cella con attacco (contro i negativi umani).
  Output ``metrics_<livelli>/attack_control/<attacco>/<params_hash>/<metodo>/...``.

Per MCGMark il riferimento è la baseline normale, non la gemella: la riscrittura completa toglie il
formato del prompt che le distingue (D34).
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from bench.config.resources import ResourceClass
from bench.domain.errors import ConfigError
from bench.domain.models import MetricValue, ThresholdSet
from bench.metrics.stats import auroc, auroc_interval, clopper_pearson, ratio_interval
from bench.metrics.uncertainty import bootstrap_seed
from bench.pipeline.stage import Cell, StageContext
from bench.pipeline.stages.attack import AttackStage, attacked_subject
from bench.pipeline.stages.attack_pools import attack_pool_ref
from bench.pipeline.stages.calibrate import levels_tag, primary_view, threshold_ref
from bench.pipeline.stages.detect import DetectStage, detection_ref
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.pipeline.stages.metrics import MetricsStage, _attack_info, _detected, _scores
from bench.pipeline.stages.watermark import watermarked_ref
from bench.registry import STAGES
from bench.store.refs import ArtifactRef


def attacked_baseline_ref(
    attack_id: str, params_hash: str, model_id: str, level: str, language: str, split: str
) -> ArtifactRef:
    """Baseline attaccata di un modello (comune a tutti i metodi)."""
    return ArtifactRef.of(
        "attacked_baseline",
        f"attacked_baseline/{attack_id}/{params_hash}/{model_id}/{level}_{language}_{split}.parquet",
    )


def control_subject(attack_id: str, params_hash: str) -> str:
    """Soggetto della rilevazione della baseline attaccata."""
    return f"neg_attacked/{attack_id}/{params_hash}"


def control_detection_ref(
    method: str,
    model_id: str,
    cfg_hash: str,
    attack_id: str,
    params_hash: str,
    level: str,
    language: str,
    split: str,
) -> ArtifactRef:
    """Punteggi della baseline attaccata con un metodo (accanto ai soggetti di ``detect``)."""
    return ArtifactRef.of(
        "detection_control",
        f"detection/{method}/{model_id}/{cfg_hash}/{control_subject(attack_id, params_hash)}/"
        f"{level}_{language}_{split}.parquet",
    )


def control_metrics_ref(
    method: str,
    model_id: str,
    cfg_hash: str,
    level: str,
    language: str,
    split: str,
    threshold_levels: list[str],
    attack: tuple[str, str],
) -> ArtifactRef:
    """Metriche del controllo negativo di una cella con attacco."""
    tag = levels_tag(threshold_levels)
    folder = "metrics" + (f"_{tag}" if tag else "")
    return ArtifactRef.of(
        "metrics_control",
        f"{folder}/attack_control/{attack[0]}/{attack[1]}/{method}/{model_id}/{cfg_hash}/"
        f"{level}_{language}_{split}.parquet",
    )


def _attack_key(cell: Cell) -> tuple[str, str]:
    if not (cell.attack_id and cell.attack_params_hash):
        raise ConfigError(f"the attack control needs attack_id and its params hash: {cell.key()}")
    return cell.attack_id, cell.attack_params_hash


@STAGES.register("attack_baseline")
class AttackBaselineStage(AttackStage):
    """L'attacco sulla baseline senza watermark di un modello (controllo negativo, D34)."""

    name: ClassVar[str] = "attack_baseline"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"attacked_baseline"})
    cell_axes: ClassVar[tuple[str, ...]] = ("model_id", "level", "language", "split", "attack_id")
    attack_points: ClassVar[str] = "negative_control"

    @staticmethod
    def _cell(cell: Cell) -> tuple[str, str, str, str, str, str]:
        attack_id, params_hash = _attack_key(cell)
        if not (cell.model_id and cell.level and cell.language and cell.split):
            raise ConfigError(f"attack_baseline needs model, level, language, split: {cell.key()}")
        return cell.model_id, cell.level, cell.language, cell.split, attack_id, params_hash

    def config_hash(self, cell: Cell) -> str:
        return ""  # nessun metodo: la baseline non ha configurazione

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        model_id, level, language, split, _, _ = self._cell(cell)
        refs = [problems_ref(level, language), baseline_ref(model_id, level, language, split)]
        if self.attack(cell).needs_pools:
            refs.append(attack_pool_ref(language))
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        model_id, level, language, split, attack_id, params_hash = self._cell(cell)
        return [attacked_baseline_ref(attack_id, params_hash, model_id, level, language, split)]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        model_id, level, language, split, _, _ = self._cell(cell)
        parents = baseline_ref(model_id, level, language, split)
        self.attack_and_write(ctx, cell, parents, self.outputs(cell)[0], None)


@STAGES.register("detect_attack_control")
class DetectAttackControlStage(DetectStage):
    """Rilevazione della baseline attaccata con la configurazione (congelata) del metodo."""

    name: ClassVar[str] = "detect_attack_control"
    output_kinds: ClassVar[frozenset[str]] = frozenset({"detection_control"})
    sweep_roles: ClassVar[tuple[str, ...]] = ()
    attack_points: ClassVar[str] = "negative_control"

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        return cell if cell.attack_id else None

    def cell_subjects(self, cell: Cell) -> tuple[str, ...]:
        return (control_subject(*_attack_key(cell)),)

    def _sample_refs(self, cell: Cell) -> dict[str, list[ArtifactRef]]:
        _, model_id, level, language, split = self._require(cell)
        attack_id, params_hash = _attack_key(cell)
        ref = attacked_baseline_ref(attack_id, params_hash, model_id, level, language, split)
        return {control_subject(attack_id, params_hash): [ref]}

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        attack_id, params_hash = _attack_key(cell)
        return [
            control_detection_ref(
                method,
                model_id,
                self.config_hash(cell),
                attack_id,
                params_hash,
                level,
                language,
                split,
            )  # fmt: skip
        ]


@STAGES.register("metrics_attack_control")
class MetricsAttackControlStage(MetricsStage):
    """Falsi positivi sulla baseline attaccata e AUROC fra marcati e baseline attaccati."""

    name: ClassVar[str] = "metrics_attack_control"
    output_kinds: ClassVar[frozenset[str]] = frozenset({"metrics_control"})
    attack_points: ClassVar[str] = "negative_control"

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        return cell if cell.attack_id else None

    def _refs(self, cell: Cell) -> dict[str, ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        attack_id, params_hash = _attack_key(cell)
        cfg_hash = self.config_hash(cell)

        def det(subject: str) -> ArtifactRef:
            return detection_ref(method, model_id, cfg_hash, subject, level, language, split)

        refs = {
            "threshold": threshold_ref(method, model_id, language, cfg_hash, self.levels()),
            "attacked_det": det(attacked_subject(attack_id, params_hash)),
            "control_det": control_detection_ref(
                method, model_id, cfg_hash, attack_id, params_hash, level, language, split
            ),
            "neg_llm": det("neg_llm"),
        }
        if method == "promptmark":  # ciclo di correttezza nel manifest (D27, ``_correctness_loop``)
            refs["watermarked"] = watermarked_ref(
                method, model_id, cfg_hash, level, language, split
            )
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        return [
            control_metrics_ref(
                method,
                model_id,
                self.config_hash(cell),
                level,
                language,
                split,
                self.levels(),
                _attack_key(cell),
            )  # fmt: skip
        ]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(cell)
        refs = self._refs(cell)
        thresholds = ctx.store.read_model(refs["threshold"], ThresholdSet)
        self._check_test(ctx, cell, refs["threshold"], thresholds)
        tau = thresholds.threshold
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

        attacked = primary_view(ctx.store.read_table(refs["attacked_det"]), adapter)
        attacked = attacked[attacked["status"] != "NOT_APPLICABLE"]
        table = attacked.assign(hit=_detected(attacked, tau).astype(float))
        seed = bootstrap_seed(cfg.global_seed, "tpr_at_fpr", cell.key())
        add("tpr_at_fpr", float(table["hit"].mean()), ratio_interval(table, "hit", seed, n_boot),
            "cluster_bootstrap", len(table))  # fmt: skip
        for name, key in (("fpr_llm_rewritten", "control_det"), ("fpr_llm_original", "neg_llm")):
            negatives = primary_view(ctx.store.read_table(refs[key]), adapter)
            k = int(_detected(negatives, tau).sum())
            add(name, k / len(negatives), clopper_pearson(k, len(negatives)), "clopper_pearson",
                len(negatives))  # fmt: skip
        control = primary_view(ctx.store.read_table(refs["control_det"]), adapter)
        pos = attacked.assign(score=_scores(attacked, with_embed=True))
        neg = _scores(control)
        seed = bootstrap_seed(cfg.global_seed, "auroc_rewritten", cell.key())
        add(
            "auroc_rewritten",
            auroc(pos["score"].to_numpy(dtype=float), neg),
            auroc_interval(pos, neg, seed, n_boot),
            "cluster_bootstrap+negative_resampling",
            len(pos) + len(neg),
        )
        self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=False)


__all__ = [
    "AttackBaselineStage",
    "DetectAttackControlStage",
    "MetricsAttackControlStage",
    "attacked_baseline_ref",
    "control_detection_ref",
    "control_metrics_ref",
    "control_subject",
]
