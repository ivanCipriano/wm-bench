"""Fasi dell'impercettibilità (Milestone 12; SPEC §13.4; ADR-015; D37).

- ``perplexity`` (GPU): NLL per token con ``StarCoder2-7B`` di ogni campione di una sorgente
  (``llm_baseline``, ``llm_watermarked``, ``baseline_twin``), come ``execute`` per le sorgenti;
- ``adversarial_logreg`` (CPU) e ``adversarial_unixcoder`` (GPU): predizioni out-of-fold dei due
  classificatori avversari per ogni riferimento del metodo (baseline; per MCGMark la gemella e, come
  confronto secondario, la baseline normale);
- ``adversarial_logreg_fmtfree`` e ``adversarial_unixcoder_fmtfree``: gli stessi classificatori sul
  codice senza la formattazione di ACW (``imp.format_free``), misura secondaria solo per ACW;
- ``metrics_imperceptibility`` (CPU): CodeBLEU, ΔNLL e ΔPPL e AUROC dei classificatori con IC,
  campioni vuoti per parte (``bench.metrics.imperceptibility``).

Output sotto ``imperceptibility/`` negli artefatti. Celle sulla parte di test con la
configurazione congelata del metodo (``hpo.use_selected``, ADR-013).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import replace
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.domain.ids import derive_seed
from bench.domain.models import MetricValue
from bench.hpo.grid import resolve
from bench.metrics import imperceptibility as imp
from bench.metrics.adversarial import Array, Splits, folds, logreg_oof, unixcoder_oof
from bench.metrics.stats import auroc, wilson
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.generate_baseline import baseline_ref
from bench.pipeline.stages.watermark import (
    baseline_twin_ref,
    make_adapter,
    twin_config_hash,
    watermarked_ref,
)
from bench.registry import METHODS, STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

OBSERVER = "starcoder2_7b"
CLASSIFIER = "unixcoder_base"
PPL_SOURCES = ("llm_baseline", "llm_watermarked", "baseline_twin")
VARIANTS = ("logreg", "unixcoder")
# Classificatori sul codice "al netto della formattazione" (``imp.format_free``), misura
# secondaria solo per ACW (decisione dell'utente del 29 ottobre 2026: regole 44 e 45).
FORMAT_FREE_METHODS = frozenset({"acw"})
FORMAT_FREE = "_fmtfree"
EMPTY_GAP = 0.02  # differenza delle frazioni di vuoti da segnalare (decisione dell'utente)


def ppl_ref(
    source: str, method: str | None, model_id: str, cfg_hash: str | None, level: str,
    language: str, split: str,
) -> ArtifactRef:  # fmt: skip
    """NLL per campione di una sorgente (``cfg_hash``: del marcato, o della gemella)."""
    tail = f"{level}_{language}_{split}.parquet"
    if source == "llm_baseline":
        return ArtifactRef.of("ppl", f"imperceptibility/ppl/baseline/{model_id}/{tail}")
    folder = {"llm_watermarked": "marked", "baseline_twin": "twin"}[source]
    return ArtifactRef.of(
        "ppl", f"imperceptibility/ppl/{folder}/{method}/{model_id}/{cfg_hash}/{tail}"
    )


def adversarial_ref(
    variant: str, method: str, model_id: str, cfg_hash: str, reference: str, level: str,
    language: str, split: str,
) -> ArtifactRef:  # fmt: skip
    """Predizioni out-of-fold di un classificatore contro un riferimento (``baseline``/``twin``)."""
    return ArtifactRef.of(
        "adversarial",
        f"imperceptibility/adversarial/{variant}/{method}/{model_id}/{cfg_hash}/{reference}/"
        f"{level}_{language}_{split}.parquet",
    )


def imperceptibility_ref(
    method: str, model_id: str, cfg_hash: str, level: str, language: str, split: str
) -> ArtifactRef:
    """Metriche d'impercettibilità di una cella."""
    return ArtifactRef.of(
        "imperceptibility_metrics",
        f"imperceptibility/metrics/{method}/{model_id}/{cfg_hash}/{level}_{language}_{split}.parquet",
    )


