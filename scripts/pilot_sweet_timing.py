"""Pilota dei tempi di SWEET con Qwen su A100 (gpuq) e H100 (aiq) (piano della chiusura, fase 1).

Uso (bench-core; vedi scripts/pilot_sweet_timing.sh, che lancia un job per partizione):
    python scripts/pilot_sweet_timing.py run      # in un job GPU: misura e scrive <partizione>.json
    python scripts/pilot_sweet_timing.py report   # nodo di login: confronto fra le partizioni

Nella stima di L3 (``M13_l3_cost_2.txt``) i tempi di SWEET con Qwen variano fino a 40 volte fra le
configurazioni dello sweep sugli stessi dati, quelli di DeepSeek no: il pilota misura su un nodo
fisso, con la configurazione scelta su L1+L2 per (Qwen, Java):

- generazione: ``N_PROBLEMS`` problemi di L2 dev (6 campioni ciascuno, decoding di L2);
- rilevazione: ``N_DETECT`` negativi umani di L2 dev Java (contesto del loro problema).

Solo dati di sviluppo; scrive solo in ``$WMB/tmp/sweet_timing/`` (nessun artefatto). Il tempo per
campione è quello del worker (``elapsed_s``) e la durata reale del processo, con il caricamento.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.domain.models import CodeSample, Problem, SelectedConfig
from bench.generation.decoding import neutral_settings
from bench.generation.prompt_builder import PromptBuilder
from bench.hpo.freeze import FINAL_LEVELS
from bench.hpo.grid import resolve
from bench.methods.base import DetectInput
from bench.pipeline.stage import hardware
from bench.pipeline.stages.generate_baseline import decoding_for, problems_ref
from bench.pipeline.stages.prepare_data import native_ref
from bench.pipeline.stages.tune import selected_ref
from bench.pipeline.stages.watermark import make_adapter
from bench.store.artifact_store import ArtifactStore

MODEL, LANG, LEVEL = "qwen25_coder_7b", "java", "L2"
N_PROBLEMS, N_DETECT = 5, 50


def run(cfg: Any, out: Path) -> None:
    store = ArtifactStore(Path(cfg.paths.artifacts))
    selected = store.read_model(selected_ref("sweet", MODEL, LANG, FINAL_LEVELS), SelectedConfig)
    adapter = make_adapter(cfg, "sweet")
    hp, cfg_hash, _ = resolve(cfg, adapter, selected.config_hash)
    model = cfg.models_catalog[MODEL]
    decoding = decoding_for(cfg, LEVEL)
    request = {**neutral_settings(decoding), "torch_dtype": model.dtype}
    prompts = PromptBuilder.from_config(cfg.prompt)
    table = store.read_table(problems_ref(LEVEL, LANG))
    problems = [
        Problem.model_validate(r)
        for r in table[table["split"] == "dev"]
        .sort_values("problem_key")
        .head(N_PROBLEMS)
        .to_dict(orient="records")
    ]
    partition = os.environ.get("SLURM_JOB_PARTITION", "local")
    work = out / partition
    start = time.perf_counter()
    gen = adapter.embed_from_prompts(  # type: ignore[attr-defined]
        problems, hp, model, "k1", request, decoding.n, prompts, work / "embed"
    )
    gen_wall = time.perf_counter() - start
    natives = store.read_table(native_ref(Language(LANG), LEVEL))
    natives = natives[natives["split"] == "dev"].sort_values("sample_id").head(N_DETECT)
    by_key = {
        p.problem_key: p
        for p in (Problem.model_validate(r) for r in table.to_dict(orient="records"))
    }
    inputs = []
    for r in natives.to_dict(orient="records"):
        s = CodeSample.model_validate({k: r[k] for k in CodeSample.model_fields})
        problem = by_key.get(s.problem_key)
        inputs.append(
            DetectInput(
                s.sample_id, LANG, s.code, prompts.build(problem) if problem is not None else None
            )
        )
    start = time.perf_counter()
    detect = adapter.detect_codes  # type: ignore[attr-defined]
    results = detect(inputs, hp, model, "k1", request, work / "detect")
    det_wall = time.perf_counter() - start
    worker = gen.worker.results if gen.worker else []
    data = {
        "hardware": hardware(),
        "config_hash": cfg_hash,
        "generation": {
            "problems": len(problems),
            "samples": len(gen.samples),
            "wall_s": round(gen_wall, 1),
            "worker_s": round(sum(r.elapsed_s or 0 for r in worker), 1),
        },
        "detection": {
            "samples": len(results),
            "wall_s": round(det_wall, 1),
            "worker_s": round(sum(r.elapsed_s or 0 for r in results), 1),
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{partition}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(data, indent=1) + "\n")


def report(out: Path) -> None:
    w = sys.stdout.write
    w(f"##### SWEET timing pilot: {MODEL}, {LANG}, {LEVEL} dev\n")
    for path in sorted(out.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        g, det = d["generation"], d["detection"]
        w(f"\n## {path.stem} ({d['hardware'].get('gpu')}, node {d['hardware'].get('node')})\n")
        per_problem = g["worker_s"] / max(1, g["problems"])
        per_sample = det["worker_s"] / max(1, det["samples"])
        w(f"  generation: {g['problems']} problems, {g['samples']} samples, wall {g['wall_s']} s, ")
        w(f"worker {g['worker_s']} s -> {per_problem:.1f} s/problem\n")
        w(f"  detection: {det['samples']} samples, wall {det['wall_s']} s, ")
        w(f"worker {det['worker_s']} s -> {per_sample:.3f} s/sample\n")
    w(
        "\nreference (M13_l3_cost_2.txt, sweep medians): generation 44.2 s/problem, detection "
        "16.409 s/sample (L2 dev java); minima 12.960 and 1.016\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["run", "report"])
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=watermark", "methods=[sweet]"])
    out = Path(cfg.paths.tmp) / "sweet_timing"
    if args.mode == "run":
        run(cfg, out)
    else:
        report(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
