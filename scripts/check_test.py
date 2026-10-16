"""Stato della fase 0 della Milestone 10 di un metodo: L1 test con la configurazione congelata.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_test.py METHOD [--missing]

Legge ``freeze/<metodo>.json`` (ADR-013) e, per ogni passo di ``submit_test_l1.sh`` (gemella di
MCGMark, watermark, execute, detect, metrics), conta gli artefatti **completi** della parte di test
di L1, una cella per (modello, linguaggio) con la configurazione congelata. Il primo passo
``INCOMPLETE`` è il prossimo da lanciare; un job sparito da ``squeue`` ha davvero finito solo se il
suo passo risulta ``COMPLETE`` qui.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Any

from bench.config.builder import load_experiment
from bench.hpo.freeze import FINAL_LEVELS, load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.metrics import metrics_ref
from bench.pipeline.stages.watermark import baseline_twin_ref, make_adapter, watermarked_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
SUBJECTS = ("positives", "neg_human", "neg_llm")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=["sweet", "acw", "stone", "promptmark", "mcgmark"])
    parser.add_argument("--missing", action="store_true")
    args = parser.parse_args()
    method = args.method
    cfg = load_experiment(["paths=cluster", "stage=watermark"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, method)
    if record is None:
        sys.stdout.write(
            f"##### {method}: NOT FROZEN, run `python scripts/freeze_method.py {method}` first\n"
        )
        return 1
    adapter: Any = make_adapter(cfg, method)
    twin = bool(getattr(adapter, "twin_baseline", False))
    subjects = [*SUBJECTS, *(["neg_llm_twin"] if twin else [])]
    steps: dict[str, list[tuple[str, Any]]] = {
        s: [] for s in ("twin", "watermark", "execute", "detect", "metrics")
    }
    for cell in record.cells:
        if cell.not_applicable or cell.config_hash is None:
            continue
        model, lang, h = cell.model_id, cell.language, cell.config_hash
        name = f"{model} {lang} {h}"
        base = Cell(method=method, model_id=model, level=LEVEL, language=lang, split=SPLIT)
        if twin:
            steps["twin"].append((name, baseline_twin_ref(method, model, h, LEVEL, lang, SPLIT)))
            twin_cell = replace(base, source="baseline_twin")
            steps["execute"].append((f"{name} twin", execution_ref(twin_cell, h)))
        steps["watermark"].append((name, watermarked_ref(method, model, h, LEVEL, lang, SPLIT)))
        marked = replace(base, source="llm_watermarked")
        steps["execute"].append((name, execution_ref(marked, h)))
        for subject in subjects:
            ref = detection_ref(method, model, h, subject, LEVEL, lang, SPLIT)
            steps["detect"].append((f"{name} {subject}", ref))
        ref = metrics_ref(method, model, h, LEVEL, lang, SPLIT, FINAL_LEVELS)
        steps["metrics"].append((name, ref))

    sys.stdout.write(
        f"##### {method} on {LEVEL} {SPLIT} (frozen {record.frozen_at:%Y-%m-%d %H:%M} UTC)\n"
    )
    for step, refs in steps.items():
        if not refs:
            continue  # passo che il metodo non ha (gemella)
        missing = [n for n, r in refs if not store.exists(r)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        sys.stdout.write(f"{step:<10} {len(refs) - len(missing):>4}/{len(refs):<4} {state}\n")
        if args.missing:
            sys.stdout.write("".join(f"    missing: {m}\n" for m in missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
