"""Stato della fase 0 della Milestone 10 di un metodo: L1 test con la configurazione congelata.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_test.py METHOD [--missing]
    python scripts/check_test.py mcss|multimark [--level L1|L2|L3] [--missing]
    python scripts/check_test.py mcss|multimark --plan       # prima del congelamento (P6)

Legge ``freeze/<metodo>.json`` (ADR-013) e, per ogni passo di ``submit_test_l1.sh`` (gemella di
MCGMark, watermark, execute, detect, metrics), conta gli artefatti **completi** della parte di test
di L1, una cella per (modello, linguaggio) con la configurazione congelata. Il primo passo
``INCOMPLETE`` è il prossimo da lanciare; un job sparito da ``squeue`` ha davvero finito solo se il
suo passo risulta ``COMPLETE`` qui.

Metodi post-hoc ``mcss`` e ``multimark`` (P6, ADR-019): una riga per configurazione congelata
(etichetta), su ``--level`` (L1, L2 o L3 = ClassEval); per ogni metrica già scritta controlla che
la soglia usata (sha256 negli input del manifest) sia quella del congelamento (nessuna soglia
ricalibrata). ``--plan`` (sola lettura, anche prima del congelamento): i passi di test delle catene
(``bench.chains.posthoc_steps``) con le celle che ciascuno pianificherebbe, dagli hash del registro
``configs/freeze/posthoc.yaml``, e il totale; è il controllo a vuoto della parte di test.
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
from bench.pipeline.stages.watermark import (
    baseline_twin_ref,
    make_adapter,
    twin_config_hash,
    watermarked_ref,
)
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
SUBJECTS = ("positives", "neg_human", "neg_llm")
POSTHOC = ("mcss", "multimark")


def plan(method: str) -> int:
    """Celle dei passi di test di un post-hoc, con gli hash del registro (sola lettura)."""
    import bench.pipeline.stages  # noqa: F401  (popola STAGES)
    from bench.chains import posthoc_steps
    from bench.pipeline.stage import CellPlanner
    from bench.registry import STAGES

    base = load_experiment(["paths=cluster", "stage=watermark"])
    total = 0
    sys.stdout.write(f"##### {method}: test steps planned with the registry hashes (dry run)\n")
    for step in posthoc_steps(method, str(base.paths.wmb)):
        cfg = load_experiment(["paths=cluster", f"stage={step.stage}", *step.overrides])
        cells = CellPlanner(cfg, posthoc_guard=False).cells_for(STAGES.get(step.stage))
        hashes = sorted({c.config_hash for c in cells if c.config_hash})
        total += len(cells)
        sys.stdout.write(f"  {step.key:<38} {step.stage:<24} {len(cells):>4} cells  "
                         f"{'GPU' if step.gpu else 'CPU'}  {' '.join(hashes)}\n")  # fmt: skip
    sys.stdout.write(f"  total {total} cells\n")
    return 0


def posthoc(method: str, level: str, missing: bool) -> int:
    """Stato della parte di test di un post-hoc congelato su un livello (P6)."""
    from bench.pipeline.stages.calibrate import threshold_ref

    cfg = load_experiment(["paths=cluster", "stage=watermark"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, method)
    if record is None or record.library_commit is None:
        sys.stdout.write(f"##### {method}: NOT FROZEN (post-hoc registry), see --plan\n")
        return 1
    steps: dict[str, list[tuple[str, Any]]] = {s: [] for s in ("watermark", "execute", "detect",
                                                               "metrics")}  # fmt: skip
    bad: list[str] = []
    for cell in record.cells:
        if cell.not_applicable or cell.config_hash is None:
            continue
        model, lang, h = cell.model_id, cell.language, cell.config_hash
        name = f"{model} {lang} {cell.label} {h}"
        base = Cell(method=method, model_id=model, level=level, language=lang, split=SPLIT)
        steps["watermark"].append((name, watermarked_ref(method, model, h, level, lang, SPLIT)))
        steps["execute"].append((name, execution_ref(replace(base, source="llm_watermarked"), h)))
        for subject in SUBJECTS:
            ref = detection_ref(method, model, h, subject, level, lang, SPLIT)
            steps["detect"].append((f"{name} {subject}", ref))
        ref = metrics_ref(method, model, h, level, lang, SPLIT, FINAL_LEVELS)
        steps["metrics"].append((name, ref))
        manifest = store.read_manifest(ref)
        if manifest is not None:
            thr = str(threshold_ref(method, model, lang, h, FINAL_LEVELS).path)
            if manifest.inputs.get(thr) != cell.threshold_sha256:
                bad.append(f"{name}: metrics used {thr} sha256 {manifest.inputs.get(thr)}, "
                           f"frozen {cell.threshold_sha256}")  # fmt: skip
    sys.stdout.write(f"##### {method} on {level} {SPLIT} "
                     f"(frozen {record.frozen_at:%Y-%m-%d %H:%M} UTC)\n")  # fmt: skip
    for step, refs in steps.items():
        lost = [n for n, r in refs if not store.exists(r)]
        state = "COMPLETE" if not lost else "INCOMPLETE"
        sys.stdout.write(f"{step:<10} {len(refs) - len(lost):>4}/{len(refs):<4} {state}\n")
        if missing:
            sys.stdout.write("".join(f"    missing: {m}\n" for m in lost))
    if bad:
        sys.stdout.write("thresholds: MISMATCH\n" + "".join(f"    {b}\n" for b in bad))
    else:
        sys.stdout.write("thresholds: frozen ones in every metrics cell\n")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=["sweet", "acw", "stone", "promptmark", "mcgmark",
                                           *POSTHOC])  # fmt: skip
    parser.add_argument("--missing", action="store_true")
    parser.add_argument("--level", choices=["L1", "L2", "L3"], default=LEVEL,
                        help="post-hoc only (L3 = ClassEval)")  # fmt: skip
    parser.add_argument("--plan", action="store_true", help="post-hoc only: dry run of the steps")
    args = parser.parse_args()
    method = args.method
    if method in POSTHOC:
        return plan(method) if args.plan else posthoc(method, args.level, args.missing)
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
        if twin:  # gemella comune alle configurazioni (D32): hash dei soli iperparametri utili
            th = twin_config_hash(cfg, adapter, h)
            steps["twin"].append((name, baseline_twin_ref(method, model, th, LEVEL, lang, SPLIT)))
            twin_cell = replace(base, source="baseline_twin")
            steps["execute"].append((f"{name} twin", execution_ref(twin_cell, th)))
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
