"""Prova dose-risposta di STONE prima dell'HPO (Milestone 8). Job GPU su gpuq, circa 20 min.

Uso (con bench-core, su un nodo NVIDIA):
    python scripts/diag_stone_dose.py [--artifacts DIR] [--out DIR] [--problems 20]
                                      [--deltas 1 3 10]

20 prompt di L1 Python (sviluppo, i primi per chiave), Qwen, γ = 0,5 e δ ∈ {1, 3, 10}, con la catena
del framework (adapter, worker e shim di STONE). Per ogni δ si generano i campioni marcati (6 per
prompt, stessi semi della baseline) e si confrontano con la baseline LLM degli stessi prompt:

- frazione di token verdi fra i non sintattici (aggregata e mediana per campione), marcato contro
  baseline: se non sale chiaramente con δ c'è un problema di integrazione;
- AUROC marcato contro baseline con ``z_nonsyntax`` (principale, D16) e con lo z ufficiale.

La rilevazione dipende solo da γ e dalla chiave, non da δ: la baseline si punteggia una volta.
Tutto fuori dagli artefatti del benchmark (``--out``, default ``$WMB/tmp/diag_stone_dose``).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

WMB = os.environ.get("WMB", "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench")
MODEL = "qwen25_coder_7b"
GAMMA = 0.5
MIN_GROUP = 5


def summarize(results: list[Any]) -> dict[str, Any]:
    """Frazione di verdi (aggregata e mediana) e punteggi dai risultati di rilevazione."""
    green = ns = 0.0
    fractions, z_ns, z_off = [], [], []
    for r in results:
        extra = r.extra or {}
        n = float(extra.get("n_nonsyntax") or 0)
        g = float(extra.get("n_green_nonsyntax") or 0)
        if r.status != "OK" or n < 1:
            continue
        green += g
        ns += n
        fractions.append(g / n)
        z_ns.append(
            float(extra["z_nonsyntax"]) if extra.get("z_nonsyntax") is not None else math.nan
        )
        z_off.append(float(r.score) if r.score is not None else math.nan)
    return {
        "n": len(fractions),
        "green_pooled": green / ns if ns else math.nan,
        "green_median": float(np.median(fractions)) if fractions else math.nan,
        "z_ns": z_ns,
        "z_off": z_off,
    }


def auroc(pos: list[float], neg: list[float]) -> float:
    p = [x for x in pos if not math.isnan(x)]
    n = [x for x in neg if not math.isnan(x)]
    if len(p) < MIN_GROUP or len(n) < MIN_GROUP:
        return math.nan
    labels = np.concatenate([np.ones(len(p)), np.zeros(len(n))])
    return float(roc_auc_score(labels, np.array(p + n)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=f"{WMB}/artifacts")
    parser.add_argument("--out", default=f"{WMB}/tmp/diag_stone_dose")
    parser.add_argument("--problems", type=int, default=20)
    parser.add_argument("--deltas", type=float, nargs="+", default=[1.0, 3.0, 10.0])
    args = parser.parse_args()

    from bench.config.builder import load_experiment
    from bench.domain.models import Problem
    from bench.generation.decoding import neutral_settings
    from bench.generation.prompt_builder import PromptBuilder
    from bench.methods.base import DetectInput
    from bench.pipeline.stages.generate_baseline import decoding_for
    from bench.pipeline.stages.watermark import KEY_ID, make_adapter

    root, out = Path(args.artifacts), Path(args.out)
    cfg = load_experiment(["paths=cluster", "stage=watermark"])
    model = cfg.models_catalog[MODEL]
    adapter: Any = make_adapter(cfg, "stone")
    level_decoding = decoding_for(cfg, "L1")
    decoding = {**neutral_settings(level_decoding), "torch_dtype": model.dtype}
    prompts = PromptBuilder.from_config(cfg.prompt)

    table = pd.read_parquet(root / "data" / "problems" / "L1_python.parquet")
    table = table[table["split"] == "dev"].sort_values("problem_key").head(args.problems)
    problems = [Problem.model_validate(r) for r in table.to_dict(orient="records")]
    keys = {p.problem_key for p in problems}

    def detect(name: str, codes: list[tuple[str, str]]) -> dict[str, Any]:
        inputs = [DetectInput(item_id=i, language="python", code=c) for i, c in codes]
        hp = {"gamma": GAMMA, "delta": 1.0}  # δ non entra nella rilevazione
        results = adapter.detect_codes(inputs, hp, model, KEY_ID, decoding, out / name, "cuda:0")
        return summarize(results)

    baseline = pd.read_parquet(root / "baseline" / MODEL / "L1_python_dev.parquet")
    baseline = baseline[baseline["problem_key"].isin(keys) & (baseline["code"].str.strip() != "")]
    base = detect("baseline", list(zip(baseline["sample_id"], baseline["code"], strict=True)))
    sys.stdout.write(
        f"problems: {len(problems)}  baseline samples scored: {base['n']}\n"
        f"baseline: green_pooled {base['green_pooled']:.3f}  green_median "
        f"{base['green_median']:.3f}\n\n"
        f"{'delta':>6} {'n':>4} {'green_pooled':>12} {'green_median':>12} "
        f"{'AUROC_z_ns':>10} {'AUROC_z_uff':>11}\n"
    )
    for delta in args.deltas:
        run = adapter.embed_from_prompts(
            problems,
            {"gamma": GAMMA, "delta": delta},
            model,
            KEY_ID,
            decoding,
            level_decoding.n,
            prompts,
            out / f"embed_delta_{delta:g}",
        )
        marked = [(s.sample_id, s.code) for s in run.samples if s.code.strip()]
        res = detect(f"marked_delta_{delta:g}", marked)
        a_ns = auroc(res["z_ns"], base["z_ns"])
        a_off = auroc(res["z_off"], base["z_off"])
        sys.stdout.write(
            f"{delta:>6g} {res['n']:>4} {res['green_pooled']:>12.3f} {res['green_median']:>12.3f} "
            f"{a_ns:>10.3f} {a_off:>11.3f}\n"
        )


if __name__ == "__main__":
    main()
