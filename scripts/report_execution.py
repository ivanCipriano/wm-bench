"""Riepilogo dell'esecuzione dei test (Milestone 4): Pass@1 con IC, stati, canoniche fallite.

Uso (con bench-core):
    python scripts/report_execution.py [--artifacts DIR] [--level L1] [--resamples 1000]

Per le canoniche stampa la quota che passa e, per ogni canonica fallita, lo stato e la coda di
stderr. Per la baseline stampa, per modello e linguaggio (dev, test e dev+test), Pass@1
(stimatore non distorto con N = 6: media dei c/n per problema) con IC al 95% da bootstrap per
problema (SPEC §13.6) e la distribuzione degli stati; infine lo stato dei campioni con un blocco
di codice mai chiuso (probabile troncamento a max_new_tokens, cfr. report_baseline.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from bench.metrics.uncertainty import ClusterBootstrap, bootstrap_seed, pass_at_1

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
GLOBAL_SEED = 20261001
STATUSES = [
    "PASSED",
    "FAILED",
    "SYNTAX_ERROR",
    "COMPILE_ERROR",
    "RUNTIME_ERROR",
    "TIMEOUT",
    "EXTRACTION_FAILED",
    "SANDBOX_ERROR",
]
SHORT = {
    "PASSED": "pass",
    "FAILED": "fail",
    "SYNTAX_ERROR": "syn",
    "COMPILE_ERROR": "comp",
    "RUNTIME_ERROR": "run",
    "TIMEOUT": "tout",
    "EXTRACTION_FAILED": "extr",
    "SANDBOX_ERROR": "sbox",
}


def _manifest(path: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(Path(f"{path}.manifest.json").read_text(encoding="utf-8"))


def _statuses(df: pd.DataFrame) -> str:
    counts = df["status"].value_counts()
    return " ".join(f"{SHORT[s]}={counts.get(s, 0)}" for s in STATUSES if counts.get(s, 0))


def canonical_report(root: Path, level: str) -> None:
    sys.stdout.write("##### canonical solutions (expected: all PASSED or documented)\n")
    for path in sorted((root / "execution" / "canonical").glob(f"{level}_*.parquet")):
        df = pd.read_parquet(path)
        lang = path.stem.split("_", 1)[1]
        ok = (df["status"] == "PASSED").mean()
        image = _manifest(path).get("sandbox_image_hash") or ""
        sys.stdout.write(
            f"  {lang:11s} {ok:7.2%} passed of {len(df):4d}  {_statuses(df)}  image {image[:12]}\n"
        )
        if df["sample_index"].notna().any():
            reps = df.groupby("problem_key")["sample_index"].nunique().max()
            always = df.groupby("problem_key")["status"].apply(lambda s: (s == "PASSED").all())
            sys.stdout.write(
                f"    {reps} run(s) per problem; passed in every run: {always.mean():.2%}\n"
            )
        if "first_attempt_status" in df:
            first = df.assign(status=df["first_attempt_status"])
            sys.stdout.write(
                f"    first attempt: {_statuses(first)}; "
                f"retried after TIMEOUT: {df['retry_status'].notna().sum()}\n"
            )
        for row in df[df["status"] != "PASSED"].itertuples(index=False):
            tail = (row.stderr_tail or "").replace("\n", " | ")[-300:]
            sys.stdout.write(f"    {row.problem_key:16s} {row.status:15s} {tail}\n")


def baseline_report(root: Path, level: str, resamples: int) -> None:
    sys.stdout.write("\n##### baseline Pass@1 (95% CI, bootstrap per problem)\n")
    base = root / "execution" / "llm_baseline"
    frames = []
    for path in sorted(base.glob(f"*/{level}_*.parquet")):
        df = pd.read_parquet(path)
        _, lang, split = path.stem.split("_", 2)
        df["model"], df["language"], df["split"] = path.parent.name, lang, split
        frames.append(df)
    if not frames:
        sys.stdout.write("  no baseline execution artifacts\n")
        return
    # Colonne tutte vuote (es. n_tests di HumanEvalPack) escluse: evita l'avviso di pandas.
    allx = pd.concat([f.dropna(axis=1, how="all") for f in frames], ignore_index=True)
    sys.stdout.write(
        f"  {'model':22s} {'language':11s} {'split':8s} {'problems':>8s} {'pass@1':>7s}  "
        f"{'95% CI':17s} statuses\n"
    )
    for (model, lang), group in allx.groupby(["model", "language"], sort=True):
        for split, part in [
            ("dev", group[group["split"] == "dev"]),
            ("test", group[group["split"] == "test"]),
            ("dev+test", group),
        ]:
            if part.empty:
                continue
            value, per_problem = pass_at_1(part)
            seed = bootstrap_seed(
                GLOBAL_SEED, "pass_at_1", f"llm_baseline/{model}/{lang}/{level}/{split}"
            )
            low, high = ClusterBootstrap(seed, resamples).mean_interval(per_problem)
            sys.stdout.write(
                f"  {model:22s} {lang:11s} {split:8s} {len(per_problem):8d} {value:7.4f}  "
                f"[{low:.4f}, {high:.4f}]  {_statuses(part)}\n"
            )
    sys.stdout.write(
        f"\n  total records: {len(allx)}; "
        f"sandbox errors: {(allx['status'] == 'SANDBOX_ERROR').sum()}\n"
    )

    # Ripetizione dei TIMEOUT (ADR-007): quanti campioni recuperati, per modello e linguaggio.
    if "retry_status" in allx:
        sys.stdout.write("\n##### retry of TIMEOUT samples (alone, once; ADR-007)\n")
        sys.stdout.write(
            f"  {'model':22s} {'language':11s} {'timeout 1st':>11s} {'retried':>8s} "
            f"{'->PASSED':>9s} {'still TIMEOUT':>13s}\n"
        )
        for (model, lang), group in allx.groupby(["model", "language"], sort=True):
            retried = group[group["retry_status"].notna()]
            first_timeout = int((group["first_attempt_status"] == "TIMEOUT").sum())
            recovered = int((retried["retry_status"] == "PASSED").sum())
            still = int((retried["retry_status"] == "TIMEOUT").sum())
            sys.stdout.write(
                f"  {model:22s} {lang:11s} {first_timeout:11d} {len(retried):8d} "
                f"{recovered:9d} {still:13d}\n"
            )

    # Campioni probabilmente troncati: stato dopo l'esecuzione.
    trunc = []
    for path in sorted((root / "baseline").glob(f"*/{level}_*.parquet")):
        samples = pd.read_parquet(path, columns=["sample_id", "raw_output"])
        open_fence = samples[
            samples["raw_output"].fillna("").map(lambda t: t.count("```") % 2 == 1)
        ]
        trunc.append(open_fence[["sample_id"]])
    if trunc:
        ids = pd.concat(trunc)["sample_id"]
        sub = allx[allx["sample_id"].isin(set(ids))]
        sys.stdout.write(f"  samples with an unclosed code block: {len(sub)}  {_statuses(sub)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--level", default="L1")
    parser.add_argument("--resamples", type=int, default=1000)
    ns = parser.parse_args()
    root = Path(ns.artifacts)
    canonical_report(root, ns.level)
    baseline_report(root, ns.level, ns.resamples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