def _has_twin(method: str) -> bool:
    import bench.methods.adapters  # noqa: F401  (popola METHODS)

    return bool(getattr(METHODS.get(method), "twin_baseline", False))


def references(method: str) -> list[str]:
    """Riferimenti del metodo, il principale per primo (TODO 10: gemella per MCGMark)."""
    return ["twin", "baseline"] if _has_twin(method) else ["baseline"]


class _MethodStage(Stage):
    """Base delle fasi con una cella per (metodo, modello, livello, linguaggio, parte)."""

    cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "level", "language", "split")

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._adapters: dict[str, Any] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError(f"{self.name} needs the experiment configuration")
        return self.config

    def adapter(self, method: str) -> Any:
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return self._adapters[method]

    def cfg_hash(self, cell: Cell) -> str:
        return str(resolve(self._cfg(), self.adapter(str(cell.method)), cell.config_hash)[1])

    def twin_hash(self, cell: Cell) -> str:
        return twin_config_hash(self._cfg(), self.adapter(str(cell.method)), self.cfg_hash(cell))

    @staticmethod
    def parts(cell: Cell) -> tuple[str, str, str, str, str]:
        if not (cell.method and cell.model_id and cell.level and cell.language and cell.split):
            raise ConfigError(f"needs method, model_id, level, language, split: {cell.key()}")
        return cell.method, cell.model_id, cell.level, cell.language, cell.split

    def samples(self, cell: Cell, reference: str | None) -> ArtifactRef:
        """Campioni marcati (``reference=None``) o del riferimento."""
        method, model_id, level, language, split = self.parts(cell)
        if reference is None:
            return watermarked_ref(method, model_id, self.cfg_hash(cell), level, language, split)
        if reference == "twin":
            return baseline_twin_ref(method, model_id, self.twin_hash(cell), level, language, split)
        return baseline_ref(model_id, level, language, split)

    def ppl(self, cell: Cell, reference: str | None) -> ArtifactRef:
        method, model_id, level, language, split = self.parts(cell)
        if reference is None:
            return ppl_ref("llm_watermarked", method, model_id, self.cfg_hash(cell), level,
                           language, split)  # fmt: skip
        if reference == "twin":
            return ppl_ref("baseline_twin", method, model_id, self.twin_hash(cell), level,
                           language, split)  # fmt: skip
        return ppl_ref("llm_baseline", None, model_id, None, level, language, split)


