"""Report finale (M14, SPEC §22): tabelle, figure ed efficienza dai soli artefatti.

Un comando (``scripts/report.py``) rigenera tutto in ``<out>/``:

- ``tables/metrics_long.csv``: tutte le metriche (metodo, modello, livello, linguaggio, attacco,
  parametri, nome, valore, intervallo, n, metodo dell'intervallo), con la colonna
  ``contamination_risk`` (I6) e le deviazioni che si applicano (``deviations``);
- ``tables/main_<livello>.{csv,tex}``: rilevabilità e preservazione funzionale delle celle senza
  attacco (LaTeX con ``booktabs``); in L3 Pass@1 solo su ClassEval e, per CodeSearchNet e The
  Stack, la validità tree-sitter (I6: niente conclusioni sulla correttezza funzionale);
- ``tables/main_L3_by_dataset.{csv,tex}``: rilevabilità di L3 per dataset (ClassEval, CodeSearchNet,
  The Stack), con il rischio di contaminazione (I6);
- ``tables/imperceptibility_L1.{csv,tex}`` e ``figures/imperceptibility_adversarial_L1``:
  impercettibilità della M12 (CodeBLEU, ΔNLL, ΔPPL, AUROC dei classificatori avversari);
- ``tables/attacks_L1.{csv,tex}``: TPR e AUROC per attacco e parametri, con l'intensità
  (``changed_fraction``, ``codebleu_distance``);
- ``figures/roc_<metodo>_<modello>_<livello>_<linguaggio>.{pdf,png}``: curve ROC (positivi contro
  negativi umani, punteggio principale del metodo come nelle metriche);
- ``figures/tpr_vs_intensity_<attacco>.{pdf,png}`` e ``figures/k_curve_<livello>.{pdf,png}``;
- ``tables/efficiency.{csv,tex}``: secondi per campione di generazione e rilevazione dai worker,
  per tipo di GPU (manifest), con la nota su MCGMark (patch 0003, speed-up 3,11× sull'oracle);
  una riga per campione (l'ultima: i tentativi ripetuti dopo una ripresa non si contano due volte);
- ``README.md``: indice, esclusioni I6 e note sulle deviazioni
  (``configs/report/annotations.yaml``).

**Metodi post-hoc** (``mcss``, ``multimark``; P6, ADR-019), solo con ``methods`` che li comprende
(``scripts/report.py --with-posthoc``): più configurazioni congelate per (modello, linguaggio),
distinte dalla colonna ``label`` (``prudent``, ``high_capacity``; vuota per i cinque metodi) in
tutte le tabelle e figure; attacchi anche T1.6 e T2.5; tabelle ``attacks_L2`` e ``attacks_L3``
(insieme S, T1.6, T2.4k, T2.5 su L2; ClassEval su L3). Senza i post-hoc (default) tabelle, figure
e file scritti sono quelli di prima, byte per byte.

**Opzioni della tesi per i cinque metodi** (28 novembre 2026; ``scripts/report.py --with-p7a``,
``--with-capacity``), spente per default (report di prima, byte per byte):

- ``p7a`` (P7.a, ADR-018 §5): T1.6 e T2.5 di ACW, STONE e MCGMark sul test di L1 e L2 entrano in
  ``tables/metrics_long.csv`` e nella tabella separata ``tables/attacks_P7a.{csv,tex}``; le altre
  tabelle e le figure restano senza questi attacchi. Non si combina con i post-hoc (che
  raccolgono già T1.6 e T2.5 per tutti i metodi);
- ``capacity``: nelle tabelle degli attacchi (``attacks_L1`` e ``attacks_P7a``) le colonne
  ``message_accuracy``, ``bit_accuracy`` e ``message_accuracy_detected``, valorizzate solo per
  MCGMark; curva capacità–lunghezza di MCGMark sul test senza attacco (L1, L2, L3):
  ``figures/capacity_by_sites_mcgmark_<livello>`` (fasce a giri di 24 posizioni, 1–23, 24–47,
  48–95, 96+) e ``figures/capacity_by_loc_mcgmark_<livello>`` (fasce di ``loc_to_generate``, solo
  dove il dato c'è), con le tabelle ``tables/capacity_by_sites_mcgmark`` e
  ``tables/capacity_by_loc_mcgmark``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from bench.attacks.base import attack_grid, grid_attacks
from bench.hpo.freeze import FINAL_LEVELS, load_freeze
from bench.pipeline.stages.calibrate import primary_view
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.imperceptibility import imperceptibility_ref
from bench.pipeline.stages.metrics import CONTAMINATED_L3, _scores, metrics_ref
from bench.store.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)

METHODS = ("sweet", "acw", "stone", "promptmark", "mcgmark")
POSTHOC_METHODS = ("mcss", "multimark")  # P6: solo con methods che li comprende
POSTHOC_ATTACKS = ("T1.6", "T2.5")  # attacchi girati solo per i post-hoc (P6)
LEVELS = ("L1", "L2", "L3")
ATTACKS = (
    "T1.1",
    "T1.2",
    "T1.3",
    "T1.4",
    "T1.5",
    "T2.1",
    "T2.2",
    "T2.4",
    "T2.4k",
    "T3.1",
    "T4.1",
    "T4.2",
)
MAIN_METRICS = (
    "tpr_at_fpr",
    "auroc",
    "fpr_human",
    "fpr_llm",
    "pass_at_1",
    "delta_pass_at_1_pp",
    "tree_sitter_valid",
    "delta_tree_sitter_valid_pp",
)
# Metriche principali della M12 (riferimento principale: baseline; per MCGMark la gemella, D20)
IMPERCEPTIBILITY_METRICS = (
    "codebleu",
    "codebleu_natural",
    "codebleu_minus_natural",
    "delta_nll_mean",
    "delta_ppl_median",
    "adversarial_logreg_auroc_oof",
    "adversarial_unixcoder_auroc_oof",
)
L3_DATASET_METRICS = ("tpr_at_fpr", "auroc", "fpr_human")
ATTACK_METRICS = (
    "tpr_at_fpr",
    "auroc",
    "changed_fraction",
    "codebleu_distance",
    "delta_pass_at_1_pp_vs_marked",
)
# P7.a (ADR-018 §5): T1.6 e T2.5 sul test di L1 e L2, solo python, per i metodi di ivan.
P7A_METHODS = ("acw", "stone", "mcgmark")
P7A_ATTACKS = ("T1.6", "T2.5")
P7A_LEVELS = ("L1", "L2")
# Capacità sotto attacco nelle tabelle degli attacchi e curva capacità–lunghezza (solo MCGMark,
# l'unico multi-bit fra i cinque).
CAPACITY_METHODS = ("mcgmark",)
CAPACITY_METRICS = ("message_accuracy", "bit_accuracy", "message_accuracy_detected")
CAPACITY_PANELS = (("tpr_at_fpr", "TPR at 1% FPR"), ("message_accuracy", "message accuracy"),
                   ("bit_accuracy", "bit accuracy"))  # fmt: skip
NO_GPU_NOTE = "GPU n/d: i manifest delle fasi girate prima della M13 non la registravano."
MCGMARK_NOTE = (
    "MCGMark: tempi con la patch 0003 (solo prestazioni); speed-up della 0003 sull'oracle 3,11x "
    "(568,8 s -> 182,7 s su 9 generazioni, A100 80 GB; audit di MCGMark, Tempi)."
)


@dataclass(frozen=True)
class Annotation:
    """Deviazione da segnalare nelle tabelle (``configs/report/annotations.yaml``)."""

    deviation: str
    note: str
    methods: tuple[str, ...] = ()
    levels: tuple[str, ...] = ()
    attacks: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    scope: str = ""  # "metrics" o "imperceptibility"; vuoto = entrambe

    def applies(
        self, method: str, level: str, attack: str | None, scope: str = "metrics",
        language: str | None = None,
    ) -> bool:  # fmt: skip
        return (
            (not self.methods or method in self.methods)
            and (not self.levels or level in self.levels)
            and (not self.attacks or (attack or "") in self.attacks)
            and (not self.scope or self.scope == scope)
            and (not self.languages or language in self.languages)
        )


def load_annotations(path: Path) -> list[Annotation]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        Annotation(
            deviation=str(a["deviation"]),
            note=str(a["note"]),
            methods=tuple(a.get("methods", ())),
            levels=tuple(a.get("levels", ())),
            attacks=tuple(a.get("attacks", ())),
            languages=tuple(a.get("languages", ())),
            scope=str(a.get("scope", "")),
        )
        for a in data.get("annotations", [])
    ]


# --------------------------------------------------------------------------- raccolta
def labelled(methods: Sequence[str]) -> bool:
    """Report con i post-hoc: colonna ``label`` (più configurazioni per modello e linguaggio)."""
    return any(m in POSTHOC_METHODS for m in methods)


def frozen_configs(
    store: ArtifactStore, methods: Sequence[str] = METHODS
) -> list[tuple[str, str, str, str, str]]:
    """(metodo, modello, linguaggio, hash congelato, etichetta) dei metodi congelati; etichetta
    vuota per i record senza (i cinque metodi)."""
    out = []
    for method in methods:
        record = load_freeze(store, method)
        if record is None:
            continue
        out += [
            (method, c.model_id, c.language, str(c.config_hash), c.label or "")
            for c in record.cells
            if c.config_hash and not c.not_applicable
        ]
    return out


def frozen_cells(
    store: ArtifactStore, methods: Sequence[str] = METHODS
) -> list[tuple[str, str, str, str]]:
    """(metodo, modello, linguaggio, hash congelato) dei metodi congelati."""
    return [(m, model, lang, h) for m, model, lang, h, _ in frozen_configs(store, methods)]


def _keys(frame: pd.DataFrame, *rest: str) -> list[str]:
    """Colonne chiave: ``method``, ``label`` se c'è (report con i post-hoc), poi ``rest``."""
    return ["method", *(["label"] if "label" in frame.columns else []), *rest]


