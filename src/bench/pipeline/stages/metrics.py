"""Fase ``metrics``: rilevabilità e preservazione funzionale con IC (SPEC §13.3, §13.5, §13.6).

Una cella = (metodo, modello, livello, linguaggio, parte) con la configurazione di default del
metodo. Output ``metrics/<metodo>/<modello>/<cfg>/<L>_<lang>_<parte>.parquet``, una riga per
``MetricValue`` (I7: ogni metrica ha il suo intervallo).

- ``tpr_at_fpr``: positivi rilevati con la soglia congelata (I2: inserimento ``FAILED`` = non
  rilevato), bootstrap per problema;
- ``fpr_human``: negativi umani della parte sopra soglia, Clopper-Pearson (sul dev è diagnostica:
  sono gli stessi negativi della calibrazione);
- ``fpr_llm``: baseline senza watermark sopra soglia, Clopper-Pearson; per MCGMark sulla baseline
  gemella, con ``fpr_llm_baseline`` sulla baseline normale come confronto secondario (decisione
  dell'utente del 9 ottobre 2026);
- ``auroc``: positivi contro negativi umani (``FAILED`` = punteggio minimo), bootstrap per
  problema sui positivi e ricampionamento dei negativi;
- ``pass_at_1``, ``pass_at_1_baseline``, ``delta_pass_at_1_pp`` (baseline − marcato, accoppiato
  per problema); per MCGMark il riferimento è la gemella (TODO 10) e ``delta_pass_at_1_pp_baseline``
  usa la baseline normale; per ACW la baseline da cui derivano i campioni;
- ``conditional_preservation`` (solo ACW): P(corretto dopo | corretto prima), Wilson;
- capacità (solo MCGMark): ``message_accuracy``, ``bit_accuracy``, ``embedding_success_rate``.

**Soglie provvisorie** (decisione dell'utente del 9 ottobre 2026): sulla parte di test la fase si
rifiuta di usarle, salvo ``detection.allow_provisional_test`` (solo smoke test).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import MetricValue, ThresholdSet
from bench.methods.base import Detector
from bench.metrics.calibration import MIN_SCORE, detected
from bench.metrics.stats import auroc, auroc_interval, clopper_pearson, ratio_interval, wilson
from bench.metrics.uncertainty import ClusterBootstrap, bootstrap_seed, pass_at_1
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.calibrate import json_threshold, threshold_ref, with_secondary
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.watermark import make_adapter, watermarked_ref
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

MESSAGE_BITS = 12  # D1


def metrics_ref(
    method: str, model_id: str, cfg_hash: str, level: str, language: str, split: str
) -> ArtifactRef:
    return ArtifactRef.of(
        "metrics", f"metrics/{method}/{model_id}/{cfg_hash}/{level}_{language}_{split}.parquet"
    )


def _scores(table: pd.DataFrame, with_embed: bool = False) -> np.ndarray[Any, Any]:
    """Punteggi con ``MIN_SCORE`` per rilevazione o (positivi) inserimento falliti."""
    score = pd.to_numeric(table["score"], errors="coerce")
    bad = (table["status"] != "OK") | score.isna()
    if with_embed:
        bad = bad | (table["embed_status"] == "FAILED")
    return np.where(bad, MIN_SCORE, score).astype(float)


def _detected(table: pd.DataFrame, threshold: float) -> pd.Series:
    return pd.Series(
        [
            detected(
                None if pd.isna(s) else float(s),
                str(st),
                None if pd.isna(e) else str(e),
                threshold,
            )
            for s, st, e in zip(
                pd.to_numeric(table["score"], errors="coerce"),
                table["status"],
                table.get("embed_status", pd.Series([None] * len(table))),
                strict=True,
            )
        ],
        index=table.index,
    )


@STAGES.register("metrics")
class MetricsStage(Stage):
    """Metriche di una cella sulla parte indicata."""

    name: ClassVar[str] = "metrics"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"metrics"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "level", "language", "split")

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._adapters: dict[str, Any] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("metrics needs the experiment configuration")
        return self.config

    def adapter(self, method: str) -> Any:
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return self._adapters[method]

    def config_hash(self, method: str) -> str:
        adapter = self.adapter(method)
        return str(adapter.config_hash(adapter.default_hparams()))

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str, str, str]:
        if not (cell.method and cell.model_id and cell.level and cell.language and cell.split):
            raise ConfigError(
                f"metrics needs method, model_id, level, language, split: {cell.key()}"
            )
        return cell.method, cell.model_id, cell.level, cell.language, cell.split

    def _twin(self, method: str) -> bool:
        return bool(getattr(self.adapter(method), "twin_baseline", False))

    def _refs(self, cell: Cell) -> dict[str, ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(method)

        def det(subject: str) -> ArtifactRef:
            return detection_ref(method, model_id, cfg_hash, subject, level, language, split)

        def exe(source: str, with_method: bool) -> ArtifactRef:
            c = Cell(
                source=source,
                method=method if with_method else None,
                model_id=model_id,
                level=level,
                language=language,
                split=split,
            )
            return execution_ref(c, cfg_hash if with_method else None)

        refs = {
            "threshold": threshold_ref(method, model_id, language, cfg_hash),
            "positives": det("positives"),
            "neg_human": det("neg_human"),
            "neg_llm": det("neg_llm"),
            "watermarked": watermarked_ref(method, model_id, cfg_hash, level, language, split),
            "exec_marked": exe("llm_watermarked", True),
            "exec_baseline": exe("llm_baseline", False),
        }
        if self._twin(method):
            refs["neg_llm_twin"] = det("neg_llm_twin")
            refs["exec_twin"] = exe("baseline_twin", True)
        return refs

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return list(self._refs(cell).values())

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        return [metrics_ref(method, model_id, self.config_hash(method), level, language, split)]

    # ------------------------------------------------------------------ esecuzione
    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(method)
        refs = self._refs(cell)
        thresholds = ctx.store.read_model(refs["threshold"], ThresholdSet)
        if split == "test" and thresholds.provisional and not cfg.detection.allow_provisional_test:
            raise ConfigError(
                f"{cell.key()}: thresholds are provisional (levels {thresholds.levels}); "
                "no test metrics until they are recalibrated on L1+L2 (Milestone 9)"
            )
        tau = thresholds.threshold
        cell_info = {
            "method": method,
            "model_id": model_id,
            "level": level,
            "language": language,
            "split": split,
            "config_hash": cfg_hash,
        }
        rows: list[MetricValue] = []
        n_boot = cfg.detection.n_bootstrap

        def add(name: str, value: float, ci: tuple[float, float], how: str, n: int) -> None:
            rows.append(
                MetricValue(
                    name=name,
                    value=float(value),
                    ci_low=None if math.isnan(ci[0]) else ci[0],
                    ci_high=None if math.isnan(ci[1]) else ci[1],
                    ci_method=how,
                    n=n,
                    cell=dict(cell_info),
                )
            )

        def boot(name: str) -> ClusterBootstrap:
            return ClusterBootstrap(
                bootstrap_seed(cfg.global_seed, name, cell.key()), n_resamples=n_boot
            )

        positives = ctx.store.read_table(refs["positives"])
        applicable = positives[positives["status"] != "NOT_APPLICABLE"]
        if applicable.empty:
            self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=True)
            return
        neg_human = ctx.store.read_table(refs["neg_human"])

        # --- rilevabilità
        hits = _detected(applicable, tau)
        table = applicable.assign(hit=hits.astype(float))
        add(
            "tpr_at_fpr",
            float(table["hit"].mean()),
            ratio_interval(
                table, "hit", bootstrap_seed(cfg.global_seed, "tpr_at_fpr", cell.key()), n_boot
            ),
            "cluster_bootstrap",
            len(table),
        )
        for name, ref_key in self._fpr_subjects(method):
            negatives = ctx.store.read_table(refs[ref_key])
            k = int(_detected(negatives, tau).sum())
            add(
                name,
                k / len(negatives),
                clopper_pearson(k, len(negatives)),
                "clopper_pearson",
                len(negatives),
            )
        pos_scores = applicable.assign(score=_scores(applicable, with_embed=True))
        neg_scores = _scores(neg_human)
        add(
            "auroc",
            auroc(pos_scores["score"].to_numpy(dtype=float), neg_scores),
            auroc_interval(
                pos_scores, neg_scores, bootstrap_seed(cfg.global_seed, "auroc", cell.key()), n_boot
            ),
            "cluster_bootstrap+negative_resampling",
            len(pos_scores) + len(neg_scores),
        )

        # --- punteggi secondari (varianti del paper): soglia propria, stessi positivi e negativi
        adapter = self.adapter(method)
        for name in adapter.secondary_scores:
            sec = thresholds.secondary.get(name)
            if sec is None:
                raise ConfigError(f"{cell.key()}: no threshold for the secondary score {name}")
            sec_pos = with_secondary(applicable, adapter, name)
            sec_hits = _detected(sec_pos, sec["threshold"]).astype(float)
            sec_table = sec_pos.assign(hit=sec_hits)
            add(
                f"tpr_at_fpr_{name}",
                float(sec_table["hit"].mean()),
                ratio_interval(
                    sec_table,
                    "hit",
                    bootstrap_seed(cfg.global_seed, f"tpr_at_fpr_{name}", cell.key()),
                    n_boot,
                ),
                "cluster_bootstrap",
                len(sec_table),
            )
            sec_scores = sec_pos.assign(score=_scores(sec_pos, with_embed=True))
            sec_neg = _scores(with_secondary(neg_human, adapter, name))
            add(
                f"auroc_{name}",
                auroc(sec_scores["score"].to_numpy(dtype=float), sec_neg),
                auroc_interval(
                    sec_scores,
                    sec_neg,
                    bootstrap_seed(cfg.global_seed, f"auroc_{name}", cell.key()),
                    n_boot,
                ),
                "cluster_bootstrap+negative_resampling",
                len(sec_scores) + len(sec_neg),
            )

        # --- preservazione funzionale
        marked_exec = ctx.store.read_table(refs["exec_marked"])
        reference = "exec_twin" if self._twin(method) else "exec_baseline"
        self._pass_metrics(add, boot, marked_exec, ctx.store.read_table(refs[reference]), "")
        if self._twin(method):
            self._pass_metrics(
                add, boot, marked_exec, ctx.store.read_table(refs["exec_baseline"]), "_baseline",
                only_delta=True,
            )  # fmt: skip
        if method == "acw":
            self._conditional_preservation(
                add, ctx.store.read_table(refs["watermarked"]), marked_exec,
                ctx.store.read_table(refs["exec_baseline"]),
            )  # fmt: skip
        if self._multibit(method):
            self._capacity(add, boot, applicable)
        self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=False)

    def _multibit(self, method: str) -> bool:
        """Metodi con messaggio (ridefiniscono ``Detector.message_for``): capacità §13.5."""
        return type(self.adapter(method)).message_for is not Detector.message_for

    def _fpr_subjects(self, method: str) -> list[tuple[str, str]]:
        subjects = [("fpr_human", "neg_human")]
        if self._twin(method):
            subjects += [("fpr_llm", "neg_llm_twin"), ("fpr_llm_baseline", "neg_llm")]
        else:
            subjects.append(("fpr_llm", "neg_llm"))
        return subjects

    @staticmethod
    def _pass_metrics(
        add: Callable[..., None],
        boot: Callable[[str], ClusterBootstrap],
        marked: pd.DataFrame,
        baseline: pd.DataFrame,
        suffix: str,
        only_delta: bool = False,
    ) -> None:
        p_marked, per_marked = pass_at_1(marked)
        p_base, per_base = pass_at_1(baseline)
        if not only_delta:
            add(
                "pass_at_1",
                p_marked,
                boot("pass_at_1").mean_interval(per_marked),
                "cluster_bootstrap",
                len(per_marked),
            )
            add(
                "pass_at_1_baseline",
                p_base,
                boot("pass_at_1_baseline").mean_interval(per_base),
                "cluster_bootstrap",
                len(per_base),
            )
        # Differenza accoppiata per problema (stessi problemi, in ordine di problem_key).
        m = (marked["status"] == "PASSED").astype(float).groupby(marked["problem_key"]).mean()
        b = (baseline["status"] == "PASSED").astype(float).groupby(baseline["problem_key"]).mean()
        common = m.index.intersection(b.index)
        diff = (b[common].to_numpy(dtype=float) - m[common].to_numpy(dtype=float)) * 100.0
        name = f"delta_pass_at_1_pp{suffix}"
        add(
            name,
            float(diff.mean()) if diff.size else math.nan,
            boot(name).mean_interval(diff),
            "cluster_bootstrap",
            int(diff.size),
        )

    @staticmethod
    def _conditional_preservation(
        add: Callable[..., None],
        watermarked: pd.DataFrame,
        marked_exec: pd.DataFrame,
        baseline_exec: pd.DataFrame,
    ) -> None:
        """ACW: P(corretto dopo | corretto prima) sui campioni della baseline corretti (Wilson)."""
        parent_of = dict(zip(watermarked["sample_id"], watermarked["parent_id"], strict=True))
        passed_before = set(baseline_exec.loc[baseline_exec["status"] == "PASSED", "sample_id"])
        pairs = [
            status == "PASSED"
            for sid, status in zip(marked_exec["sample_id"], marked_exec["status"], strict=True)
            if parent_of.get(sid) in passed_before
        ]
        k, n = int(sum(pairs)), len(pairs)
        add("conditional_preservation", k / n if n else math.nan, wilson(k, n), "wilson", n)

    @staticmethod
    def _capacity(
        add: Callable[..., None], boot: Callable[[str], ClusterBootstrap], positives: pd.DataFrame
    ) -> None:
        """MCGMark (SPEC §13.5): bit corretti dei positivi; rilevazione fallita = 0 bit."""
        bits = pd.to_numeric(positives["bits_correct"], errors="coerce").fillna(0)
        bits = bits.where(positives["status"] == "OK", 0)
        n = len(positives)
        full = int((bits == MESSAGE_BITS).sum())
        add("message_accuracy", full / n, wilson(full, n), "wilson", n)
        table = positives.assign(bit_frac=bits / MESSAGE_BITS)
        add(
            "bit_accuracy",
            float(table["bit_frac"].mean()),
            ratio_interval(
                table, "bit_frac", boot("bit_accuracy").seed, boot("bit_accuracy").n_resamples
            ),
            "cluster_bootstrap",
            n,
        )
        ok = int((positives["embed_status"] == "OK").sum())
        add("embedding_success_rate", ok / n, wilson(ok, n), "wilson", n)

    def _write(
        self,
        ctx: StageContext,
        cell: Cell,
        rows: list[MetricValue],
        cfg_hash: str,
        thresholds: ThresholdSet,
        not_applicable: bool,
    ) -> None:
        records = [r.model_dump(mode="json") for r in rows]
        df = pd.DataFrame(records, columns=list(MetricValue.model_fields))
        df["cell"] = df["cell"].map(lambda c: ";".join(f"{k}={v}" for k, v in sorted(c.items())))
        ref = self.outputs(cell)[0]
        extra = {
            "threshold": json_threshold(thresholds.threshold),
            "achieved_fpr_dev": thresholds.achieved_fpr_dev,
            "provisional_thresholds": thresholds.provisional,
            "threshold_levels": thresholds.levels,
            "underpowered": thresholds.underpowered,
            "not_applicable": not_applicable,
            "metrics": {r.name: r.value for r in rows},
        }
        manifest = ctx.make_manifest(
            ref,
            inputs=self.inputs(cell),
            n_rows_in=None,
            n_rows_out=len(df),
            n_rows_expected=len(rows),  # una riga per metrica calcolata
            config_hash=cfg_hash,
            extra=extra,
        )
        ctx.store.write_table(ref, df, manifest)
        logger.info("%s: %s", cell.key(), {r.name: round(r.value, 4) for r in rows})