# ---------------------------------------------------------------------------- perplexity
@STAGES.register("perplexity")
class PerplexityStage(_MethodStage):
    """NLL per token dei campioni di una sorgente con l'osservatore ``StarCoder2-7B``."""

    name: ClassVar[str] = "perplexity"
    resources: ClassVar[ResourceClass] = ResourceClass.GPU_NVIDIA
    output_kinds: ClassVar[frozenset[str]] = frozenset({"ppl"})
    cell_axes: ClassVar[tuple[str, ...]] = (
        "source", "method", "model_id", "level", "language", "split",
    )  # fmt: skip

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        if cell.source not in PPL_SOURCES:
            return None
        if cell.source == "llm_baseline":
            return replace(cell, method=None)
        if cell.source == "baseline_twin" and not (cell.method and _has_twin(cell.method)):
            return None
        return cell

    def _source_refs(self, cell: Cell) -> tuple[ArtifactRef, ArtifactRef]:
        if not (cell.model_id and cell.level and cell.language and cell.split):
            raise ConfigError(f"perplexity needs model_id, level, language, split: {cell.key()}")
        if cell.source == "llm_baseline":
            return (
                baseline_ref(cell.model_id, cell.level, cell.language, cell.split),
                ppl_ref("llm_baseline", None, cell.model_id, None, cell.level, cell.language,
                        cell.split),
            )  # fmt: skip
        reference = "twin" if cell.source == "baseline_twin" else None
        return self.samples(cell, reference), self.ppl(cell, reference)

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return [self._source_refs(cell)[0]]

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [self._source_refs(cell)[1]]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        from bench.metrics.perplexity import MAX_TOKENS, token_nll

        cfg = self._cfg()
        source, out = self._source_refs(cell)
        table = ctx.store.read_table(source)
        observer = cfg.models_catalog[OBSERVER]
        dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16}[str(observer.dtype)]
        tokenizer = AutoTokenizer.from_pretrained(str(observer.tokenizer_path),
                                                  local_files_only=True)  # fmt: skip
        model = AutoModelForCausalLM.from_pretrained(
            str(observer.path), torch_dtype=dtype, local_files_only=True
        ).to("cuda:0")
        codes = ["" if imp.is_empty(c) else str(c) for c in table["code"]]
        values = token_nll(model, tokenizer, codes, batch_size=4)
        df = pd.DataFrame(
            {
                "sample_id": table["sample_id"].astype(str),
                "problem_key": table["problem_key"].astype(str),
                "sample_index": table["sample_index"],
                "nll": [v[0] for v in values],
                "n_tokens": [v[1] for v in values],
                "empty": [imp.is_empty(c) for c in table["code"]],
            }
        )
        df["ppl"] = np.exp(df["nll"])
        extra = {
            "observer": observer.hf_repo,
            "observer_snapshot": observer.snapshot,
            "dtype": str(observer.dtype),
            "max_tokens": MAX_TOKENS,
            "n_empty": int(df["empty"].sum()),
            "n_truncated": int((df["n_tokens"] >= MAX_TOKENS - 1).sum()),
        }
        ctx.store.write_table(
            out,
            df,
            ctx.make_manifest(out, inputs=[source], n_rows_in=len(table), n_rows_out=len(df),
                              n_rows_expected=len(table), extra=extra),
        )  # fmt: skip
        logger.info("%s: %d samples, %d empty", out.path, len(df), extra["n_empty"])


# ---------------------------------------------------------------------------- classificatori
class _AdversarialStage(_MethodStage):
    variant: ClassVar[str]
    output_kinds: ClassVar[frozenset[str]] = frozenset({"adversarial"})
    format_free: ClassVar[bool] = False  # codice normalizzato con ``imp.format_free``

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        if cls.format_free and cell.method not in FORMAT_FREE_METHODS:
            return None
        return cell

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        refs = [self.samples(cell, None)]
        refs.extend(self.samples(cell, r) for r in references(str(cell.method)))
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self.parts(cell)
        h = self.cfg_hash(cell)
        return [
            adversarial_ref(self.variant, method, model_id, h, r, level, language, split)
            for r in references(method)
        ]

    def fit(
        self, codes: list[str], labels: Array, groups: Array, splits: Splits,
        language: Language, seed: int,
    ) -> tuple[Array, list[dict[str, Any]]]:  # fmt: skip
        raise NotImplementedError

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self.parts(cell)
        marked = imp.non_empty(ctx.store.read_table(self.samples(cell, None)))
        for reference, out in zip(references(method), self.outputs(cell), strict=True):
            negatives = imp.non_empty(ctx.store.read_table(self.samples(cell, reference)))
            data = pd.concat(
                [marked.assign(label=1), negatives.assign(label=0)], ignore_index=True
            )[["sample_id", "problem_key", "code", "label"]]
            # stessi fold per le due varianti: il seme non dipende dal classificatore
            seed = derive_seed(cfg.global_seed, "adversarial-folds", method, model_id, language,
                               reference)  # fmt: skip
            splits = folds(data["problem_key"].astype(str), data["label"], seed)
            labels = data["label"].to_numpy()
            groups = data["problem_key"].astype(str).to_numpy()
            codes = [str(c) for c in data["code"]]
            if self.format_free:
                codes = [imp.format_free(c) for c in codes]
            scores, log = self.fit(codes, labels, groups, splits, Language(language), seed)
            fold_of = np.empty(len(data), dtype=int)
            for k, (_, test) in enumerate(splits):
                fold_of[test] = k
            df = data[["sample_id", "problem_key", "label"]].assign(score=scores, fold=fold_of)
            fold_auc = []
            for k in range(len(splits)):
                part = df[df["fold"] == k]
                pos = part.loc[part["label"] == 1, "score"].to_numpy()
                neg = part.loc[part["label"] == 0, "score"].to_numpy()
                fold_auc.append(auroc(pos, neg))
            extra = {
                "variant": self.variant,
                "reference": reference,
                "n_positives": int(labels.sum()),
                "n_negatives": int((labels == 0).sum()),
                "folds_disjoint_by_problem": True,  # check_folds non ha sollevato
                "format_free": self.format_free,
                "fold_auroc": fold_auc,
                "fold_log": log,
            }
            ctx.store.write_table(
                out, df,
                ctx.make_manifest(out, inputs=self.inputs(cell), n_rows_in=len(data),
                                  n_rows_out=len(df), n_rows_expected=len(data), extra=extra),
            )  # fmt: skip
            logger.info("%s: fold AUROC %s", out.path, [round(a, 3) for a in fold_auc])


