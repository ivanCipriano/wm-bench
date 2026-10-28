"""Stato e report della Milestone 12 (impercettibilità e capacità, ADR-015) per un metodo congelato.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_m12.py METHOD              # artefatti completi per passo
    python scripts/check_m12.py METHOD --report     # più le metriche per cella

Passi: ``perplexity`` (marcati, riferimenti), ``logreg`` e ``unixcoder`` (un output per
riferimento), ``metrics``. Il report stampa per cella le metriche con IC, i campioni vuoti per parte
con la segnalazione delle celle in cui le due frazioni differiscono di più di 2 punti (decisione
dell'utente del 28 ottobre 2026), l'esito del controllo dei fold e, per MCGMark, le metriche di
capacità della fase ``metrics`` (SPEC §13.5).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from bench.config.builder import load_experiment
from bench.hpo.freeze import FINAL_LEVELS, load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.imperceptibility import (
    ImperceptibilityMetricsStage,
    adversarial_ref,
    imperceptibility_ref,
    references,
)
from bench.pipeline.stages.metrics import metrics_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
CAPACITY = ("message_accuracy", "bit_accuracy", "embedding_success_rate")


def fmt(row: dict[Any, Any]) -> str:
    lo, hi = row.get("ci_low"), row.get("ci_high")
    ci = "" if lo is None or hi is None else f" [{lo:.4f}; {hi:.4f}]"
    return f"  {row['name']:<40} {row['value']:.4f}{ci} n={row['n']} ({row['ci_method']})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=["sweet", "acw", "stone", "promptmark", "mcgmark"])
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    method = args.method
    cfg = load_experiment(["paths=cluster", f"methods=[{method}]"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, method)
    if record is None:
        sys.stdout.write(f"##### {method}: NOT FROZEN\n")
        return 1
    stage = ImperceptibilityMetricsStage(cfg)
    steps: dict[str, list[tuple[str, Any]]] = {
        "perplexity": [],
        "logreg": [],
        "unixcoder": [],
        "metrics": [],
    }
    cells = []
    for frozen in record.cells:
        if frozen.not_applicable or frozen.config_hash is None:
            continue
        cell = Cell(
            method=method,
            model_id=frozen.model_id,
            level=LEVEL,
            language=frozen.language,
            split=SPLIT,
            config_hash=frozen.config_hash,
        )
        name = f"{frozen.model_id} {frozen.language}"
        cells.append((name, cell))
        steps["perplexity"].append((f"{name} marked", stage.ppl(cell, None)))
        for r in references(method):
            steps["perplexity"].append((f"{name} {r}", stage.ppl(cell, r)))
            for variant in ("logreg", "unixcoder"):
                ref = adversarial_ref(
                    variant,
                    method,
                    frozen.model_id,
                    frozen.config_hash,
                    r,
                    LEVEL,
                    frozen.language,
                    SPLIT,
                )
                steps[variant].append((f"{name} {r}", ref))
        steps["metrics"].append(
            (
                name,
                imperceptibility_ref(
                    method, frozen.model_id, frozen.config_hash, LEVEL, frozen.language, SPLIT
                ),
            )
        )
    sys.stdout.write(f"##### {method} M12 on {LEVEL} {SPLIT}\n")
    for step, refs in steps.items():
        missing = [n for n, r in refs if not store.exists(r)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        sys.stdout.write(f"{step:<12} {len(refs) - len(missing):>4}/{len(refs):<4} {state}\n")
        for n in missing:
            sys.stdout.write(f"    missing: {n}\n")
    if args.report:
        flagged: list[str] = []
        for (name, cell), (_, ref) in zip(cells, steps["metrics"], strict=True):
            sys.stdout.write(f"\n##### {method} {name} (config {cell.config_hash})\n")
            if not store.exists(ref):
                sys.stdout.write("  metrics: MISSING\n")
                continue
            flagged += write_cell(store, method, name, cell, ref)
        sys.stdout.write(f"\nempty-fraction gaps > 2 points: {flagged or 'none'}\n")
    return 0


def write_cell(store: ArtifactStore, method: str, name: str, cell: Cell, ref: Any) -> list[str]:
    """Metriche, vuoti per parte (con le segnalazioni), fold e capacità di una cella."""
    flagged = []
    for row in store.read_table(ref).to_dict(orient="records"):
        sys.stdout.write(fmt(row) + "\n")
    manifest = store.read_manifest(ref)
    gaps = (manifest.extra or {}).get("empty_gaps", {}) if manifest else {}
    for key, gap in gaps.items():
        mark = "  <-- FLAG: > 2 punti" if gap.get("flag") else ""
        sys.stdout.write(f"  {key}: {gap.get('gap', 0):.4f}{mark}\n")
        if gap.get("flag"):
            flagged.append(f"{name} {key}")
    model, lang, h = str(cell.model_id), str(cell.language), str(cell.config_hash)
    for variant in ("logreg", "unixcoder"):
        for r in references(method):
            adv = adversarial_ref(variant, method, model, h, r, LEVEL, lang, SPLIT)
            m = store.read_manifest(adv) if store.exists(adv) else None
            if m is not None:
                extra = m.extra or {}
                sys.stdout.write(
                    f"  {variant} vs {r}: folds disjoint by problem "
                    f"{extra.get('folds_disjoint_by_problem')}, fold AUROC "
                    f"{[round(a, 3) for a in extra.get('fold_auroc', [])]}\n"
                )
    if method == "mcgmark":
        cap = metrics_ref(method, model, h, LEVEL, lang, SPLIT, list(FINAL_LEVELS))
        if store.exists(cap):
            for row in store.read_table(cap).to_dict(orient="records"):
                if row["name"] in CAPACITY:
                    sys.stdout.write(fmt(row) + "\n")
    return flagged


if __name__ == "__main__":
    raise SystemExit(main())
