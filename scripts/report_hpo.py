"""Riepilogo dell'HPO (Milestone 8): configurazione scelta e candidati migliori per cella.

Uso (con bench-core):
    python scripts/report_hpo.py [--artifacts DIR] [--method M] [--top 5]

Per ogni (metodo, modello, linguaggio) stampa la configurazione scelta dalla fase ``tune``
(iperparametri, TPR, Pass@1, ΔPass@1, vincolo rispettato o no, provvisorietà) e i migliori
candidati per TPR; segnala i casi senza configurazione ammissibile (SPEC §14.4).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--method", default=None)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()
    root = Path(args.artifacts) / "hpo"
    pattern = f"{args.method or '*'}/*/*.json"
    # Il pattern prende anche i manifest (``<lang>.json.manifest.json``): si escludono.
    paths = sorted(
        p for p in (root / "selected").glob(pattern) if not p.name.endswith(".manifest.json")
    )
    if not paths:
        sys.stdout.write(f"no selections under {root / 'selected'} for {pattern}\n")
        return
    flagged = []
    for path in paths:
        sel = json.loads(path.read_text(encoding="utf-8"))
        key = f"{sel['method']} {sel['model_id']} {sel['language']}"
        sys.stdout.write(f"\n##### {key}\n")
        if sel.get("not_applicable"):
            sys.stdout.write("NOT_APPLICABLE\n")
            continue
        flags = [f for f in ("provisional",) if sel.get(f)]
        if not sel.get("constraint_satisfied"):
            flags.append("CONSTRAINT NOT SATISFIED")
            flagged.append(key)
        sys.stdout.write(
            f"selected {sel['config_hash']} {json.dumps(sel['hparams'], sort_keys=True)}\n"
            f"  TPR {sel['tpr_dev']:.4f}  Pass@1 {sel['pass1_dev']:.4f} "
            f"(baseline {sel['pass1_baseline_dev']:.4f})  ΔPass@1 {sel['pass1_drop_pp']:.2f} pp"
            f"  admissible {sel['n_admissible']}/{sel['n_candidates']}"
            f"  max {sel['max_pass1_drop_pp']} pp  levels {sel['levels']}  {' '.join(flags)}\n"
        )
        table_path = root / "candidates" / path.relative_to(root / "selected")
        table_path = table_path.with_suffix(".parquet")
        if table_path.is_file():
            table = pd.read_parquet(table_path).sort_values(
                ["tpr_dev", "pass1_drop_pp"], ascending=[False, True]
            )
            for row in table.head(args.top).itertuples():
                mark = "*" if row.selected else " "
                ok = "ok" if row.admissible else "--"
                sys.stdout.write(
                    f"  {mark} {ok} {row.config_hash} {row.hparams_json}  TPR {row.tpr_dev:.4f}"
                    f"  ΔPass@1 {row.pass1_drop_pp:6.2f} pp  τ {row.threshold:.3f}\n"
                )
    if flagged:
        sys.stdout.write("\n##### senza configurazione ammissibile (da segnalare)\n")
        sys.stdout.write("".join(f"{k}\n" for k in flagged))


if __name__ == "__main__":
    main()