@STAGES.register("adversarial_logreg")
class AdversarialLogregStage(_AdversarialStage):
    """Regressione logistica su feature esplicite."""

    name: ClassVar[str] = "adversarial_logreg"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    variant: ClassVar[str] = "logreg"

    def fit(
        self, codes: list[str], labels: Array, groups: Array, splits: Splits,
        language: Language, seed: int,
    ) -> tuple[Array, list[dict[str, Any]]]:  # fmt: skip
        return logreg_oof(codes, labels, groups, splits, language, seed)


@STAGES.register("adversarial_unixcoder")
class AdversarialUnixcoderStage(_AdversarialStage):
    """UniXcoder con testa di classificazione."""

    name: ClassVar[str] = "adversarial_unixcoder"
    resources: ClassVar[ResourceClass] = ResourceClass.GPU_NVIDIA
    variant: ClassVar[str] = "unixcoder"

    def fit(
        self, codes: list[str], labels: Array, groups: Array, splits: Splits,
        language: Language, seed: int,
    ) -> tuple[Array, list[dict[str, Any]]]:  # fmt: skip
        path = str(self._cfg().models_catalog[CLASSIFIER].path)
        return unixcoder_oof(codes, labels, groups, splits, path, seed)


@STAGES.register("adversarial_logreg_fmtfree")
class AdversarialLogregFormatFreeStage(AdversarialLogregStage):
    """Regressione logistica sul codice senza la formattazione di ACW (secondaria)."""

    name: ClassVar[str] = "adversarial_logreg_fmtfree"
    variant: ClassVar[str] = "logreg" + FORMAT_FREE
    format_free: ClassVar[bool] = True


@STAGES.register("adversarial_unixcoder_fmtfree")
class AdversarialUnixcoderFormatFreeStage(AdversarialUnixcoderStage):
    """UniXcoder sul codice senza la formattazione di ACW (secondaria)."""

    name: ClassVar[str] = "adversarial_unixcoder_fmtfree"
    variant: ClassVar[str] = "unixcoder" + FORMAT_FREE
    format_free: ClassVar[bool] = True


def adversarial_metrics(
    add: Callable[..., None], oof: pd.DataFrame, fold_auc: list[float], name: str, seed: int,
    n_boot: int,
) -> None:  # fmt: skip
    """AUROC out-of-fold di un classificatore (IC bootstrap per problema) e media fra i fold
    (media ± deviazione standard)."""
    pos = oof.loc[oof["label"] == 1, "score"].to_numpy()
    neg = oof.loc[oof["label"] == 0, "score"].to_numpy()
    groups = {k: g for k, g in oof.groupby("problem_key", sort=True)}
    keys = list(groups)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        sample = pd.concat([groups[keys[j]] for j in rng.integers(0, len(keys), len(keys))])
        p = sample.loc[sample["label"] == 1, "score"].to_numpy()
        q = sample.loc[sample["label"] == 0, "score"].to_numpy()
        if len(p) and len(q):
            boots.append(auroc(p, q))
    ci = tuple(np.percentile(boots, [2.5, 97.5])) if boots else (float("nan"),) * 2
    add(f"{name}_auroc_oof", auroc(pos, neg), (float(ci[0]), float(ci[1])), "cluster_bootstrap",
        len(oof))  # fmt: skip
    if fold_auc:
        mean, sd = float(np.mean(fold_auc)), float(np.std(fold_auc, ddof=1))
        add(f"{name}_auroc_fold_mean", mean, (mean - sd, mean + sd), "mean_pm_sd_folds",
            len(fold_auc))  # fmt: skip


