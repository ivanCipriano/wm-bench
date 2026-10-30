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

**Livello 3** (ADR-016): Pass@1, ΔPass@1 e preservazione condizionata solo su ClassEval (91 classi,
D41); per CodeSearchNet e The Stack ``tree_sitter_valid`` (principale), ``syntax_check``
(``py_compile``, ``node --check``) o ``compile_info`` (``javac``, ``g++``, solo informazione), con
baseline e differenza; TPR, FPR e AUROC anche per dataset (``<metrica>|<dataset>``) e, nel
manifest, i dataset con ``contamination_risk`` (I6).

**Celle con attacco** (M10, ADR-012; ``Cell.attack_id``): stessa soglia congelata della cella senza
attacco; output ``<cartella>/attacked/<attacco>/<params_hash>/<metodo>/...``. Metriche:
``tpr_at_fpr`` e ``auroc`` sugli attaccati (con i punteggi secondari), ``pass_at_1`` degli
attaccati, ``delta_pass_at_1_pp`` (riferimento − attaccato, come per i marcati: gemella per
MCGMark), ``delta_pass_at_1_pp_vs_marked`` (marcato non attaccato − attaccato),
``changed_fraction`` (campioni modificati, Wilson) e ``codebleu_distance`` (1 − CodeBLEU medio,
bootstrap per problema): l'intensità dell'attacco accanto al suo effetto.

