"""Verifiche sul punteggio di STONE (Milestone 8, prima dell'HPO): composizione e AUROC per fonte.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/diag_stone_score.py [--artifacts DIR]

Lo z ufficiale (``stone.py:228``) è ``(G_ns − γ·N_syn) / sqrt(γ(1−γ)·N_syn)``: verdi contati
fra i token non sintattici, ma T = token sintattici. Sotto l'ipotesi nulla (ogni token non
sintattico verde con probabilità γ) vale ``E[z] = γ(N_ns − N_syn) / sqrt(γ(1−γ)·N_syn)``,
diverso da 0: lo z ufficiale misura anche la composizione del codice.

Lo script legge le rilevazioni della M7 (``detection/stone/...``; in ``extra_json``:
``n_syntax``, ``n_nonsyntax``, ``n_green_nonsyntax``, ``z_nonsyntax``) e stampa:

(a) per soggetto e fonte: mediane di N_syn, N_ns, N_ns/N_syn, frazione di verdi fra i non
    sintattici, z ufficiale, E[z] sotto l'ipotesi nulla;
(b) AUROC (positivi contro ciascun gruppo di negativi) di: z ufficiale, ``z_nonsyntax``, sola
    composizione (E[z] nullo) e residuo ``z − E[z]`` = ``(G_ns − γ·N_ns) / sqrt(γ(1−γ)·N_syn)``.
    I negativi umani si dividono per dataset: nativi (soluzioni canoniche, il confronto degli
    autori in ``run.py``) e integrazione (CodeSearchNet, The Stack).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
MODELS = ["qwen25_coder_7b", "deepseek_coder_6p7b"]
LANGS = ["python", "java", "cpp"]
INTEGRATION = {"codesearchnet", "thestack_cpp"}
SCORES = ["z_official", "z_nonsyntax", "z_null", "z_residual"]
MIN_GROUP = 5


def null_z(n_syntax: float, n_nonsyntax: float, gamma: float) -> float:
    """E[z ufficiale] sotto l'ipotesi nulla: γ(N_ns − N_syn) / sqrt(γ(1−γ)·N_syn)."""
    if n_syntax < 1:
        return math.nan
    return gamma * (n_nonsyntax - n_syntax) / math.sqrt(gamma * (1 - gamma) * n_syntax)


def residual_z(n_green: float, n_syntax: float, n_nonsyntax: float, gamma: float) -> float:
    """z ufficiale meno il suo valore atteso nullo: (G_ns − γ·N_ns) / sqrt(γ(1−γ)·N_syn)."""
    if n_syntax < 1:
        return math.nan
    return (n_green - gamma * n_nonsyntax) / math.sqrt(gamma * (1 - gamma) * n_syntax)


def expand(df: pd.DataFrame, gamma: float) -> pd.DataFrame:
    """Colonne dei conteggi e dei punteggi derivati da ``extra_json``."""
    extra = [json.loads(x) if isinstance(x, str) else {} for x in df["extra_json"]]
    out = df.copy()
    out["n_syntax"] = [float(e.get("n_syntax") or 0) for e in extra]
    out["n_nonsyntax"] = [float(e.get("n_nonsyntax") or 0) for e in extra]
    out["n_green"] = [float(e.get("n_green_nonsyntax") or 0) for e in extra]
    out["z_official"] = pd.to_numeric(out["score"], errors="coerce")
    out["z_nonsyntax"] = [
        float(e["z_nonsyntax"]) if e.get("z_nonsyntax") is not None else math.nan for e in extra
    ]
    out["z_null"] = [
        null_z(s, n, gamma) for s, n in zip(out["n_syntax"], out["n_nonsyntax"], strict=True)
    ]
    out["z_residual"] = [
        residual_z(g, s, n, gamma)
        for g, s, n in zip(out["n_green"], out["n_syntax"], out["n_nonsyntax"], strict=True)
    ]
    out["ratio"] = out["n_nonsyntax"] / out["n_syntax"].replace(0, np.nan)
    out["green_frac"] = out["n_green"] / out["n_nonsyntax"].replace(0, np.nan)
    return out[out["status"] == "OK"]


