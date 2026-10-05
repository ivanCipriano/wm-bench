"""Input fissi dell'oracle di STONE (SPEC §21.4), da lanciare con bench-core sul cluster.

    python tests/oracle/make_stone_inputs.py

Scrive ``tests/fixtures/oracle/stone/inputs.json``:
- 5 prompt fissi di HumanEval+ (``humaneval/0`` … ``humaneval/4``) con i messaggi chat del
  ``PromptBuilder`` e il seme di generazione della baseline (Qwen);
- codici fissati per la rilevazione: soluzioni canoniche e il campione 0 della baseline Qwen;
- chiave, iperparametri di default e decoding neutro con n = 1.

Il percorso originale (``stone_original.py``, ambiente ``stone``) e lo shim leggono gli stessi
input: così il confronto non dipende da come ciascuno costruisce i prompt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from bench.config.builder import load_experiment, repo_root
from bench.domain.models import Problem
from bench.generation.decoding import neutral_settings
from bench.generation.hf_generator import generation_seed
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.adapters.stone import StoneAdapter
from bench.methods.worker_client import WorkerClient
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.store.artifact_store import ArtifactStore

MODEL = "qwen25_coder_7b"
KEYS = [f"humaneval/{i}" for i in range(5)]
OUT = repo_root() / "tests" / "fixtures" / "oracle" / "stone" / "inputs.json"


def main() -> int:
    cfg = load_experiment(["paths=cluster", "stage=watermark", "levels=[L1]"])
    store = ArtifactStore(cfg.paths.artifacts)
    problems = store.read_table(problems_ref("L1", "python")).set_index("problem_key")
    builder = PromptBuilder.from_config(cfg.prompt)
    adapter = StoneAdapter(cfg.methods_catalog["stone"], cfg, WorkerClient({}, Path("."), 1))
    hp = adapter.default_hparams()
    decoding = cfg.decoding["level1"].model_copy(update={"n": 1})
    prompts, codes = [], []
    for key in KEYS:
        problem = Problem.model_validate({"problem_key": key, **problems.loc[key].to_dict()})
        prompts.append(
            {
                "problem_key": key,
                "language": "python",
                "seed": generation_seed(cfg.global_seed, MODEL, key, "python"),
                "messages": builder.build(problem),
            }
        )
        codes.append(
            {
                "id": f"canonical:{key}",
                "language": "python",
                "code": (problem.prompt_text or "") + (problem.canonical_solution or ""),
            }
        )
        split = str(problem.split)
        baseline = store.read_table(baseline_ref(MODEL, "L1", "python", split))
        row = baseline[(baseline["problem_key"] == key) & (baseline["sample_index"] == 0)].iloc[0]
        codes.append({"id": f"baseline:{key}", "language": "python", "code": str(row["code"])})
    data = {
        "model_id": MODEL,
        "model_path": str(cfg.models_catalog[MODEL].path),
        "key": adapter.key("k1"),
        "native_hparams": adapter.to_native_hparams(hp),
        "decoding": {**neutral_settings(decoding), "torch_dtype": cfg.models_catalog[MODEL].dtype},
        "prompts": prompts,
        "codes": codes,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {OUT} ({len(prompts)} prompts, {len(codes)} codes)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
