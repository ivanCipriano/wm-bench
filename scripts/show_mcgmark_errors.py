"""Campioni marcati di MCGMark che non passano, accanto al campione gemello (stesso seme).

Uso (con bench-core):
    python scripts/show_mcgmark_errors.py --model qwen25_coder_7b [--status SYNTAX_ERROR] [--n 5]

Per ogni campione stampa lo stato, la coda di stderr, i siti marcati, il codice marcato e il codice
della baseline gemella con lo stesso problema e indice: serve a capire se il calo di Pass@1 viene
dalle sostituzioni del watermark (Milestone 7).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--model", required=True)
    parser.add_argument("--status", default="SYNTAX_ERROR")
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()
    root = Path(args.artifacts)
    (cfg,) = (p.name for p in (root / "watermarked/mcgmark" / args.model).iterdir())
    name = "L1_python_dev.parquet"
    marked = pd.read_parquet(root / "watermarked/mcgmark" / args.model / cfg / name)
    twin = pd.read_parquet(root / "baseline" / args.model / "twin/mcgmark" / cfg / name)
    exe = pd.read_parquet(root / "execution/llm_watermarked/mcgmark" / args.model / cfg / name)
    df = marked.merge(exe[["sample_id", "status", "stderr_tail"]], on="sample_id")
    df = df[df["status"] == args.status].sort_values(["problem_key", "sample_index"])
    twin_code = twin.set_index(["problem_key", "sample_index"])["code"]
    # Un campione per problema, per vedere casi diversi.
    for row in df.drop_duplicates("problem_key").head(args.n).itertuples():
        other = twin_code.get((row.problem_key, row.sample_index), "<missing>")
        sys.stdout.write(
            f"\n{'=' * 100}\n{row.problem_key} #{row.sample_index}  status={row.status}"
            f"  embed={row.embed_status}  n_sites={row.n_sites}\n"
            f"--- stderr\n{row.stderr_tail}\n--- marked code\n{row.code}\n"
            f"--- twin code (same seed)\n{other}\n"
        )


if __name__ == "__main__":
    main()