# ---------------------------------------------------------------------------- metriche
@STAGES.register("metrics_imperceptibility")
class ImperceptibilityMetricsStage(_MethodStage):
    """CodeBLEU, ΔNLL/ΔPPL e AUROC dei classificatori di una cella, con IC."""

    name: ClassVar[str] = "metrics_imperceptibility"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"imperceptibility_metrics"})

    def _adv(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self.parts(cell)
        h = self.cfg_hash(cell)
        return [
            adversarial_ref(v, method, model_id, h, r, level, language, split)
            for v in VARIANTS
            for r in references(method)
        ]

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        method = str(cell.method)
        refs = [self.samples(cell, None), self.ppl(cell, None)]
        for r in references(method):
            refs += [self.samples(cell, r), self.ppl(cell, r)]
        return refs + self._adv(cell)

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self.parts(cell)
        return [imperceptibility_ref(method, model_id, self.cfg_hash(cell), level, language,
                                     split)]  # fmt: skip

    def secondary_pairing(self, method: str) -> tuple[str, tuple[str, str]] | None:
        """Accoppiamento secondario: per indice (STONE, SWEET: un seme per problema) o esatto
        (ACW: ``parent_id``); nessuno per i metodi con un seme per campione."""
        from bench.methods.base import CodeEmbedder

        adapter = self.adapter(method)
        if isinstance(adapter, CodeEmbedder):
            return "exact", ("parent_id", "sample_id")
        if not getattr(adapter, "one_sample_per_item", False):
            return "index", ("sample_index", "sample_index")
        return None

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self.parts(cell)
        cfg_hash = self.cfg_hash(cell)
        info = {"method": method, "model_id": model_id, "level": level, "language": language,
                "split": split, "config_hash": cfg_hash}  # fmt: skip
        rows: list[MetricValue] = []
        n_boot = cfg.detection.n_bootstrap

        def add(name: str, value: float, ci: tuple[float, float], how: str, n: int) -> None:
            rows.append(MetricValue(
                name=name, value=float(value),
                ci_low=None if math.isnan(ci[0]) else ci[0],
                ci_high=None if math.isnan(ci[1]) else ci[1],
                ci_method=how, n=int(n), cell=dict(info),
            ))  # fmt: skip

        def per_problem(name: str, values: pd.Series, median: bool = False) -> None:
            seed = derive_seed(cfg.global_seed, "bootstrap", name, cell.key())
            stat: Callable[[Any], float] = np.median if median else np.mean
            value, ci = imp.bootstrap_stat(values, stat, seed, n_boot)
            add(name, value, ci, "cluster_bootstrap", len(values))

        marked = ctx.store.read_table(self.samples(cell, None))
        marked_ppl = ctx.store.read_table(self.ppl(cell, None))
        marked = marked.merge(marked_ppl[["sample_id", "nll", "ppl"]], on="sample_id")
        score = self._scorer(language)
        flags: dict[str, Any] = {}
        for i, reference in enumerate(references(method)):
            suffix = "" if i == 0 else f"_{reference}"
            ref = ctx.store.read_table(self.samples(cell, reference))
            ref = ref.merge(ctx.store.read_table(self.ppl(cell, reference))[
                ["sample_id", "nll", "ppl"]], on="sample_id")  # fmt: skip
            # campioni vuoti per parte (D37)
            em, nm = imp.empty_counts(marked)
            er, nr = imp.empty_counts(ref)
            add(f"empty_fraction_marked{suffix}", em / max(1, nm), wilson(em, nm), "wilson", nm)
            add(f"empty_fraction_reference{suffix}", er / max(1, nr), wilson(er, nr), "wilson", nr)
            gap = abs(em / max(1, nm) - er / max(1, nr))
            flags[f"empty_gap{suffix}"] = {"gap": gap, "flag": gap > EMPTY_GAP}
            # CodeBLEU: principale, variabilità naturale e differenza
            cross = imp.cross_mean(marked, ref, score)
            natural = imp.within_mean(ref, score)
            common = cross.index.intersection(natural.index)
            per_problem(f"codebleu{suffix}", cross)
            per_problem(f"codebleu_natural{suffix}", natural)
            per_problem(f"codebleu_minus_natural{suffix}", cross.loc[common] - natural.loc[common])
            # ΔNLL (principale) e ΔPPL: differenza delle medie per problema
            for col in ("nll", "ppl"):
                diff = imp.difference_of_means(marked, ref, col)
                per_problem(f"delta_{col}_mean{suffix}", diff)
                per_problem(f"delta_{col}_median{suffix}", diff, median=True)
            # classificatori avversari
            for variant in VARIANTS:
                self._adversarial(ctx, cell, variant, reference, suffix, add, cfg_hash)
            if i == 0:
                self._secondary(marked, ref, method, language, score, per_problem)
        out = self.outputs(cell)[0]
        df = pd.DataFrame([r.model_dump() for r in rows])
        ctx.store.write_table(
            out, df,
            ctx.make_manifest(out, inputs=self.inputs(cell), n_rows_in=len(marked),
                              n_rows_out=len(df), n_rows_expected=len(df), config_hash=cfg_hash,
                              extra={"empty_gaps": flags, "principal_ppl_metric": "delta_nll"}),
        )  # fmt: skip

    @staticmethod
    def _scorer(language: str) -> Callable[[str, str], float]:
        cache: dict[tuple[str, str], float] = {}

        def score(reference: str, hypothesis: str) -> float:
            key = (reference, hypothesis)
            if key not in cache:
                cache[key] = imp.codebleu_score(reference, hypothesis, language)
            return cache[key]

        return score

    def _secondary(
        self, marked: pd.DataFrame, ref: pd.DataFrame, method: str, language: str,
        score: Callable[[str, str], float], per_problem: Callable[..., None],
    ) -> None:  # fmt: skip
        """Accoppiamento per indice (STONE, SWEET) o esatto (ACW), come misura secondaria."""
        pairing = self.secondary_pairing(method)
        if pairing is None:
            return
        name, on = pairing
        pairs = imp.paired(marked, ref, on)
        bleu = pd.Series([score(r, m) for m, r in zip(pairs["code_m"], pairs["code_r"],
                                                      strict=True)])  # fmt: skip
        per_problem(f"codebleu_{name}", imp.per_problem_of_pairs(pairs, bleu))
        for col in ("nll", "ppl"):
            diff = (pairs[f"{col}_m"] - pairs[f"{col}_r"]).reset_index(drop=True)
            per_problem(f"delta_{col}_{name}", imp.per_problem_of_pairs(pairs, diff))

    def _adversarial(
        self, ctx: StageContext, cell: Cell, variant: str, reference: str, suffix: str,
        add: Callable[..., None], cfg_hash: str,
    ) -> None:  # fmt: skip
        method, model_id, level, language, split = self.parts(cell)
        ref = adversarial_ref(variant, method, model_id, cfg_hash, reference, level, language,
                              split)  # fmt: skip
        oof = ctx.store.read_table(ref)
        manifest = ctx.store.read_manifest(ref)
        fold_auc = list((manifest.extra or {}).get("fold_auroc", [])) if manifest else []
        name = f"adversarial_{variant}"
        seed = derive_seed(self._cfg().global_seed, "bootstrap", f"{name}{suffix}", cell.key())

        def add_suffixed(metric: str, *args: Any) -> None:
            add(f"{metric}{suffix}", *args)

        adversarial_metrics(add_suffixed, oof, fold_auc, name, seed,
                            self._cfg().detection.n_bootstrap)  # fmt: skip