**Soglie provvisorie** (decisione dell'utente del 9 ottobre 2026): sulla parte di test la fase si
rifiuta di usarle, salvo ``detection.allow_provisional_test`` (solo smoke test).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.data.loaders.classeval import load_pass1_excluded
from bench.domain.errors import ConfigError
from bench.domain.models import MetricValue, ThresholdSet
from bench.hpo.freeze import data_sha256, load_freeze
from bench.hpo.grid import resolve
from bench.methods.base import Detector
from bench.metrics.calibration import MIN_SCORE, detected
from bench.metrics.stats import auroc, auroc_interval, clopper_pearson, ratio_interval, wilson
from bench.metrics.uncertainty import ClusterBootstrap, bootstrap_seed, pass_at_1
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.calibrate import (
    json_threshold,
    levels_tag,
    primary_view,
    threshold_ref,
    with_secondary,
)
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.watermark import make_adapter, twin_config_hash, watermarked_ref
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

MESSAGE_BITS = 12  # D1


def metrics_ref(
    method: str,
    model_id: str,
    cfg_hash: str,
    level: str,
    language: str,
    split: str,
    threshold_levels: Sequence[str] = ("L1",),
    attack: tuple[str, str] | None = None,
) -> ArtifactRef:
    """Metriche di una cella con le soglie calibrate su ``threshold_levels`` (cartella
    ``metrics_L1L2/`` per le soglie definitive, ``metrics/`` per quelle provvisorie di L1); con
    ``attack`` = (attacco, params_hash) la sottocartella ``attacked/<attacco>/<params_hash>/``."""
    tag = levels_tag(threshold_levels)
    folder = "metrics" + (f"_{tag}" if tag else "")
    if attack is not None:
        folder += f"/attacked/{attack[0]}/{attack[1]}"
    return ArtifactRef.of(
        "metrics", f"{folder}/{method}/{model_id}/{cfg_hash}/{level}_{language}_{split}.parquet"
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


def _attack_info(cell: Cell) -> dict[str, str]:
    """Chiavi dell'attacco nella cella della metrica (vuote senza attacco)."""
    if not cell.attack_id:
        return {}
    return {"attack_id": cell.attack_id, "attack_params_hash": str(cell.attack_params_hash)}


# Dataset di L3 con test (Pass@1); gli altri sono senza test e con contaminazione (I6, ADR-016).
TESTED_L3 = frozenset({"classeval"})
CONTAMINATED_L3 = frozenset({"codesearchnet", "thestack_cpp"})
TREE_SITTER_ERROR = "tree-sitter: ERROR node"


def _ts_valid(table: pd.DataFrame) -> pd.DataFrame:
    """Esecuzioni con ``status`` = ``PASSED`` se il codice è valido per tree-sitter (né estrazione
    fallita né ``SYNTAX_ERROR`` del controllo tree-sitter dell'executor ``syntax``)."""
    invalid = [
        status == "EXTRACTION_FAILED" or str(tail or "").startswith(TREE_SITTER_ERROR)
        for status, tail in zip(table["status"], table["stderr_tail"], strict=True)
    ]
    return table.assign(status=["FAILED" if bad else "PASSED" for bad in invalid])


def _compiled(table: pd.DataFrame) -> pd.DataFrame:
    """Esecuzioni così come sono: ``PASSED`` = supera anche il controllo del compilatore."""
    return table


@STAGES.register("metrics")
class MetricsStage(Stage):
    """Metriche di una cella sulla parte indicata."""

    name: ClassVar[str] = "metrics"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"metrics"})
    cell_axes: ClassVar[tuple[str, ...]] = (
        "method",
        "model_id",
        "level",
        "language",
        "split",
        "attack_id",
    )

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

    def pass1_excluded(self) -> dict[str, str]:
        """Problemi esclusi da Pass@1 e ΔPass@1 (D41: classi di ClassEval che non possono passare
        nella sandbox o dipendono dal caso), ``problem_key`` → motivo."""
        spec = self._cfg().datasets.get("classeval")
        if spec is None or "pass1_excluded" not in spec.files:
            return {}
        return load_pass1_excluded(spec.file("pass1_excluded"))

    def _read_exec(self, ctx: StageContext, ref: ArtifactRef) -> pd.DataFrame:
        """Esecuzioni senza i problemi esclusi da Pass@1 (D41); le altre misure non cambiano."""
        table = ctx.store.read_table(ref)
        excluded = self.pass1_excluded()
        if not excluded or table.empty:
            return table
        return table[~table["problem_key"].isin(excluded)].reset_index(drop=True)

    def adapter(self, method: str) -> Any:
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return self._adapters[method]

    def config_hash(self, cell: Cell | str) -> str:
        """Hash della configurazione della cella (con un nome di metodo: il default)."""
        cell = Cell(method=cell) if isinstance(cell, str) else cell
        return resolve(self._cfg(), self.adapter(str(cell.method)), cell.config_hash)[1]

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
        cfg_hash = self.config_hash(cell)

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
            if not with_method:
                return execution_ref(c, None)
            if source == "baseline_twin":  # gemella comune alle configurazioni (D32)
                return execution_ref(
                    c, twin_config_hash(self._cfg(), self.adapter(method), cfg_hash)
                )
            return execution_ref(c, cfg_hash)

        if cell.attack_id:
            from bench.pipeline.stages.attack import attacked_ref, attacked_subject

            attack_id, params_hash = cell.attack_id, str(cell.attack_params_hash)
            attacked_cell = replace(cell, source="attacked")  # esecuzione degli attaccati
            refs = {
                "threshold": threshold_ref(method, model_id, language, cfg_hash, self.levels()),
                "attacked_det": det(attacked_subject(attack_id, params_hash)),
                "neg_human": det("neg_human"),
                "attacked": attacked_ref(
                    attack_id, params_hash, method, model_id, cfg_hash, level, language, split
                ),
                "exec_attacked": execution_ref(attacked_cell, cfg_hash),
                "watermarked": watermarked_ref(method, model_id, cfg_hash, level, language, split),
                "exec_marked": exe("llm_watermarked", True),
                "exec_baseline": exe("llm_baseline", False),
            }
            if self._twin(method):
                refs["exec_twin"] = exe("baseline_twin", True)
            return refs
        refs = {
            "threshold": threshold_ref(method, model_id, language, cfg_hash, self.levels()),
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
        cfg_hash = self.config_hash(cell)
        attack = (cell.attack_id, str(cell.attack_params_hash)) if cell.attack_id else None
        return [
            metrics_ref(method, model_id, cfg_hash, level, language, split, self.levels(), attack)
        ]

    def levels(self) -> list[str]:
        """Livelli delle soglie: ``detection.threshold_levels`` se c'è (sul test di L1 le soglie
        definitive L1+L2, ADR-013), altrimenti quelli della configurazione (come ``calibrate``)."""
        cfg = self._cfg()
        levels = cfg.detection.threshold_levels or cfg.levels
        return sorted(str(level) for level in levels)

    def _check_test(
        self, ctx: StageContext, cell: Cell, threshold: ArtifactRef, thresholds: ThresholdSet
    ) -> None:
        """Sul test: soglie definitive (non provvisorie) e congelate (ADR-013)."""
        if cell.split != "test" or self._cfg().detection.allow_provisional_test:
            return
        if thresholds.provisional:
            raise ConfigError(
                f"{cell.key()}: thresholds are provisional (levels {thresholds.levels}); "
                "no test metrics until they are recalibrated on L1+L2 (Milestone 9)"
            )
        method, model_id, language = str(cell.method), str(cell.model_id), str(cell.language)
        record = load_freeze(ctx.store, method)
        frozen = record.cell(model_id, language) if record is not None else None
        if frozen is None:
            raise ConfigError(f"{cell.key()}: {method} is not frozen (ADR-013): no test metrics")
        cfg_hash = self.config_hash(cell)
        if frozen.config_hash != cfg_hash:
            raise ConfigError(
                f"{cell.key()}: config {cfg_hash} is not the frozen one ({frozen.config_hash})"
            )
        if data_sha256(ctx.store, threshold) != frozen.threshold_sha256:
            raise ConfigError(
                f"{cell.key()}: threshold {threshold.path} differs from the frozen one "
                "(recalibrated after the freeze?)"
            )

    # ------------------------------------------------------------------ esecuzione
    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(cell)
        refs = self._refs(cell)
        thresholds = ctx.store.read_model(refs["threshold"], ThresholdSet)
        self._check_test(ctx, cell, refs["threshold"], thresholds)
        tau = thresholds.threshold
        cell_info = {
            "method": method,
            "model_id": model_id,
            "level": level,
            "language": language,
            "split": split,
            "config_hash": cfg_hash,
            **_attack_info(cell),
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

        # Punteggio principale del metodo (per STONE z_nonsyntax, D16), come in calibrazione.
        adapter = self.adapter(method)
        if cell.attack_id:
            self._attacked_metrics(ctx, cell, refs, add, boot, tau, thresholds)
            self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=False)
            return
        positives = primary_view(ctx.store.read_table(refs["positives"]), adapter)
        applicable = positives[positives["status"] != "NOT_APPLICABLE"]
        if applicable.empty:
            self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=True)
            return
        neg_human = primary_view(ctx.store.read_table(refs["neg_human"]), adapter)

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
            negatives = primary_view(ctx.store.read_table(refs[ref_key]), adapter)
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
        self._secondary_metrics(cell, add, applicable, neg_human, thresholds)

        if level == "L3":  # TPR, FPR e AUROC anche per dataset, con il flag I6 (ADR-016)
            self._per_dataset(cell, add, applicable, neg_human, tau)

        # --- preservazione funzionale
        marked_exec = self._read_exec(ctx, refs["exec_marked"])
        reference = "exec_twin" if self._twin(method) else "exec_baseline"
        if level == "L3":
            marked_exec = self._l3_functional(ctx, cell, refs, add, boot, marked_exec, reference)
        self._pass_metrics(add, boot, marked_exec, self._l3_exec(ctx, refs[reference], level), "")
        if self._twin(method):
            self._pass_metrics(
                add, boot, marked_exec, self._l3_exec(ctx, refs["exec_baseline"], level),
                "_baseline", only_delta=True,
            )  # fmt: skip
        if method == "acw":
            self._conditional_preservation(
                add, ctx.store.read_table(refs["watermarked"]), marked_exec,
                self._l3_exec(ctx, refs["exec_baseline"], level),
            )  # fmt: skip
        if self._multibit(method):
            self._capacity(add, boot, applicable)
        self._write(ctx, cell, rows, cfg_hash, thresholds, not_applicable=False)

    # ------------------------------------------------------------------ Livello 3 (ADR-016)
    def _l3_exec(self, ctx: StageContext, ref: ArtifactRef, level: str) -> pd.DataFrame:
        """Esecuzioni per Pass@1: in L3 solo ClassEval (CodeSearchNet e The Stack non hanno test,
        I6), senza le 3 classi di D41; negli altri livelli come ``_read_exec``."""
        table = self._read_exec(ctx, ref)
        if level != "L3":
            return table
        return table[table["dataset"].isin(TESTED_L3)].reset_index(drop=True)

    def _l3_functional(
        self,
        ctx: StageContext,
        cell: Cell,
        refs: dict[str, ArtifactRef],
        add: Callable[..., None],
        boot: Callable[[str], ClusterBootstrap],
        marked_exec: pd.DataFrame,
        reference: str,
    ) -> pd.DataFrame:
        """Validità del codice di CodeSearchNet e The Stack (decisioni dell'utente del 29 e 30
        ottobre 2026): ``tree_sitter_valid`` (principale, tutti i linguaggi), ``syntax_check``
        (``py_compile``/``node --check``, controllo aggiuntivo affidabile) o ``compile_info``
        (``javac``/``g++ -fsyntax-only``, solo informazione), ognuna con baseline e differenza.
        Restituisce le esecuzioni di ClassEval, le sole che entrano in Pass@1."""
        language = str(cell.language)
        untested_marked = marked_exec[~marked_exec["dataset"].isin(TESTED_L3)]
        untested_ref = self._read_exec(ctx, refs[reference])
        untested_ref = untested_ref[~untested_ref["dataset"].isin(TESTED_L3)]
        if not untested_marked.empty:
            reliable = language in ("python", "javascript")
            compile_name = "syntax_check" if reliable else "compile_info"
            for measure, view in (("tree_sitter_valid", _ts_valid), (compile_name, _compiled)):
                self._pass_metrics(
                    add, boot, view(untested_marked), view(untested_ref), "", measure=measure
                )
        return marked_exec[marked_exec["dataset"].isin(TESTED_L3)].reset_index(drop=True)

    def _per_dataset(
        self,
        cell: Cell,
        add: Callable[..., None],
        positives: pd.DataFrame,
        neg_human: pd.DataFrame,
        tau: float,
    ) -> None:
        """``tpr_at_fpr|<dataset>``, ``fpr_human|<dataset>`` e ``auroc|<dataset>`` (I6: il report
        segna i dataset con ``contamination_risk``)."""
        cfg = self._cfg()
        n_boot = cfg.detection.n_bootstrap
        for dataset in sorted(set(positives["dataset"])):
            pos = positives[positives["dataset"] == dataset]
            neg = neg_human[neg_human["dataset"] == dataset]
            table = pos.assign(hit=_detected(pos, tau).astype(float))
            name = f"tpr_at_fpr|{dataset}"
            seed = bootstrap_seed(cfg.global_seed, name, cell.key())
            add(name, float(table["hit"].mean()), ratio_interval(table, "hit", seed, n_boot),
                "cluster_bootstrap", len(table))  # fmt: skip
            if neg.empty:
                continue
            k = int(_detected(neg, tau).sum())
            add(f"fpr_human|{dataset}", k / len(neg), clopper_pearson(k, len(neg)),
                "clopper_pearson", len(neg))  # fmt: skip
            scores = pos.assign(score=_scores(pos, with_embed=True))
            neg_scores = _scores(neg)
            name = f"auroc|{dataset}"
            seed = bootstrap_seed(cfg.global_seed, name, cell.key())
            add(name, auroc(scores["score"].to_numpy(dtype=float), neg_scores),
                auroc_interval(scores, neg_scores, seed, n_boot),
                "cluster_bootstrap+negative_resampling", len(scores) + len(neg_scores))  # fmt: skip

    def _secondary_metrics(
        self,
        cell: Cell,
        add: Callable[..., None],
        positives: pd.DataFrame,
        neg_human: pd.DataFrame,
        thresholds: ThresholdSet,
    ) -> None:
        """TPR e AUROC dei punteggi secondari (varianti del paper), ognuno con la sua soglia."""
        cfg = self._cfg()
        adapter = self.adapter(str(cell.method))
        n_boot = cfg.detection.n_bootstrap
        for name in adapter.secondary_scores:
            sec = thresholds.secondary.get(name)
            if sec is None:
                raise ConfigError(f"{cell.key()}: no threshold for the secondary score {name}")
            sec_pos = with_secondary(positives, adapter, name)
            sec_table = sec_pos.assign(hit=_detected(sec_pos, sec["threshold"]).astype(float))
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

    def _attacked_metrics(
        self,
        ctx: StageContext,
        cell: Cell,
        refs: dict[str, ArtifactRef],
        add: Callable[..., None],
        boot: Callable[[str], ClusterBootstrap],
        tau: float,
        thresholds: ThresholdSet,
    ) -> None:
        """Metriche di una cella con attacco (ADR-012): effetto e intensità dell'attacco."""
        cfg = self._cfg()
        method = str(cell.method)
        adapter = self.adapter(method)
        n_boot = cfg.detection.n_bootstrap
        attacked = primary_view(ctx.store.read_table(refs["attacked_det"]), adapter)
        attacked = attacked[attacked["status"] != "NOT_APPLICABLE"]
        neg_human = primary_view(ctx.store.read_table(refs["neg_human"]), adapter)
        table = attacked.assign(hit=_detected(attacked, tau).astype(float))
        add(
            "tpr_at_fpr",
            float(table["hit"].mean()),
            ratio_interval(
                table, "hit", bootstrap_seed(cfg.global_seed, "tpr_at_fpr", cell.key()), n_boot
            ),
            "cluster_bootstrap",
            len(table),
        )
        pos_scores = attacked.assign(score=_scores(attacked, with_embed=True))
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
        self._secondary_metrics(cell, add, attacked, neg_human, thresholds)
        # --- preservazione funzionale
        attacked_exec = self._read_exec(ctx, refs["exec_attacked"])
        reference = "exec_twin" if self._twin(method) else "exec_baseline"
        self._pass_metrics(add, boot, attacked_exec, self._read_exec(ctx, refs[reference]), "")
        self._pass_metrics(
            add, boot, attacked_exec, self._read_exec(ctx, refs["exec_marked"]), "_vs_marked",
            only_delta=True,
        )  # fmt: skip
        # --- intensità dell'attacco
        samples = ctx.store.read_table(refs["attacked"])
        samples = samples[samples["attack_status"] != "NOT_APPLICABLE"]
        changed = int((samples["attack_status"] == "APPLIED").sum())
        add("changed_fraction", changed / len(samples) if len(samples) else math.nan,
            wilson(changed, len(samples)), "wilson", len(samples))  # fmt: skip
        tool_errors = int((samples["attack_status"] == "TOOL_ERROR").sum())
        add("tool_error_fraction", tool_errors / len(samples) if len(samples) else math.nan,
            wilson(tool_errors, len(samples)), "wilson", len(samples))  # fmt: skip
        per_problem = (
            samples["codebleu_distance"].astype(float).groupby(samples["problem_key"]).mean()
        ).to_numpy(dtype=float)
        add(
            "codebleu_distance",
            float(samples["codebleu_distance"].astype(float).mean()) if len(samples) else math.nan,
            boot("codebleu_distance").mean_interval(per_problem),
            "cluster_bootstrap",
            len(samples),
        )

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
        measure: str = "pass_at_1",
    ) -> None:
        """``<misura>``, ``<misura>_baseline`` e ``delta_<misura>_pp<suffisso>`` (riferimento -
        marcato, accoppiato per problema); ``measure`` = frazione di campioni ``PASSED``."""
        p_marked, per_marked = pass_at_1(marked)
        p_base, per_base = pass_at_1(baseline)
        if not only_delta:
            add(
                measure,
                p_marked,
                boot(measure).mean_interval(per_marked),
                "cluster_bootstrap",
                len(per_marked),
            )
            add(
                f"{measure}_baseline",
                p_base,
                boot(f"{measure}_baseline").mean_interval(per_base),
                "cluster_bootstrap",
                len(per_base),
            )
        # Differenza accoppiata per problema (stessi problemi, in ordine di problem_key).
        m = (marked["status"] == "PASSED").astype(float).groupby(marked["problem_key"]).mean()
        b = (baseline["status"] == "PASSED").astype(float).groupby(baseline["problem_key"]).mean()
        common = m.index.intersection(b.index)
        diff = (b[common].to_numpy(dtype=float) - m[common].to_numpy(dtype=float)) * 100.0
        name = f"delta_{measure}_pp{suffix}"
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

    def _correctness_loop(self, ctx: StageContext, cell: Cell) -> bool | None:
        """Ciclo di correttezza dei campioni marcati, dal manifest (``native_hparams``)."""
        if str(cell.method) != "promptmark":
            return None
        manifest = ctx.store.read_manifest(self._refs(cell)["watermarked"])
        native = ((manifest.extra if manifest else None) or {}).get("native_hparams") or {}
        return bool(native.get("correctness_loop", True))

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
            # PromptMark: ciclo di correttezza attivo nella cella (D27; None per gli altri metodi).
            "correctness_loop": self._correctness_loop(ctx, cell),
            "not_applicable": not_applicable,
            # D41: problemi della cella esclusi da Pass@1 e ΔPass@1, con il motivo
            "pass1_excluded": self.pass1_excluded()
            if (cell.level, cell.language) == ("L3", "python")
            else {},
            # I6: dataset della cella senza test e con rischio di contaminazione (ADR-016)
            "contamination_risk": sorted(CONTAMINATED_L3) if cell.level == "L3" else [],
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
