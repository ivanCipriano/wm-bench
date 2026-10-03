"""Mostra in modo leggibile i campioni della baseline (o di un'altra origine) di una cella.

Uso (con bench-core):
    python scripts/show_samples.py --model qwen25_coder_7b --lang python --split dev --n 2
    python scripts/show_samples.py --model deepseek_coder_6p7b --lang java --problem humaneval/0
    python scripts/show_samples.py --model qwen25_coder_7b --lang cpp --split test \\
        --export-jsonl /tmp/qwen_cpp_test.jsonl

Per ogni problema stampa il prompt del dataset e, per ogni campione, la risposta completa del
modello (``raw_output``) e il codice estratto (``code``). Con ``--export-jsonl`` scrive invece
un file JSONL (una riga per campione, con il prompt) da copiare sul PC.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--source", default="baseline", help="artifact folder (default baseline)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--lang", required=True, choices=["python", "java", "cpp", "javascript"])
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--level", default="L1")
    parser.add_argument("--problem", help="only this problem_key (e.g. humaneval/0)")
    parser.add_argument("--n", type=int, default=1, help="number of problems to show")
    parser.add_argument("--no-raw", action="store_true", help="show only the extracted code")
    parser.add_argument("--export-jsonl", type=Path, help="write all samples of the cell here")
    ns = parser.parse_args()

    root = Path(ns.artifacts)
    path = root / ns.source / ns.model / f"{ns.level}_{ns.lang}_{ns.split}.parquet"
    if not path.is_file():
        sys.stderr.write(f"not found: {path}\n")
        return 1
    df = pd.read_parquet(path)
    problems_path = root / "data" / "problems" / f"{ns.level}_{ns.lang}.parquet"
    prompts: dict[str, str] = {}
    if problems_path.is_file():
        probs = pd.read_parquet(problems_path, columns=["problem_key", "prompt_text"])
        prompts = dict(zip(probs["problem_key"], probs["prompt_text"], strict=True))

    if ns.export_jsonl is not None:
        ns.export_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with ns.export_jsonl.open("w", encoding="utf-8") as fh:
            for row in df.sort_values(["problem_key", "sample_index"]).itertuples(index=False):
                record = {
                    "problem_key": row.problem_key,
                    "sample_index": int(row.sample_index),
                    "extraction_ok": bool(row.extraction_ok),
                    "prompt": prompts.get(row.problem_key),
                    "raw_output": row.raw_output,
                    "code": row.code,
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        sys.stdout.write(f"{len(df)} samples written to {ns.export_jsonl}\n")
        return 0

    keys = sorted(df["problem_key"].unique())
    if ns.problem:
        keys = [k for k in keys if k == ns.problem]
        if not keys:
            sys.stderr.write(f"problem {ns.problem} not in {path.name}\n")
            return 1
    for key in keys[: max(ns.n, 1)]:
        bar = "=" * 100
        sys.stdout.write(f"{bar}\nPROBLEM {key}\n{bar}\n")
        if key in prompts:
            sys.stdout.write(f"--- dataset prompt ---\n{prompts[key]}\n")
        rows = df[df["problem_key"] == key].sort_values("sample_index")
        for row in rows.itertuples(index=False):
            sys.stdout.write(
                f"\n##### sample {row.sample_index} (extraction_ok={row.extraction_ok})\n"
            )
            if not ns.no_raw:
                sys.stdout.write(f"--- raw_output ---\n{row.raw_output}\n")
            sys.stdout.write(f"--- code ---\n{row.code}\n")
    sys.stdout.write(f"\n{len(keys)} problem(s) in the cell, {len(df)} samples\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
