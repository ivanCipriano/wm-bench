"""Input fissi dell'oracle di un metodo (SPEC §21.4), da lanciare con bench-core sul cluster.

    python tests/oracle/make_oracle_inputs.py sweet

Scrive ``tests/fixtures/oracle/<metodo>/inputs.json``:
- 5 prompt fissi di HumanEval+ (``humaneval/0`` … ``humaneval/4``) con i messaggi chat del
  ``PromptBuilder`` e il seme di generazione della baseline (Qwen);
- codici fissati per la rilevazione (soluzioni canoniche e campione 0 della baseline Qwen),
  ciascuno con i messaggi del suo problema come contesto (SPEC §9.1);
- chiave, iperparametri nativi di default e decoding neutro con n = 1.

Il percorso originale (``<metodo>_original.py``, ambiente del metodo) e lo shim leggono gli
stessi input.
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
from bench.methods.worker_client import WorkerClient
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.pipeline.stages.watermark import make_adapter
from bench.store.artifact_store import ArtifactStore

MODEL = "qwen25_coder_7b"
KEYS = [f"humaneval/{i}" for i in range(5)]


def main(argv: list[str]) -> int:
    method = argv[1]
    cfg = load_experiment(["paths=cluster", "stage=watermark", "levels=[L1]"])
    store = ArtifactStore(cfg.paths.artifacts)
    problems = store.read_table(problems_ref("L1", "python")).set_index("problem_key")
    builder = PromptBuilder.from_config(cfg.prompt)
    adapter = make_adapter(cfg, method)
    adapter.worker = WorkerClient({}, Path("."), 1)  # serve solo per chiave e iperparametri
    hp = adapter.default_hparams()
    decoding = cfg.decoding["level1"].model_copy(update={"n": 1})
    prompts, codes = [], []
    for key in KEYS:
        problem = Problem.model_validate({"problem_key": key, **problems.loc[key].to_dict()})
        messages = builder.build(problem)
        prompts.append(
            {
                "problem_key": key,
                "language": "python",
                "seed": generation_seed(cfg.global_seed, MODEL, key, "python"),
                "messages": messages,
            }
        )
        canonical = (problem.prompt_text or "") + (problem.canonical_solution or "")
        codes.append(
            {
                "id": f"canonical:{key}",
                "language": "python",
                "code": canonical,
                "messages": messages,
            }
        )
        baseline = store.read_table(baseline_ref(MODEL, "L1", "python", str(problem.split)))
        row = baseline[(baseline["problem_key"] == key) & (baseline["sample_index"] == 0)].iloc[0]
        codes.append(
            {
                "id": f"baseline:{key}",
                "language": "python",
                "code": str(row["code"]),
                "messages": messages,
            }
        )
    # Un codice senza contesto (negativi senza prompt, D3).
    codes.append(
        {
            "id": "nocontext:humaneval/0",
            "language": "python",
            "code": codes[0]["code"],
            "messages": None,
        }
    )
    data = {
        "method": method,
        "model_id": MODEL,
        "model_path": str(cfg.models_catalog[MODEL].path),
        "key": adapter.key("k1"),
        "native_hparams": adapter.to_native_hparams(hp),
        "decoding": {**neutral_settings(decoding), "torch_dtype": cfg.models_catalog[MODEL].dtype},
        "prompts": prompts,
        "codes": codes,
    }
    out = repo_root() / "tests" / "fixtures" / "oracle" / method / "inputs.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {out} ({len(prompts)} prompts, {len(codes)} codes)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
