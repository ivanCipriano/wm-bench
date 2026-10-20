"""Riepilogo dell'HPO (Milestone 8): configurazione scelta e candidati migliori per cella.

Uso (con bench-core):
    python scripts/report_hpo.py [--artifacts DIR] [--method M] [--top 5] [--final]
                                 [--curve PARAM]

Per ogni (metodo, modello, linguaggio) stampa la configurazione scelta dalla fase ``tune``
(iperparametri, TPR, Pass@1, ΔPass@1, vincolo rispettato o no, provvisorietà) e i migliori
candidati per TPR; segnala i casi senza configurazione ammissibile (SPEC §14.4).

Con ``--final`` legge la scelta definitiva su L1+L2 (``hpo/selected_L1L2``, M9) e aggiunge, per la
configurazione scelta, l'FPR di sviluppo per parte (nativi L1, nativi L2, integrazione) e, per
PromptMark, se il ciclo di correttezza era attivo su ciascun livello (D27).

Con ``--curve PARAM`` (es. ``delta`` per SWEET, D31) stampa per ogni cella la curva del TPR e di
ΔPass@1 al variare di ``PARAM``, con gli altri iperparametri fissi a quelli della configurazione
scelta; ``BORDO`` segnala una scelta sul valore più alto della curva, e la crescita del TPR fra
gli ultimi due valori è quella della regola di estensione (D31).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--method", default=None)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--final", action="store_true", help="scelta definitiva su L1+L2")
    parser.add_argument("--curve", default=None, help="iperparametro della curva (es. delta)")
    args = parser.parse_args()
    artifacts = Path(args.artifacts)
    root = artifacts / "hpo"
    suffix = "_L1L2" if args.final else ""
    pattern = f"{args.method or '*'}/*/*.json"
    # Il pattern prende anche i manifest (``<lang>.json.manifest.json``): si escludono.
    selected_dir, candidates_dir = root / f"selected{suffix}", root / f"candidates{suffix}"
    paths = sorted(p for p in selected_dir.glob(pattern) if not p.name.endswith(".manifest.json"))
    if not paths:
        sys.stdout.write(f"no selections under {selected_dir} for {pattern}\n")
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
        if args.final:
            _final_details(artifacts, sel)
        table_path = candidates_dir / path.relative_to(selected_dir)
        table_path = table_path.with_suffix(".parquet")
        if table_path.is_file():
            table = pd.read_parquet(table_path).sort_values(
                ["tpr_dev", "pass1_drop_pp"], ascending=[False, True]
            )
            if args.curve:
                _curve(table, sel, args.curve)
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


def _curve(table: pd.DataFrame, sel: dict[str, Any], param: str) -> None:
    """TPR e ΔPass@1 al variare di ``param``, gli altri iperparametri come nella scelta."""
    fixed = {k: v for k, v in sel["hparams"].items() if k != param}
    rows = []
    for record in table.to_dict(orient="records"):
        hp = json.loads(str(record["hparams_json"]))
        if param in hp and {k: v for k, v in hp.items() if k != param} == fixed:
            rows.append(
                (float(hp[param]), float(record["tpr_dev"]), float(record["pass1_drop_pp"]))
            )
    rows.sort()
    if not rows:
        sys.stdout.write(f"  curva di {param}: nessun punto\n")
        return
    chosen = float(sel["hparams"].get(param, float("nan")))
    border = chosen == rows[-1][0] and len(rows) > 1
    sys.stdout.write(
        f"  curva di {param} con {json.dumps(fixed, sort_keys=True)}"
        f"{'  BORDO' if border else ''}\n"
    )
    for value, tpr, drop in rows:
        mark = "*" if value == chosen else " "
        sys.stdout.write(f"    {mark} {param} {value:g}  TPR {tpr:.4f}  ΔPass@1 {drop:6.2f} pp\n")
    # Regola di estensione (D31): crescita del TPR fra il valore scelto e quello precedente.
    index = next((i for i, r in enumerate(rows) if r[0] == chosen), None)
    if index:
        before, at = rows[index - 1], rows[index]
        sys.stdout.write(
            f"    crescita del TPR fra {param} {before[0]:g} e {at[0]:g} (scelto): "
            f"{(at[1] - before[1]) * 100:+.1f} punti\n"
        )


def _manifest_extra(path: Path) -> dict[str, Any]:
    manifest = path.with_name(path.name + ".manifest.json")
    if not manifest.is_file():
        return {}
    return dict(json.loads(manifest.read_text(encoding="utf-8")).get("extra") or {})


def _final_details(artifacts: Path, sel: dict[str, Any]) -> None:
    """FPR di sviluppo per parte della soglia scelta e ciclo di PromptMark per livello."""
    method, model, lang, cfg = sel["method"], sel["model_id"], sel["language"], sel["config_hash"]
    threshold = artifacts / "thresholds" / method / model / lang / "L1L2" / f"{cfg}.json"
    parts = _manifest_extra(threshold).get("fpr_dev_by_part") or {}
    text = "  ".join(
        f"{name} {p['fpr']:.4f} ({p['false_positives']}/{p['n_negatives']})"
        for name, p in parts.items()
        if p.get("fpr") is not None
    )
    sys.stdout.write(f"  FPR dev per parte: {text or 'n/d'}\n")
    if method == "promptmark":
        loops = []
        for level in sel.get("levels") or []:
            path = artifacts / "watermarked" / method / model / cfg / f"{level}_{lang}_dev.parquet"
            native = _manifest_extra(path).get("native_hparams") or {}
            loops.append(f"{level} {'on' if native.get('correctness_loop', True) else 'off'}")
        sys.stdout.write(f"  ciclo di correttezza (D27): {', '.join(loops)}\n")


if __name__ == "__main__":
    main()
