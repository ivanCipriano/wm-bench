"""Report finale (M14, SPEC §22): tabelle, figure ed efficienza dai soli artefatti.

Un comando (``scripts/report.py``) rigenera tutto in ``<out>/``:

- ``tables/metrics_long.csv``: tutte le metriche (metodo, modello, livello, linguaggio, attacco,
  parametri, nome, valore, intervallo, n, metodo dell'intervallo), con la colonna
  ``contamination_risk`` (I6) e le deviazioni che si applicano (``deviations``);
- ``tables/main_<livello>.{csv,tex}``: rilevabilità e preservazione funzionale delle celle senza
  attacco (LaTeX con ``booktabs``); in L3 Pass@1 solo su ClassEval e, per CodeSearchNet e The
  Stack, la validità tree-sitter (I6: niente conclusioni sulla correttezza funzionale);
- ``tables/attacks_L1.{csv,tex}``: TPR e AUROC per attacco e parametri, con l'intensità
  (``changed_fraction``, ``codebleu_distance``);
- ``figures/roc_<metodo>_<modello>_<livello>_<linguaggio>.{pdf,png}``: curve ROC (positivi contro
  negativi umani, punteggio principale del metodo come nelle metriche);
- ``figures/tpr_vs_intensity_<attacco>.{pdf,png}`` e ``figures/k_curve_<livello>.{pdf,png}``;
- ``tables/efficiency.{csv,tex}``: secondi per campione di generazione e rilevazione dai worker,
  per tipo di GPU (manifest), con la nota su MCGMark (patch 0003, speed-up 3,11× sull'oracle);
- ``README.md``: indice, esclusioni I6 e note sulle deviazioni
  (``configs/report/annotations.yaml``).
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
from bench.pipeline.stages.metrics import CONTAMINATED_L3, _scores, metrics_ref
from bench.store.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)

METHODS = ("sweet", "acw", "stone", "promptmark", "mcgmark")
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
ATTACK_METRICS = (
    "tpr_at_fpr",
    "auroc",
    "changed_fraction",
    "codebleu_distance",
    "delta_pass_at_1_pp_vs_marked",
)
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

    def applies(self, method: str, level: str, attack: str | None) -> bool:
        return (
            (not self.methods or method in self.methods)
            and (not self.levels or level in self.levels)
            and (not self.attacks or (attack or "") in self.attacks)
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
        )
        for a in data.get("annotations", [])
    ]


# --------------------------------------------------------------------------- raccolta
def frozen_cells(store: ArtifactStore) -> list[tuple[str, str, str, str]]:
    """(metodo, modello, linguaggio, hash congelato) dei metodi congelati."""
    out = []
    for method in METHODS:
        record = load_freeze(store, method)
        if record is None:
            continue
        out += [
            (method, c.model_id, c.language, str(c.config_hash))
            for c in record.cells
            if c.config_hash and not c.not_applicable
        ]
    return out


def collect(store: ArtifactStore, annotations: Sequence[Annotation] = ()) -> pd.DataFrame:
    """Tutte le metriche di test (soglie L1+L2), senza e con attacco, in formato lungo."""
    rows: list[dict[str, Any]] = []
    attacks = {a: grid_attacks(a) for a in ATTACKS if _has_grid(a)}
    for method, model, lang, h in frozen_cells(store):
        for level in LEVELS:
            refs: list[tuple[str | None, dict[str, Any], Any]] = [
                (None, {}, metrics_ref(method, model, h, level, lang, "test", FINAL_LEVELS))
            ]
            for attack_id, grid in attacks.items():
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
                    refs.append((attack_id, dict(attack.params), ref))
            for attack_name, params, ref in refs:
                if not store.exists(ref):
                    continue
                notes = [a.deviation for a in annotations if a.applies(method, level, attack_name)]
                for r in store.read_table(ref).to_dict(orient="records"):
                    name = str(r["name"])
                    dataset = name.split("|", 1)[1] if "|" in name else None
                    rows.append(
                        {
                            "method": method,
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


def main_table(long: pd.DataFrame, level: str) -> pd.DataFrame:
    """Una riga per (metodo, modello, linguaggio), una colonna per metrica (valore e IC)."""
    part = long[(long["level"] == level) & long["attack"].isna()]
    part = part[part["metric"].isin(MAIN_METRICS)]
    if part.empty:
        return pd.DataFrame()
    part = part.assign(
        cell=[
            fmt_value(v, lo, hi)
            for v, lo, hi in zip(part["value"], part["ci_low"], part["ci_high"], strict=True)
        ]
    )
    table = part.pivot_table(
        index=["method", "model_id", "language"], columns="metric", values="cell", aggfunc="first"
    )
    cols = [m for m in MAIN_METRICS if m in table.columns]
    devs = part.groupby(["method", "model_id", "language"])["deviations"].first()
    return table[cols].assign(deviations=devs).reset_index()


def attack_table(long: pd.DataFrame, level: str = "L1") -> pd.DataFrame:
    part = long[(long["level"] == level) & long["attack"].notna()]
    part = part[part["metric"].isin(ATTACK_METRICS)]
    if part.empty:
        return pd.DataFrame()
    table = part.pivot_table(
        index=["attack", "params", "method", "model_id", "language"],
        columns="metric",
        values="value",
        aggfunc="first",
    )
    return table.reset_index()


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


def save_figure(fig: Any, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}", bbox_inches="tight", dpi=150)


def roc_figures(store: ArtifactStore, cfg: Any, out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bench.pipeline.stages.watermark import make_adapter

    written = []
    for method, model, lang, h in frozen_cells(store):
        adapter = make_adapter(cfg, method)
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
            ax.set(xlabel="FPR", ylabel="TPR", title=f"{method} {model} {level} {lang}")
            name = f"roc_{method}_{model}_{level}_{lang}"
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
    att = long[long["attack"].notna()]
    for attack_id, part in att.groupby("attack"):
        wide = part.pivot_table(
            index=["method", "model_id", "language", "level", "params"],
            columns="metric",
            values="value",
            aggfunc="first",
        ).reset_index()
        if attack_id == "T2.4k":
            for level, sub in wide.groupby("level"):
                fig, ax = plt.subplots(figsize=(4, 3))
                for key, s in sub.groupby(["method", "model_id", "language"]):
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
        for key, s in wide.groupby(["method", "model_id"]):
            ax.scatter(s["codebleu_distance"], s["tpr_at_fpr"], s=8, label=" ".join(map(str, key)))
        ax.set(xlabel="1 - CodeBLEU (intensity)", ylabel="TPR at 1% FPR", title=str(attack_id))
        ax.legend(fontsize=5)
        name = f"tpr_vs_intensity_{attack_id}"
        save_figure(fig, out, name)
        plt.close(fig)
        written.append(name)
    return written


# --------------------------------------------------------------------------- efficienza
def efficiency(store: ArtifactStore) -> pd.DataFrame:
    """Secondi per campione dei worker sul test (generazione e rilevazione dei positivi)."""
    rows = []
    runs = store.root / "_runs"
    for method, model, lang, h in frozen_cells(store):
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
                        "model_id": model,
                        "level": level,
                        "language": lang,
                        "phase": phase,
                        "gpu": gpu,
                        "samples": n,
                        "seconds_per_sample": seconds / n if n else math.nan,
                    }
                )
    return pd.DataFrame(rows)


def _elapsed(path: Path) -> tuple[float, int]:
    seconds, n = 0.0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            seconds += float(json.loads(line).get("elapsed_s") or 0.0)
            n += 1
    return seconds, n


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
def build_report(store: ArtifactStore, cfg: Any, out: Path, annotations_path: Path) -> list[str]:
    """Rigenera tabelle, figure ed efficienza in ``out``; restituisce i file scritti."""
    annotations = load_annotations(annotations_path) if annotations_path.is_file() else []
    long = collect(store, annotations)
    tables, figures = out / "tables", out / "figures"
    tables.mkdir(parents=True, exist_ok=True)
    written = []
    long.to_csv(tables / "metrics_long.csv", index=False)
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
        write_table(attack_table(long), tables, "attacks_L1", "Attacchi su L1 test")
        written.append("tables/attacks_L1")
        written += [f"figures/{n}" for n in line_figures(long, figures)]
    written += [f"figures/{n}" for n in roc_figures(store, cfg, figures)]
    eff = efficiency(store)
    write_table(eff, tables, "efficiency", "Secondi per campione (worker); " + MCGMARK_NOTE)
    written.append("tables/efficiency")
    _readme(out, written, long, annotations)
    return written


def _readme(
    out: Path, written: Iterable[str], long: pd.DataFrame, annotations: Sequence[Annotation]
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
    lines += ["", "## Efficienza", "", f"- {MCGMARK_NOTE}", ""]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
