"""Stato del Livello 3 (Milestone 13): artefatti completi per passo di ``submit_l3.sh``.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_l3.py [--missing] [--report]

Passi: ``data`` (split, problemi e negativi per linguaggio), ``canonical`` (esecuzione delle
canoniche), ``baseline`` (2 modelli × 4 linguaggi, solo test) ed ``execute`` (esecuzione della
baseline). Un passo è finito solo se è ``COMPLETE``; altrimenti si rilancia lo stesso comando di
``submit_l3.sh``. Con ``--report``, per esecuzione e dataset (ADR-016):

- ClassEval: Pass@1 (frazione PASSED) sulle 91 classi, senza le 3 di D41, e sulle 94;
- CodeSearchNet e The Stack: **validità con tree-sitter** (misura di riferimento: campioni senza
  ``tree-sitter: ERROR node`` né estrazione fallita) e, solo informativa, la frazione che supera
  anche ``py_compile``, ``javac``, ``g++ -fsyntax-only`` o ``node --check`` (PASSED);
- per la baseline anche la frazione di estrazioni riuscite.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import pandas as pd

from bench.config.builder import load_experiment
from bench.data.loaders.classeval import load_pass1_excluded
from bench.domain.enums import Language
from bench.pipeline.stage import Cell
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import baseline_ref
from bench.pipeline.stages.prepare_data import native_ref, problems_ref, split_ref
from bench.store.artifact_store import ArtifactStore

MODELS = ["qwen25_coder_7b", "deepseek_coder_6p7b"]
LEVEL, SPLIT = "L3", "test"
TREE_SITTER = "tree-sitter: ERROR node"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--missing", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=execute", "levels=[L3]"])
    store = ArtifactStore(cfg.paths.artifacts)
    steps: dict[str, list[tuple[str, Any]]] = {"data": [("split_L3", split_ref(LEVEL))]}
    for lang in Language:
        steps["data"] += [
            (f"problems {lang}", problems_ref(lang, LEVEL)),
            (f"natives {lang}", native_ref(lang, LEVEL)),
        ]
        canonical = Cell(source="canonical", level=LEVEL, language=str(lang))
        steps.setdefault("canonical", []).append((f"{lang}", execution_ref(canonical)))
        for model in MODELS:
            name = f"{model} {lang}"
            steps.setdefault("baseline", []).append(
                (name, baseline_ref(model, LEVEL, str(lang), SPLIT))
            )
            cell = Cell(source="llm_baseline", model_id=model, level=LEVEL, language=str(lang),
                        split=SPLIT)  # fmt: skip
            steps.setdefault("execute", []).append((name, execution_ref(cell)))

    out = sys.stdout.write
    out("##### Livello 3 (ClassEval, CodeSearchNet, The Stack C++), test\n")
    for step, refs in steps.items():
        missing = [name for name, ref in refs if not store.exists(ref)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        out(f"{step:<10} {len(refs) - len(missing):>4}/{len(refs):<4} {state}\n")
        if args.missing and missing:
            out("".join(f"    missing: {m}\n" for m in missing))
    if args.report:
        report(store, steps, load_pass1_excluded(cfg.datasets["classeval"].file("pass1_excluded")))
    return 0


def report(
    store: ArtifactStore, steps: dict[str, list[tuple[str, Any]]], excluded: dict[str, str]
) -> None:
    out = sys.stdout.write
    out(f"\n##### esiti per dataset (ClassEval: Pass@1 senza le {len(excluded)} classi di D41; "
        "CodeSearchNet e The Stack: tree-sitter, compilazione informativa)\n")  # fmt: skip
    for step in ("canonical", "execute"):
        for name, ref in steps[step]:
            if not store.exists(ref):
                continue
            table = store.read_table(ref)
            for dataset, part in table.groupby("dataset"):
                out(
                    f"{step:<10} {name:<32} {dataset:<14} {summary(str(dataset), part, excluded)}\n"
                )
    out("\n##### baseline: estrazioni riuscite per dataset\n")
    for name, ref in steps["baseline"]:
        if store.exists(ref):
            table = store.read_table(ref)
            for dataset, part in table.groupby("dataset"):
                ok = float(part["extraction_ok"].mean())
                out(f"  {name:<32} {dataset:<14} n={len(part):>5}  extraction_ok {ok:.3f}\n")


def summary(dataset: str, part: pd.DataFrame, excluded: dict[str, str]) -> str:
    """Riga di report di un dataset in un'esecuzione."""
    statuses = part["status"].value_counts().to_dict()
    passed = (part["status"] == "PASSED").astype(float)
    if dataset == "classeval":
        kept = ~part["problem_key"].isin(excluded)
        return (f"n={len(part):>5}  Pass@1 {passed[kept].mean():.3f} (91 classi), "
                f"{passed.mean():.3f} (94)  {statuses}")  # fmt: skip
    invalid = [
        status == "EXTRACTION_FAILED" or str(tail or "").startswith(TREE_SITTER)
        for status, tail in zip(part["status"], part["stderr_tail"], strict=True)
    ]
    valid = 1 - sum(invalid) / len(invalid) if invalid else float("nan")
    return (f"n={len(part):>5}  tree-sitter valid {valid:.3f}  "
            f"compile ok (informativo) {passed.mean():.3f}  {statuses}")  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