def collect(
    store: ArtifactStore,
    annotations: Sequence[Annotation] = (),
    methods: Sequence[str] = METHODS,
    p7a: bool = False,
) -> pd.DataFrame:
    """Tutte le metriche di test (soglie L1+L2), senza e con attacco, e quelle d'impercettibilità
    (colonna ``source``: ``metrics`` o ``imperceptibility``), in formato lungo; con i post-hoc
    anche la colonna ``label`` e gli attacchi T1.6 e T2.5. Con ``p7a`` (solo senza i post-hoc)
    anche T1.6 e T2.5 di :data:`P7A_METHODS` sui livelli :data:`P7A_LEVELS`."""
    rows: list[dict[str, Any]] = []
    with_label = labelled(methods)
    if p7a and with_label:
        raise ValueError("p7a: not with the post-hoc methods (they already collect T1.6 and T2.5)")
    names = (*ATTACKS, *POSTHOC_ATTACKS) if with_label else ATTACKS
    attacks = {a: grid_attacks(a) for a in names if _has_grid(a)}
    p7a_grids = {a: grid_attacks(a) for a in P7A_ATTACKS if _has_grid(a)} if p7a else {}
    for method, model, lang, h, label in frozen_configs(store, methods):
        for level in LEVELS:
            refs: list[tuple[str | None, dict[str, Any], Any, str]] = [
                (None, {}, metrics_ref(method, model, h, level, lang, "test", FINAL_LEVELS),
                 "metrics"),
                (None, {}, imperceptibility_ref(method, model, h, level, lang, "test"),
                 "imperceptibility"),
            ]  # fmt: skip
            extra = p7a_grids if method in P7A_METHODS and level in P7A_LEVELS else {}
            for attack_id, grid in {**attacks, **extra}.items():
                for params_hash, attack in grid.items():
                    ref = metrics_ref(
                        method,
                        model,
                        h,
                        level,
                        lang,
                        "test",
                        FINAL_LEVELS,
                        (attack_id, params_hash),
                    )
                    refs.append((attack_id, dict(attack.params), ref, "metrics"))
            for attack_name, params, ref, source in refs:
                if not store.exists(ref):
                    continue
                notes = [
                    a.deviation
                    for a in annotations
                    if a.applies(method, level, attack_name, source, lang)
                ]
                for r in store.read_table(ref).to_dict(orient="records"):
                    name = str(r["name"])
                    dataset = name.split("|", 1)[1] if "|" in name else None
                    rows.append(
                        {
                            "source": source,
                            "method": method,
                            **({"label": label} if with_label else {}),
                            "model_id": model,
                            "level": level,
                            "language": lang,
                            "attack": attack_name,
                            "params": json.dumps(params, sort_keys=True),
                            "metric": name,
                            "value": r["value"],
                            "ci_low": r.get("ci_low"),
                            "ci_high": r.get("ci_high"),
                            "n": r.get("n"),
                            "ci_method": r.get("ci_method"),
                            "contamination_risk": dataset in CONTAMINATED_L3,
                            "deviations": ",".join(notes),
                        }
                    )
    return pd.DataFrame(rows)


