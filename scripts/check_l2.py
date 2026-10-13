"""Stato del Livello 2 (Milestone 9): artefatti completi per passo di ``submit_l2.sh``.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_l2.py [--missing]

Passi: ``data`` (split, problemi, nativi e integrazione dev L1+L2), ``canonical`` (esecuzione delle
soluzioni canoniche, con il Pass@1 che deve essere 100%), ``baseline`` (2 modelli × 4 linguaggi ×
dev/test) ed ``execute`` (esecuzione della baseline, con Pass@1 per cella). Un passo è finito solo
se è ``COMPLETE``; altrimenti si rilancia lo stesso comando di ``submit_l2.sh``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.pipeline.stage import Cell
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import baseline_ref
from bench.pipeline.stages.prepare_data import (
    native_ref,
    pooled_integration_ref,
    problems_ref,
    split_ref,
)
from bench.store.artifact_store import ArtifactStore

MODELS = ["qwen25_coder_7b", "deepseek_coder_6p7b"]
SPLITS = ["dev", "test"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--missing", action="store_true")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=execute"])
    store = ArtifactStore(cfg.paths.artifacts)
    steps: dict[str, list[tuple[str, Any]]] = {"data": [("split_L2", split_ref("L2"))]}
    for lang in Language:
        steps["data"] += [
            (f"problems {lang}", problems_ref(lang, "L2")),
            (f"natives {lang}", native_ref(lang, "L2")),
            (f"integration dev L1+L2 {lang}", pooled_integration_ref(lang)),
        ]
        canonical = Cell(source="canonical", level="L2", language=str(lang))
        steps.setdefault("canonical", []).append((f"{lang}", execution_ref(canonical)))
        for model in MODELS:
            for split in SPLITS:
                name = f"{model} {lang} {split}"
                steps.setdefault("baseline", []).append(
                    (name, baseline_ref(model, "L2", str(lang), split))
                )
                cell = Cell(
                    source="llm_baseline",
                    model_id=model,
                    level="L2",
                    language=str(lang),
                    split=split,
                )
                steps.setdefault("execute", []).append((name, execution_ref(cell)))

    out = sys.stdout.write
    out("##### Livello 2 (CodeNet)\n")
    for step, refs in steps.items():
        missing = [name for name, ref in refs if not store.exists(ref)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        out(f"{step:<10} {len(refs) - len(missing):>4}/{len(refs):<4} {state}\n")
        if args.missing and missing:
            out("".join(f"    missing: {m}\n" for m in missing))

    out("\n##### Pass@1 (frazione di campioni PASSED)\n")
    for step in ("canonical", "execute"):
        for name, ref in steps[step]:
            if store.exists(ref):
                table = store.read_table(ref)
                passed = float((table["status"] == "PASSED").mean()) if len(table) else 0.0
                bad = table.loc[table["status"] != "PASSED", "status"].value_counts().to_dict()
                out(f"{step:<10} {name:<36} n={len(table):>5}  PASSED {passed:.3f}  {bad}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
