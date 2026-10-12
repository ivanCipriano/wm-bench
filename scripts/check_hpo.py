"""Stato dello sweep dell'HPO di un metodo (Milestone 8): celle complete per passo.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_hpo.py METHOD [--level L1] [--missing]

Per ogni passo (traiettorie di PromptMark, watermark, execute, detect, calibrate, tune) conta gli
artefatti **completi** (manifest con ``status: complete``), una cella per configurazione della
griglia, modello e linguaggio. Un job sparito da ``squeue`` ha davvero finito solo se il suo
passo risulta ``COMPLETE`` qui; altrimenti si rilancia lo stesso comando (riprende dalle celle
fatte). ``--missing`` elenca le celle mancanti.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Any

from bench.config.builder import load_experiment
from bench.hpo.grid import load_grid
from bench.pipeline.stage import Cell
from bench.pipeline.stages.calibrate import threshold_ref
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.tune import selected_ref
from bench.pipeline.stages.watermark import make_adapter, watermarked_ref
from bench.store.artifact_store import ArtifactStore

MODELS = ["qwen25_coder_7b", "deepseek_coder_6p7b"]
LANGS = {
    "sweet": ["python", "java", "cpp", "javascript"],
    "stone": ["python", "java", "cpp"],
    "mcgmark": ["python"],
    "promptmark": ["python"],
    "acw": ["python"],
}
SUBJECTS = ("positives", "neg_human", "neg_llm")
SHOW = 50  # celle mancanti elencate al massimo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=sorted(LANGS))
    parser.add_argument("--level", default="L1")
    parser.add_argument("--missing", action="store_true")
    args = parser.parse_args()
    method, level, split = args.method, args.level, "dev"
    cfg = load_experiment(["paths=cluster", "stage=watermark"])
    store = ArtifactStore(cfg.paths.artifacts)
    adapter: Any = make_adapter(cfg, method)
    grid = load_grid(method)
    candidates = [adapter.config_hash(p.hparams) for p in grid.candidates()]
    trajectories = [adapter.config_hash(p.hparams) for p in grid.trajectories()]

    steps: dict[str, list[tuple[str, Any]]] = {}
    for model in MODELS:
        for lang in LANGS[method]:
            base = Cell(method=method, model_id=model, level=level, language=lang, split=split)
            for h in trajectories:
                ref = watermarked_ref(method, model, h, level, lang, split)
                steps.setdefault("trajectories", []).append((f"{model} {lang} {h}", ref))
            for h in candidates:
                name = f"{model} {lang} {h}"
                steps.setdefault("watermark", []).append(
                    (name, watermarked_ref(method, model, h, level, lang, split))
                )
                marked = replace(base, source="llm_watermarked")
                steps.setdefault("execute", []).append((name, execution_ref(marked, h)))
                for subject in SUBJECTS:
                    ref = detection_ref(method, model, h, subject, level, lang, split)
                    steps.setdefault("detect", []).append((f"{name} {subject}", ref))
                steps.setdefault("calibrate", []).append(
                    (name, threshold_ref(method, model, lang, h))
                )
            steps.setdefault("tune", []).append(
                (f"{model} {lang}", selected_ref(method, model, lang))
            )

    sys.stdout.write(
        f"##### HPO {method} ({level} {split}): {len(candidates)} candidates, "
        f"{len(trajectories)} trajectories\n"
    )
    for step, refs in steps.items():
        done = [name for name, ref in refs if store.exists(ref)]
        missing = [name for name, ref in refs if not store.exists(ref)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        sys.stdout.write(f"{step:<13} {len(done):>4}/{len(refs):<4} {state}\n")
        if args.missing and missing:
            sys.stdout.write("".join(f"    missing: {m}\n" for m in missing[:SHOW]))
            if len(missing) > SHOW:
                sys.stdout.write(f"    ... and {len(missing) - SHOW} more\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
