"""Verifica della griglia di PromptMark ricostruita (Milestone 8). Job GPU su gpuq, circa 30-40 min.

Uso (con bench-core, su un nodo NVIDIA):
    python scripts/verify_promptmark_replay.py [--level L1|L2] [--artifacts DIR] [--out DIR]
                                               [--problems 10] [--samples 2]

Idea: per una dimensione della green list si esegue **una** traiettoria con 5 iterazioni e soglia
irraggiungibile (η = z 1e6, p < 0 mai vero) e si ricava a posteriori il risultato di ogni
combinazione (iterazioni massime k, soglia η), fermando la traiettoria dove ``run_phase1`` si
sarebbe fermato (``exp_iterative_wm.py:207-284``):

- stop alla prima iterazione t < k con codice corretto e ``p_exact < norm.sf(η)``;
- altrimenti la candidata ``best``: la prima iterazione corretta, se c'è, se no la prima.

Prima condizione (codice): il feedback dell'iterazione t dipende solo dall'esito dell'iterazione
t-1 (errore degli esempi, oppure p-value e conteggi se il codice è corretto ma non marcato) e i
semi da ``derive_seed(seme, "promptmark-iter", t)``: nessuno dei due dipende da η o da k, salvo
la decisione di fermarsi. Seconda condizione (questo script): su campioni reali, due configurazioni
eseguite davvero (A: 3 iterazioni, η = 2,12; B: 5 iterazioni, η = 1,64) devono coincidere
carattere per carattere con quelle ricostruite (risposta scelta, iterazione scelta, numero di
iterazioni e tutte le risposte intermedie). Per esercitare anche lo stop, si scelgono i problemi
con il p-value più basso fra le iterazioni corrette della M7 (Qwen, default).

Con ``--level L2`` (D27, M9) la verifica si ripete su CodeNet, dove il ciclo di correttezza è
spento (``test_list`` vuota): ogni iterazione è corretta, quindi lo stop dipende solo da p e η. I
problemi sono quelli con il p-value più basso fra le iterazioni del default di L2 o di una
traiettoria di L2, se ci sono già; altrimenti i primi problemi dev in ordine di chiave.

Tutto fuori dagli artefatti del benchmark (``--out``, default
``$WMB/tmp/promptmark_replay[_L2]``). Exit code 1 se una sola differenza.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from bench.methods.adapters.promptmark import replay_selection

WMB = os.environ.get("WMB", "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench")
MODEL = "qwen25_coder_7b"
UNREACHABLE_Z = 1e6  # norm.sf(1e6) = 0.0: p_exact < 0 non è mai vero
CONFIGS: dict[str, tuple[int, float]] = {"A": (3, 2.12), "B": (5, 1.64)}  # (iter_cap, η come z)
TRAJECTORY = {"iter_cap": 5, "z_threshold": UNREACHABLE_Z}


# Stessa funzione usata dal framework per i candidati ricostruiti (ADR-009).
replay = replay_selection


def compare(
    trajectory: dict[str, Any], actual: dict[str, Any], iter_cap: int, z: float
) -> list[str]:
    """Differenze fra la configurazione eseguita e quella ricostruita (lista vuota = uguali)."""
    its = trajectory["iterations"]
    chosen, steps = replay(its, iter_cap, z)
    diffs = []
    if actual["n_iterations"] != steps:
        diffs.append(f"n_iterations {actual['n_iterations']} != {steps}")
    if actual["selected_iteration"] != chosen:
        diffs.append(f"selected_iteration {actual['selected_iteration']} != {chosen}")
    if actual["raw_output"] != its[chosen]["response"]:
        diffs.append("raw_output differs")
    for t, (a, b) in enumerate(zip(actual["iterations"], its[:steps], strict=False)):
        if a["response"] != b["response"]:
            diffs.append(f"iteration {t} response differs")
    return diffs


def _results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def run_results(artifacts: Path, cfg_hash: str, level: str) -> Path:
    """``results.jsonl`` del worker di una configurazione (Qwen) su un livello, parte dev."""
    base = artifacts / "_runs" / "watermark" / "promptmark" / MODEL
    return base / cfg_hash / f"{level}_python_dev" / "results.jsonl"


def pick_problems(artifacts: Path, n: int, sources: list[Path], dev_keys: list[str]) -> list[str]:
    """Problemi con il p-value più basso fra le iterazioni corrette dei risultati ``sources``
    (il primo file esistente); senza risultati, i primi ``n`` problemi dev in ordine di chiave."""
    rows = next((_results(p) for p in sources if p.is_file()), [])
    if not rows:
        return sorted(dev_keys)[:n]
    best: dict[str, float] = {}
    for row in rows:
        problem = str(row["item_id"]).rpartition("#")[0]
        ps = [
            float(it["p_exact"])
            for it in (row.get("extra") or {}).get("iterations", [])
            if it.get("correctness")
        ]
        if ps:
            best[problem] = min(best.get(problem, 1.0), *ps)
    return sorted(best, key=lambda k: (best[k], k))[:n]


def run_config(diag: Any, problems: list[Any], hp: dict[str, Any], name: str, n: int) -> Any:
    run = diag["adapter"].embed_from_prompts(
        problems,
        hp,
        diag["model"],
        diag["key_id"],
        diag["decoding"],
        n,
        diag["prompts"],
        diag["out"] / name,
    )
    out = {}
    for r in run.worker.results:
        extra = r.extra or {}
        out[r.item_id] = {
            "raw_output": r.raw_output,
            "n_iterations": extra.get("n_iterations"),
            "selected_iteration": extra.get("selected_iteration"),
            "iterations": extra.get("iterations", []),
            "status": r.status,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--level", default="L1", choices=["L1", "L2"])
    parser.add_argument("--artifacts", default=f"{WMB}/artifacts")
    parser.add_argument("--out", default=None)
    parser.add_argument("--problems", type=int, default=10)
    parser.add_argument("--samples", type=int, default=2)
    args = parser.parse_args()

    import pandas as pd

    from bench.config.builder import load_experiment
    from bench.domain.models import Problem
    from bench.generation.decoding import neutral_settings
    from bench.generation.prompt_builder import PromptBuilder
    from bench.pipeline.stages.generate_baseline import decoding_for
    from bench.pipeline.stages.watermark import KEY_ID, make_adapter

    artifacts = Path(args.artifacts)
    level = args.level
    out = args.out or f"{WMB}/tmp/promptmark_replay" + ("" if level == "L1" else f"_{level}")
    cfg = load_experiment(["paths=cluster", "stage=watermark"])
    model = cfg.models_catalog[MODEL]
    adapter = make_adapter(cfg, "promptmark")
    default = adapter.default_hparams()
    diag = {
        "adapter": adapter,
        "model": model,
        "key_id": KEY_ID,
        "decoding": {**neutral_settings(decoding_for(cfg, level)), "torch_dtype": model.dtype},
        "prompts": PromptBuilder.from_config(cfg.prompt),
        "out": Path(out),
    }
    default_hash = adapter.config_hash(default)
    table = pd.read_parquet(artifacts / "data" / "problems" / f"{level}_python.parquet")
    table = table[table["split"] == "dev"]
    trajectories = sorted(
        (artifacts / "_runs" / "watermark" / "promptmark" / MODEL).glob(
            f"*/{level}_python_dev/results.jsonl"
        )
    )
    sources = [run_results(artifacts, default_hash, level), *trajectories]
    keys = pick_problems(artifacts, args.problems, sources, list(table["problem_key"]))
    rows = table[table["problem_key"].isin(keys)].to_dict(orient="records")
    problems = sorted((Problem.model_validate(r) for r in rows), key=lambda p: p.problem_key)
    sys.stdout.write(f"problems ({len(problems)}): {[p.problem_key for p in problems]}\n")

    trajectory = run_config(diag, problems, {**default, **TRAJECTORY}, "trajectory", args.samples)
    failures = 0
    for name, (cap, z) in CONFIGS.items():
        hp = {"iter_cap": cap, "z_threshold": z}
        actual = run_config(diag, problems, {**default, **hp}, name, args.samples)
        stopped = identical = 0
        for item_id in sorted(actual):
            traj = trajectory[item_id]
            diffs = compare(traj, actual[item_id], cap, z)
            _, steps = replay(traj["iterations"], cap, z)
            stopped += int(steps < cap)
            if diffs:
                failures += 1
                sys.stdout.write(f"  MISMATCH {name} {item_id}: {'; '.join(diffs)}\n")
            else:
                identical += 1
        sys.stdout.write(
            f"config {name} {hp}: {len(actual)} samples, {stopped} stopped early, "
            f"{identical} identical to the replay\n"
        )
    failures += extra_checks(
        trajectory, run_results(artifacts, default_hash, level), default, level
    )
    sys.stdout.write("RESULT: " + ("OK" if failures == 0 else f"{failures} mismatches") + "\n")
    return 0 if failures == 0 else 1


def extra_checks(
    trajectory: dict[str, Any], default_results: Path, default: dict[str, Any], level: str
) -> int:
    """Controlli in più; restituisce le differenze che contano come errori.

    - Su L2 il ciclo di correttezza è spento (D27): ogni iterazione deve risultare corretta.
    - Il default ricostruito contro gli artefatti del livello (altro nodo: solo informativo).
    """
    failures = 0
    if level == "L2":
        wrong = [
            item_id
            for item_id, traj in trajectory.items()
            if not all(it["correctness"] for it in traj["iterations"])
        ]
        sys.stdout.write(f"L2: samples with an incorrect iteration: {len(wrong)} (expected 0)\n")
        failures += len(wrong)
    stored = {str(r["item_id"]): r for r in _results(default_results)}
    same = total = 0
    for item_id, traj in trajectory.items():
        if item_id in stored:
            chosen, _ = replay(traj["iterations"], default["iter_cap"], default["z_threshold"])
            total += 1
            same += int(stored[item_id].get("raw_output") == traj["iterations"][chosen]["response"])
    sys.stdout.write(
        f"default replay vs {level} artifacts (informative): {same}/{total} identical\n"
    )
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