def auroc(pos: pd.Series, neg: pd.Series) -> float:
    """AUROC (positivi = 1) senza valori mancanti; NaN se un gruppo è troppo piccolo."""
    p, n = pos.dropna().to_numpy(), neg.dropna().to_numpy()
    if len(p) < MIN_GROUP or len(n) < MIN_GROUP:
        return math.nan
    labels = np.concatenate([np.ones(len(p)), np.zeros(len(n))])
    return float(roc_auc_score(labels, np.concatenate([p, n])))


def _cfg(root: Path, model: str) -> str | None:
    folder = root / "detection" / "stone" / model
    hashes = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
    return hashes[0] if len(hashes) == 1 else None


def _gamma(root: Path, model: str, cfg: str) -> float:
    """γ della configurazione dal manifest dei campioni marcati (default 0,5)."""
    for path in (root / "watermarked" / "stone" / model / cfg).glob("*.manifest.json"):
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        hp = data.get("hparams") or (data.get("extra") or {}).get("hparams") or {}
        if "gamma" in hp:
            return float(hp["gamma"])
    return 0.5


def groups(root: Path, model: str, lang: str) -> dict[str, pd.DataFrame]:
    """Positivi, negativi umani (tutti, nativi per dataset, integrazione) e negativi LLM."""
    cfg = _cfg(root, model)
    if cfg is None:
        return {}
    gamma = _gamma(root, model, cfg)
    base = root / "detection" / "stone" / model / cfg
    name = f"L1_{lang}_dev.parquet"
    frames = {s: base / s / name for s in ("positives", "neg_human", "neg_llm")}
    if not all(p.is_file() for p in frames.values()):
        return {}
    pos = expand(pd.read_parquet(frames["positives"]), gamma)
    human = expand(pd.read_parquet(frames["neg_human"]), gamma)
    llm = expand(pd.read_parquet(frames["neg_llm"]), gamma)
    is_integration = human["dataset"].str.lower().isin(INTEGRATION)
    out = {"positives": pos, "neg_llm": llm, "human_all": human}
    out["human_integration"] = human[is_integration]
    out["human_native"] = human[~is_integration]
    for dataset in sorted(human.loc[~is_integration, "dataset"].unique()):
        out[f"human_native:{dataset}"] = human[human["dataset"] == dataset]
    return out


def report(root: Path) -> None:
    for model in MODELS:
        for lang in LANGS:
            g = groups(root, model, lang)
            if not g:
                sys.stdout.write(f"\n##### {model} {lang}: detection artifacts missing\n")
                continue
            sys.stdout.write(f"\n##### {model} {lang}\n(a) composizione (mediane)\n")
            sys.stdout.write(
                f"  {'gruppo':<34} {'n':>5} {'N_syn':>6} {'N_ns':>6} {'ns/syn':>7} "
                f"{'verdi_ns':>8} {'z_uff':>7} {'E[z]0':>7} {'z_ns':>6}\n"
            )
            for label, df in g.items():
                med = df[["n_syntax", "n_nonsyntax", "ratio", "green_frac"]].median()
                sys.stdout.write(
                    f"  {label:<34} {len(df):>5} {med['n_syntax']:>6.0f} {med['n_nonsyntax']:>6.0f}"
                    f" {med['ratio']:>7.2f} {med['green_frac']:>8.3f}"
                    f" {df['z_official'].median():>7.2f} {df['z_null'].median():>7.2f}"
                    f" {df['z_nonsyntax'].median():>6.2f}\n"
                )
            sys.stdout.write("(b) AUROC positivi contro\n")
            sys.stdout.write(f"  {'negativi':<34} " + " ".join(f"{s:>11}" for s in SCORES) + "\n")
            pos = g["positives"]
            for label, df in g.items():
                if label == "positives":
                    continue
                if label.startswith("human_native:"):
                    dataset = label.split(":", 1)[1]
                    p = pos[pos["dataset"] == dataset]
                    shown = f"{label} (pos {dataset})"
                else:
                    p, shown = pos, label
                values = " ".join(f"{auroc(p[s], df[s]):>11.3f}" for s in SCORES)
                sys.stdout.write(f"  {shown:<34} {values}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    args = parser.parse_args()
    report(Path(args.artifacts))


if __name__ == "__main__":
    main()