def _has_grid(attack_id: str) -> bool:
    try:
        attack_grid(attack_id)
    except Exception:  # attacco spento o senza griglia: non entra nel report
        return False
    return True


# --------------------------------------------------------------------------- tabelle
def fmt_value(value: float, low: Any, high: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    text = f"{value:.3f}"
    if low is not None and high is not None and not (pd.isna(low) or pd.isna(high)):
        text += f" [{low:.3f}, {high:.3f}]"
    return text


def _source(long: pd.DataFrame, source: str) -> pd.Series:
    """Righe di una sorgente (``metrics`` o ``imperceptibility``); senza la colonna, tutte
    ``metrics``."""
    if "source" not in long.columns:
        return pd.Series(source == "metrics", index=long.index)
    return long["source"] == source


def main_table(long: pd.DataFrame, level: str) -> pd.DataFrame:
    """Una riga per (metodo, modello, linguaggio), una colonna per metrica (valore e IC)."""
    part = long[(long["level"] == level) & long["attack"].isna() & _source(long, "metrics")]
    return _ci_table(part, MAIN_METRICS)


def _ci_table(
    part: pd.DataFrame, metrics: Sequence[str], extra_index: Sequence[str] = ()
) -> pd.DataFrame:
    """Una riga per (metodo, modello, linguaggio, ...), una colonna per metrica (valore e IC)."""
    part = part[part["metric"].isin(metrics)]
    if part.empty:
        return pd.DataFrame()
    index = _keys(part, "model_id", "language", *extra_index)
    part = part.assign(
        cell=[
            fmt_value(v, lo, hi)
            for v, lo, hi in zip(part["value"], part["ci_low"], part["ci_high"], strict=True)
        ]
    )
    table = part.pivot_table(index=index, columns="metric", values="cell", aggfunc="first")
    cols = [m for m in metrics if m in table.columns]
    devs = part.groupby(index)["deviations"].first()
    return table[cols].assign(deviations=devs).reset_index()


def l3_dataset_table(long: pd.DataFrame) -> pd.DataFrame:
    """Rilevabilità di L3 per dataset (metriche ``<nome>|<dataset>``), con il rischio I6."""
    part = long[(long["level"] == "L3") & long["attack"].isna() & _source(long, "metrics")]
    part = part[part["metric"].str.contains("|", regex=False)]
    if part.empty:
        return pd.DataFrame()
    split = part["metric"].str.split("|", n=1, expand=True)
    part = part.assign(metric=split[0], dataset=split[1])
    table = _ci_table(part, L3_DATASET_METRICS, extra_index=("dataset",))
    return table.assign(contamination_risk=table["dataset"].isin(CONTAMINATED_L3))


def imperceptibility_table(long: pd.DataFrame, level: str = "L1") -> pd.DataFrame:
    """Impercettibilità (M12) per (metodo, modello, linguaggio), valori con IC."""
    part = long[(long["level"] == level) & _source(long, "imperceptibility")]
    return _ci_table(part, IMPERCEPTIBILITY_METRICS)


def attack_table(long: pd.DataFrame, level: str = "L1", capacity: bool = False) -> pd.DataFrame:
    """Metriche delle celle con attacco di un livello; con ``capacity`` anche le colonne
    :data:`CAPACITY_METRICS`, valorizzate solo per :data:`CAPACITY_METHODS`."""
    part = long[(long["level"] == level) & long["attack"].notna() & _source(long, "metrics")]
    return _attack_pivot(part, ["attack", "params", *_keys(part, "model_id", "language")],
                         capacity)  # fmt: skip


def _attack_pivot(part: pd.DataFrame, index: list[str], capacity: bool) -> pd.DataFrame:
    metrics = (*ATTACK_METRICS, *CAPACITY_METRICS) if capacity else ATTACK_METRICS
    part = part[part["metric"].isin(metrics)]
    if capacity:  # capacità solo per MCGMark: per gli altri metodi le righe non si usano
        part = part[~part["metric"].isin(CAPACITY_METRICS) | part["method"].isin(CAPACITY_METHODS)]
    if part.empty:
        return pd.DataFrame()
    table = part.pivot_table(index=index, columns="metric", values="value", aggfunc="first")
    if capacity:  # colonne sempre presenti e in fondo, nell'ordine di CAPACITY_METRICS
        base = [c for c in table.columns if c not in CAPACITY_METRICS]
        table = table.reindex(columns=[*base, *CAPACITY_METRICS])
    return table.reset_index()


def p7a_table(long: pd.DataFrame, capacity: bool = False) -> pd.DataFrame:
    """T1.6 e T2.5 di P7.a (ADR-018 §5) su L1 e L2: una riga per (attacco, parametri, livello,
    metodo, modello, linguaggio)."""
    part = long[long["attack"].isin(P7A_ATTACKS) & long["method"].isin(P7A_METHODS)
                & long["level"].isin(P7A_LEVELS) & _source(long, "metrics")]  # fmt: skip
    return _attack_pivot(part, ["attack", "params", "level", "method", "model_id", "language"],
                         capacity)  # fmt: skip


def to_latex(table: pd.DataFrame, caption: str) -> str:
    """Tabella ``booktabs`` (senza dipendenze oltre pandas)."""
    if table.empty:
        return f"% {caption}: no data\n"
    cols = list(table.columns)

    def esc(x: Any) -> str:
        text = "--" if x is None or (isinstance(x, float) and math.isnan(x)) else str(x)
        return text.replace("_", r"\_").replace("%", r"\%").replace("&", r"\&").replace("#", r"\#")

    lines = [
        r"\begin{table}[ht]",
        r"\centering",
        r"\small",
        rf"\begin{{tabular}}{{{'l' * len(cols)}}}",
        r"\toprule",
        " & ".join(esc(c) for c in cols) + r" \\",
        r"\midrule",
    ]
    for row in table.itertuples(index=False):
        values = [f"{v:.3f}" if isinstance(v, float) and not math.isnan(v) else esc(v) for v in row]
        lines.append(" & ".join(values) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", rf"\caption{{{esc(caption)}}}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def write_table(table: pd.DataFrame, out: Path, name: str, caption: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / f"{name}.csv", index=False)
    (out / f"{name}.tex").write_text(to_latex(table, caption), encoding="utf-8")


# --------------------------------------------------------------------------- figure
def roc_points(pos: Any, neg: Any) -> tuple[Any, Any]:
    """Curva ROC (FPR, TPR) per soglie decrescenti, con gli estremi (0, 0) e (1, 1)."""
    thresholds = np.unique(np.concatenate([pos, neg]))[::-1]
    fpr = np.array([0.0, *[(neg >= t).mean() for t in thresholds], 1.0])
    tpr = np.array([0.0, *[(pos >= t).mean() for t in thresholds], 1.0])
    return fpr, tpr


def _yerr(value: Any, low: Any, high: Any) -> list[list[float]]:
    """Barre d'errore asimmetriche (sotto, sopra) come liste: con una matrice 2xN e N = 1
    matplotlib converte in scalare un array di un elemento (DeprecationWarning di NumPy 1.25,
    errore nelle versioni future)."""
    below = np.nan_to_num(np.asarray(value, dtype=float) - np.asarray(low, dtype=float))
    above = np.nan_to_num(np.asarray(high, dtype=float) - np.asarray(value, dtype=float))
    return [below.tolist(), above.tolist()]


def save_figure(fig: Any, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}", bbox_inches="tight", dpi=150)


def roc_figures(
    store: ArtifactStore, cfg: Any, out: Path, methods: Sequence[str] = METHODS
) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bench.pipeline.stages.watermark import make_adapter

    written = []
    for method, model, lang, h, label in frozen_configs(store, methods):
        adapter = make_adapter(cfg, method)
        tag = f"{method}_{label}" if label else method  # post-hoc: una curva per configurazione
        for level in LEVELS:
            pos_ref = detection_ref(method, model, h, "positives", level, lang, "test")
            neg_ref = detection_ref(method, model, h, "neg_human", level, lang, "test")
            if not (store.exists(pos_ref) and store.exists(neg_ref)):
                continue
            pos = primary_view(store.read_table(pos_ref), adapter)
            pos = pos[pos["status"] != "NOT_APPLICABLE"]
            neg = primary_view(store.read_table(neg_ref), adapter)
            if pos.empty or neg.empty:
                continue
            fpr, tpr = roc_points(_scores(pos, with_embed=True), _scores(neg))
            fig, ax = plt.subplots(figsize=(3.2, 3.2))
            ax.plot(fpr, tpr, lw=1.5)
            ax.plot([0, 1], [0, 1], ls=":", lw=0.8, color="grey")
            ax.axvline(0.01, ls="--", lw=0.8, color="grey")
            title = f"{method} {f'{label} ' if label else ''}{model} {level} {lang}"
            ax.set(xlabel="FPR", ylabel="TPR", title=title)
            name = f"roc_{tag}_{model}_{level}_{lang}"
            save_figure(fig, out, name)
            plt.close(fig)
            written.append(name)
    return written


def line_figures(long: pd.DataFrame, out: Path) -> list[str]:
    """TPR contro intensità (per attacco, L1) e curva in k (T2.4k, L2 e L3)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written = []
    att = long[long["attack"].notna() & _source(long, "metrics")]
    for attack_id, part in att.groupby("attack"):
        wide = part.pivot_table(
            index=[*_keys(part, "model_id", "language"), "level", "params"],
            columns="metric",
            values="value",
            aggfunc="first",
        ).reset_index()
        if attack_id == "T2.4k":
            for level, sub in wide.groupby("level"):
                fig, ax = plt.subplots(figsize=(4, 3))
                for key, s in sub.groupby(_keys(sub, "model_id", "language")):
                    ks = [json.loads(p)["k"] for p in s["params"]]
                    order = np.argsort(ks)
                    ax.plot(
                        np.array(ks)[order],
                        s["tpr_at_fpr"].to_numpy()[order],
                        marker="o",
                        ms=3,
                        label=" ".join(map(str, key)),
                    )
                ax.set(xlabel="k (functions kept)", ylabel="TPR at 1% FPR", title=f"T2.4k {level}")
                ax.legend(fontsize=5)
                name = f"k_curve_{level}"
                save_figure(fig, out, name)
                plt.close(fig)
                written.append(name)
            continue
        if "codebleu_distance" not in wide or "tpr_at_fpr" not in wide:
            continue
        fig, ax = plt.subplots(figsize=(4, 3))
        for key, s in wide.groupby(_keys(wide, "model_id")):
            ax.scatter(s["codebleu_distance"], s["tpr_at_fpr"], s=8, label=" ".join(map(str, key)))
        ax.set(xlabel="1 - CodeBLEU (intensity)", ylabel="TPR at 1% FPR", title=str(attack_id))
        ax.legend(fontsize=5)
        name = f"tpr_vs_intensity_{attack_id}"
        save_figure(fig, out, name)
        plt.close(fig)
        written.append(name)
    return written


def imperceptibility_figure(long: pd.DataFrame, out: Path, level: str = "L1") -> list[str]:
    """AUROC dei due classificatori avversari per cella, con l'IC bootstrap per problema
    (0,5: il codice marcato non si distingue dal riferimento)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = ("adversarial_logreg_auroc_oof", "adversarial_unixcoder_auroc_oof")
    part = long[
        (long["level"] == level) & _source(long, "imperceptibility") & long["metric"].isin(names)
    ]
    if part.empty:
        return []
    axes = _keys(part, "model_id", "language")
    keys = sorted({tuple(r) for r in part[axes].itertuples(index=False)})
    x = np.arange(len(keys))
    fig, ax = plt.subplots(figsize=(max(4.0, 0.35 * len(keys) + 1), 3.2))
    for i, name in enumerate(names):
        sub = part[part["metric"] == name].set_index(axes)
        value = np.array([sub["value"].get(k, np.nan) for k in keys], dtype=float)
        low = np.array([sub["ci_low"].get(k, np.nan) for k in keys], dtype=float)
        high = np.array([sub["ci_high"].get(k, np.nan) for k in keys], dtype=float)
        err = _yerr(value, low, high)
        ax.bar(x + (i - 0.5) * 0.4, value, width=0.4, yerr=err, capsize=1.5,
               label=name.split("_")[1])  # fmt: skip
    ax.axhline(0.5, ls=":", lw=0.8, color="grey")
    ax.set_xticks(x, [" ".join(k) for k in keys], rotation=90, fontsize=5)
    ax.set(ylabel="AUROC (out-of-fold)", ylim=(0.4, 1.0), title=f"Adversarial classifiers, {level}")
    ax.legend(fontsize=6)
    name = f"imperceptibility_adversarial_{level}"
    save_figure(fig, out, name)
    plt.close(fig)
    return [name]


# --------------------------------------------------------------------------- capacità
def capacity_rows(store: ArtifactStore, cfg: Any, methods: Sequence[str] = METHODS) -> pd.DataFrame:
    """Capacità per fascia sul test senza attacco (L1, L2, L3) dei metodi di
    :data:`CAPACITY_METHODS`: per ``n_sites`` con :data:`MCGMARK_SITE_BANDS` (``kind`` =
    ``sites``) e per ``loc_to_generate`` con :data:`LOC_BANDS` (``kind`` = ``loc``), con le
    funzioni di ``scripts/report_capacity.py`` (:mod:`bench.metrics.capacity_bands`). Soglia
    congelata della cella (L1+L2), lunghezza del messaggio dall'adapter."""
    from bench.domain.ids import derive_seed
    from bench.domain.models import ThresholdSet
    from bench.metrics.capacity_bands import LOC_BANDS, MCGMARK_SITE_BANDS, band_rows, cell_table
    from bench.pipeline.stages.calibrate import threshold_ref
    from bench.pipeline.stages.watermark import make_adapter

    rows: list[dict[str, Any]] = []
    for method, model, lang, h, _ in frozen_configs(store, methods):
        if method not in CAPACITY_METHODS:
            continue
        t_ref = threshold_ref(method, model, lang, h, FINAL_LEVELS)
        levels = [
            lv
            for lv in LEVELS
            if store.exists(detection_ref(method, model, h, "positives", lv, lang, "test"))
        ]
        if not levels or not store.exists(t_ref):
            continue
        tau = store.read_model(t_ref, ThresholdSet).threshold
        bits = getattr(make_adapter(cfg, method), "message_bits", None)  # Detector (ADR-017)
        if not isinstance(bits, int) or bits <= 0:
            raise ValueError(f"{method}: multi-bit method without message_bits")
        for level in levels:
            info = {"method": method, "model_id": model, "config_hash": h, "level": level,
                    "language": lang, "split": "test"}  # fmt: skip
            table = cell_table(store, info)
            for kind, column, bands in (("sites", "n_sites", MCGMARK_SITE_BANDS),
                                        ("loc", "loc_to_generate", LOC_BANDS)):  # fmt: skip
                seed = derive_seed(cfg.global_seed, "capacity", method, model, h, level, kind)
                for r in band_rows(table, column, bands, bits, tau, seed):
                    rows.append({"method": method, "model_id": model, "level": level,
                                 "language": lang, "kind": kind, **r})  # fmt: skip
    return pd.DataFrame(rows)


def capacity_figures(rows: pd.DataFrame, out: Path) -> list[str]:
    """Una figura per (metodo, ``kind``, livello): TPR, ``message_accuracy`` e ``bit_accuracy``
    per fascia, una linea per modello, con l'intervallo di :func:`capacity_rows`. La riga dei
    campioni fuori dalle fasce resta solo nella tabella; senza campioni nelle fasce (es.
    ``loc_to_generate`` assente) la figura non si scrive."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if rows.empty:
        return []
    written = []
    inside = rows[~rows["band"].str.contains("=", regex=False)]
    for (method, kind, level), part in inside.groupby(["method", "kind", "level"], sort=True):
        if int(part["n"].sum()) == 0:
            continue
        labels = list(dict.fromkeys(part["band"]))
        x = np.arange(len(labels))
        fig, axes = plt.subplots(1, len(CAPACITY_PANELS), figsize=(9, 2.8), sharey=True)
        for ax, (metric, title) in zip(axes, CAPACITY_PANELS, strict=True):
            for model, sub in part.groupby("model_id", sort=True):
                cur = sub.set_index("band").reindex(labels)
                value = cur[metric].to_numpy(dtype=float)
                low = cur[f"{metric}_low"].to_numpy(dtype=float)
                high = cur[f"{metric}_high"].to_numpy(dtype=float)
                err = _yerr(value, low, high)
                ax.errorbar(x, value, yerr=err, marker="o", ms=3, capsize=2, label=str(model))
            ax.set_xticks(x, labels, fontsize=7)
            ax.set(title=title, ylim=(0.0, 1.0))
            ax.set_xlabel("n_sites" if kind == "sites" else "loc_to_generate")
        axes[0].set_ylabel(f"{method} {level} test")
        axes[0].legend(fontsize=6)
        name = f"capacity_by_{kind}_{method}_{level}"
        save_figure(fig, out, name)
        plt.close(fig)
        written.append(name)
    return written


# --------------------------------------------------------------------------- efficienza
def efficiency(store: ArtifactStore, methods: Sequence[str] = METHODS) -> pd.DataFrame:
    """Secondi per campione dei worker sul test (generazione e rilevazione dei positivi)."""
    rows = []
    runs = store.root / "_runs"
    with_label = labelled(methods)
    for method, model, lang, h, label in frozen_configs(store, methods):
        for level in LEVELS:
            for phase, path in (
                ("generation", runs / "watermark" / method / model / h / f"{level}_{lang}_test"),
                (
                    "detection",
                    runs / "detect" / method / model / h / "positives" / f"{level}_{lang}_test",
                ),
            ):
                results = path / "results.jsonl"
                if not results.is_file():
                    continue
                seconds, n = _elapsed(results)
                gpu = _gpu(store, method, model, h, level, lang, phase)
                rows.append(
                    {
                        "method": method,
                        **({"label": label} if with_label else {}),
                        "model_id": model,
                        "level": level,
                        "language": lang,
                        "phase": phase,
                        "gpu": gpu or "n/d",
                        "samples": n,
                        "seconds_per_sample": seconds / n if n else math.nan,
                    }
                )
    return pd.DataFrame(rows)


def _elapsed(path: Path) -> tuple[float, int]:
    """Secondi totali e campioni dei risultati di un worker, una volta per campione.

    Dopo una ripresa (requeue, rilancio) lo stesso campione può comparire più volte: vale l'ultima
    riga per ``(item_id, sample_index)``.
    """
    last: dict[tuple[Any, Any], float] = {}
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        key = (row.get("item_id", i), row.get("sample_index"))
        last[key] = float(row.get("elapsed_s") or 0.0)
    return sum(last.values()), len(last)


def _gpu(
    store: ArtifactStore, method: str, model: str, h: str, level: str, lang: str, phase: str
) -> str | None:
    from bench.pipeline.stages.watermark import watermarked_ref

    ref = (
        watermarked_ref(method, model, h, level, lang, "test")
        if phase == "generation"
        else detection_ref(method, model, h, "positives", level, lang, "test")
    )
    manifest = store.read_manifest(ref) if store.exists(ref) else None
    hw = ((manifest.extra or {}).get("hardware") or {}) if manifest else {}
    return hw.get("gpu") or hw.get("slurm_partition")


# --------------------------------------------------------------------------- tutto
def build_report(
    store: ArtifactStore,
    cfg: Any,
    out: Path,
    annotations_path: Path,
    methods: Sequence[str] = METHODS,
    p7a: bool = False,
    capacity: bool = False,
) -> list[str]:
    """Rigenera tabelle, figure ed efficienza in ``out``; restituisce i file scritti. Con i
    post-hoc in ``methods`` anche la colonna ``label`` e le tabelle degli attacchi su L2 e L3.
    Con ``p7a`` e ``capacity`` le aggiunte della tesi per i cinque metodi (docstring del modulo);
    senza, il report di prima byte per byte."""
    annotations = load_annotations(annotations_path) if annotations_path.is_file() else []
    with_label = labelled(methods)
    full = collect(store, annotations, methods, p7a=p7a)
    # P7.a solo in metrics_long e in attacks_P7a: tabelle e figure di sempre senza T1.6 e T2.5
    long = full[~full["attack"].isin(P7A_ATTACKS)] if p7a and not full.empty else full
    tables, figures = out / "tables", out / "figures"
    tables.mkdir(parents=True, exist_ok=True)
    written = []
    full.to_csv(tables / "metrics_long.csv", index=False)
    written.append("tables/metrics_long.csv")
    if not long.empty:
        for level in LEVELS:
            write_table(
                main_table(long, level),
                tables,
                f"main_{level}",
                f"Rilevabilita' e preservazione funzionale, {level} test",
            )
            written.append(f"tables/main_{level}")
        write_table(
            l3_dataset_table(long),
            tables,
            "main_L3_by_dataset",
            "Rilevabilita' di L3 test per dataset (contamination_risk: I6)",
        )
        written.append("tables/main_L3_by_dataset")
        write_table(
            imperceptibility_table(long),
            tables,
            "imperceptibility_L1",
            "Impercettibilita' su L1 test (riferimento: baseline; MCGMark: gemella)",
        )
        written.append("tables/imperceptibility_L1")
        write_table(attack_table(long, capacity=capacity), tables, "attacks_L1",
                    "Attacchi su L1 test")  # fmt: skip
        written.append("tables/attacks_L1")
        if p7a:
            write_table(p7a_table(full, capacity), tables, "attacks_P7a",
                        "P7.a: T1.6 e T2.5 su L1 e L2 test (ACW, STONE, MCGMark)")  # fmt: skip
            written.append("tables/attacks_P7a")
        if with_label:  # post-hoc (P6): attacchi anche su L2 (insieme S e altri) e su ClassEval
            for level in ("L2", "L3"):
                write_table(attack_table(long, level), tables, f"attacks_{level}",
                            f"Attacchi su {level} test")  # fmt: skip
                written.append(f"tables/attacks_{level}")
        written += [f"figures/{n}" for n in line_figures(long, figures)]
        written += [f"figures/{n}" for n in imperceptibility_figure(long, figures)]
    written += [f"figures/{n}" for n in roc_figures(store, cfg, figures, methods)]
    if capacity:
        cap = capacity_rows(store, cfg, methods)
        for kind in ("sites", "loc"):
            part = cap[cap["kind"] == kind].drop(columns="kind") if not cap.empty else cap
            name = f"capacity_by_{kind}_mcgmark"
            write_table(part, tables, name,
                        f"MCGMark, capacita' per fascia di {kind}, test senza attacco")  # fmt: skip
            written.append(f"tables/{name}")
        written += [f"figures/{n}" for n in capacity_figures(cap, figures)]
    eff = efficiency(store, methods)
    write_table(eff, tables, "efficiency", "Secondi per campione (worker); " + MCGMARK_NOTE)
    written.append("tables/efficiency")
    _readme(out, written, full, annotations, with_label, p7a, capacity)
    return written


def _readme(
    out: Path,
    written: Iterable[str],
    long: pd.DataFrame,
    annotations: Sequence[Annotation],
    with_label: bool = False,
    p7a: bool = False,
    capacity: bool = False,
) -> None:
    lines = [
        "# Report (M14)",
        "",
        "Rigenerato dai soli artefatti con `python scripts/report.py`.",
        "",
        "## File",
        "",
        *[f"- `{w}`" for w in written],
        "",
        "## Esclusioni I6 (contaminazione)",
        "",
        "- L3, CodeSearchNet e The Stack C++: niente conclusioni sulla correttezza funzionale "
        "(nessun Pass@1); si riportano rilevabilità e validità del codice (tree-sitter, "
        "py_compile/node --check; javac e g++ solo informazione).",
        "",
    ]
    if not long.empty:
        flagged = long[long["contamination_risk"]]
        lines.append(f"- righe di metriche con `contamination_risk`: {len(flagged)}")
    lines += ["", "## Deviazioni annotate", ""]
    lines += [f"- **{a.deviation}**: {a.note}" for a in annotations]
    lines += [
        "",
        "## Efficienza",
        "",
        f"- {MCGMARK_NOTE}",
        f"- {NO_GPU_NOTE}",
        "- Una riga per campione: i tentativi ripetuti dopo una ripresa non si contano due volte.",
        "",
    ]
    if with_label:
        lines += [
            "## Metodi post-hoc (P6)",
            "",
            "- `mcss` e `multimark`: una riga per configurazione congelata, colonna `label` "
            "(`main`, `prudent`, `high_capacity`; vuota per i cinque metodi), soglie L1+L2 di "
            "sviluppo congelate (ADR-019).",
            "- L3 = solo ClassEval per i positivi; l'FPR di L3 sui negativi umani Python di L3 "
            "(anche CodeSearchNet), quello di ClassEval in `fpr_human|classeval`.",
            "",
        ]
    if p7a:
        lines += [
            "## P7.a (ADR-018 §5)",
            "",
            "- T1.6 e T2.5 di ACW, STONE e MCGMark sul test di L1 e L2 (solo python): in "
            "`tables/metrics_long.csv` e in `tables/attacks_P7a`; le altre tabelle e le figure "
            "non li comprendono.",
            "",
        ]
    if capacity:
        lines += [
            "## Capacità di MCGMark",
            "",
            "- Tabelle degli attacchi con `message_accuracy`, `bit_accuracy` e "
            "`message_accuracy_detected`, valorizzate solo per MCGMark (ADR-017).",
            "- Curva capacità–lunghezza sul test senza attacco: fasce di `n_sites` a giri di 24 "
            "posizioni (1–23: nessun giro completo) e di `loc_to_generate`; la riga "
            "`<colonna>=0/none` (fuori dalle fasce) è solo nelle tabelle.",
            "",
        ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
