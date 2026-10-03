"""Riepilogo della baseline dagli artefatti (Milestone 3): conteggi, estrazione, decoding.

Uso (con bench-core):
    python scripts/report_baseline.py [--artifacts DIR] [--level L1] [--examples 2]

Per ogni cella stampa righe e righe attese, tasso di estrazione (complessivo e per dataset),
quota di output con un blocco di codice mai chiuso (probabile troncamento a max_new_tokens),
campioni distinti per problema e secondi per problema. Controlla che decoding e prompt siano
uguali in tutte le celle e mostra alcuni esempi di estrazione fallita.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
NEUTRAL = {"do_sample": True, "top_k": 0, "repetition_penalty": 1.0, "no_repeat_ngram_size": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--level", default="L1")
    parser.add_argument(
        "--examples", type=int, default=2, help="failed extractions shown per model/language"
    )
    ns = parser.parse_args()

    root = Path(ns.artifacts) / "baseline"
    files = sorted(root.glob(f"*/{ns.level}_*.parquet"))
    if not files:
        sys.stdout.write(f"no baseline artifacts under {root}\n")
        return 1

    sys.stdout.write(
        f"{'cell':44s} {'rows':>5s} {'exp':>5s} {'extract':>8s} {'open```':>8s} "
        f"{'distinct':>8s} {'s/prob':>7s}  by_dataset\n"
    )
    gen_configs: dict[str, set[str]] = defaultdict(set)
    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    failures: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    problems = 0
    for path in files:
        manifest = json.loads(Path(f"{path}.manifest.json").read_text(encoding="utf-8"))
        extra = manifest["extra"]
        df = pd.read_parquet(path)
        model = path.parent.name
        _, lang, split = path.stem.split("_", 2)
        cell = f"{model}/{lang}/{split}"
        problems += df["problem_key"].nunique()
        open_fence = df["raw_output"].fillna("").map(lambda t: t.count("```") % 2 == 1).mean()
        distinct = df.groupby("problem_key")["code"].nunique().mean()
        by_ds = {k: round(v, 3) for k, v in extra["extraction_rate_by_dataset"].items()}
        expected = manifest["n_rows_expected"]
        rate = extra["extraction_rate"]
        seconds = extra["seconds_per_problem"]["mean"]
        sys.stdout.write(
            f"{cell:44s} {len(df):5d} {expected:5d} {rate:8.4f} "
            f"{open_fence:8.4f} {distinct:8.2f} {seconds:7.2f}  {by_ds}\n"
        )
        gen = extra.get("generation", {}).get("generation_config", {})
        key = {k: gen.get(k) for k in (*NEUTRAL, "temperature", "top_p", "max_new_tokens")}
        gen_configs[json.dumps(key, sort_keys=True)].add(cell)
        gen_configs["system_prompt=" + str(extra.get("system_prompt_sha256"))].add(cell)
        agg[(model, lang)][0] += int(df["extraction_ok"].sum())
        agg[(model, lang)][1] += len(df)
        for row in df[~df["extraction_ok"]].head(ns.examples).itertuples(index=False):
            if len(failures[(model, lang)]) < ns.examples:
                failures[(model, lang)].append(
                    {
                        "problem_key": row.problem_key,
                        "sample_index": row.sample_index,
                        "raw_output": str(row.raw_output)[:400],
                    }
                )

    total_ok = sum(v[0] for v in agg.values())
    total = sum(v[1] for v in agg.values())
    sys.stdout.write(
        f"\ncells: {len(files)}, problems: {problems}, samples: {total}, "
        f"extraction rate: {total_ok / total:.4f}\n"
    )
    sys.stdout.write("\nextraction rate per model and language (dev+test):\n")
    for (model, lang), (ok, n) in sorted(agg.items()):
        sys.stdout.write(f"  {model:22s} {lang:11s} {ok / n:.4f}  ({n - ok} failed of {n})\n")

    sys.stdout.write("\ndecoding and system prompt (one line per distinct value):\n")
    for value, cells in sorted(gen_configs.items()):
        sys.stdout.write(f"  {len(cells):2d} cells: {value}\n")
    bad = [
        v
        for v in gen_configs
        if v.startswith("{") and any(json.loads(v).get(k) != want for k, want in NEUTRAL.items())
    ]
    sys.stdout.write(f"  neutral decoding everywhere: {'NO' if bad else 'yes'}\n")

    sys.stdout.write("\nexamples of failed extraction (first 400 characters):\n")
    for (model, lang), rows in sorted(failures.items()):
        for row in rows:
            sys.stdout.write(f"--- {model} {lang} {row['problem_key']} #{row['sample_index']}\n")
            sys.stdout.write(f"{row['raw_output']!r}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
