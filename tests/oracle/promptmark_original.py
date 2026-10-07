"""Percorso diretto di PromptMark per l'oracle (SPEC §21.4). Ambiente ``promptmark`` (Python 3.10).

    PYTHONPATH=build/patched/promptmark/src:shims <python di promptmark> \\
        tests/oracle/promptmark_original.py --inputs tests/fixtures/oracle/promptmark/inputs.json \\
        --out tests/fixtures/oracle/promptmark/original.json [--share K/M]

Riproduce expI senza lo shim e **senza i limiti** sull'esecuzione degli esempi (processo figlio
del repository così com'è): provider ``inprocess_hf`` (patch 0001) costruito con la factory,
costanti di configurazione del modulo (``SEED_KEY``, ``Z_THRESHOLD``, ``G_MIN``/``G_MAX``), lista
di frequenza in ``results/dataset/`` della cartella di lavoro, record con il messaggio utente e gli
esempi del prompt, ``run_phase1`` con lo stesso schema di semi dello shim. Registra tutte le
iterazioni e la risposta scelta; poi la rilevazione su codici fissi con ``detect_watermark`` come
``evaluate_candidate``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from typing import Any, Dict, List


def main() -> int:  # noqa: PLR0915 - script lineare dell'oracle
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--share", default="1/1", help="K/M: prompt e codici con i %% M == K - 1")
    ns = parser.parse_args()
    inputs_path, out_path = os.path.abspath(ns.inputs), os.path.abspath(ns.out)

    import torch
    from bench_contracts import derive_seed
    from bench_shims.promptmark.examples import prompt_examples
    from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig

    with open(inputs_path, encoding="utf-8") as handle:
        data = json.load(handle)
    part, parts = (int(x) for x in ns.share.split("/"))
    data["prompts"] = [p for i, p in enumerate(data["prompts"]) if i % parts == part - 1]
    data["codes"] = [c for i, c in enumerate(data["codes"]) if i % parts == part - 1]
    hp = data["native_hparams"]

    workdir = tempfile.mkdtemp(prefix="promptmark_oracle_")
    target = os.path.join(workdir, "results", "dataset")
    os.makedirs(target)
    with open(os.path.join(target, "humaneval_letter_freqs.json"), "w", encoding="utf-8") as h:
        json.dump(
            {"letter_freqs": hp["letter_freqs"], "total_identifiers": hp["total_identifiers"]}, h
        )
    with open(os.path.join(target, "mbpp_letter_freqs.json"), "w", encoding="utf-8") as h:
        json.dump({"letter_freqs": {}, "total_identifiers": 0}, h)
    os.chdir(workdir)

    with contextlib.redirect_stdout(io.StringIO()):
        import llm_providers
        import shared_utils

        sys.path.insert(0, os.path.join(os.path.dirname(shared_utils.__file__), "watermarking"))
        import exp_iterative_wm as exp
    exp.SEED_KEY = str(data["key"])
    exp.Z_THRESHOLD = float(hp["z_threshold"])
    exp.G_MIN, exp.G_MAX = int(hp["g_min"]), int(hp["g_max"])

    decoding = dict(data["decoding"])
    dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16}[decoding.pop("torch_dtype")]
    tokenizer = AutoTokenizer.from_pretrained(data["model_path"], local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        data["model_path"], torch_dtype=dtype, local_files_only=True
    ).to(ns.device)
    model.eval()
    eos = sorted({tokenizer.eos_token_id} | set(_ids(model.generation_config.eos_token_id)))
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else eos[0]
    generation_config = GenerationConfig(**decoding, eos_token_id=eos, pad_token_id=pad)
    model.generation_config = generation_config
    seeds: List[int] = []
    provider = llm_providers.LLMProviderFactory.create(
        "inprocess_hf",
        model=model,
        tokenizer=tokenizer,
        generation_config=generation_config,
        system_prompt=data["prompts"][0]["messages"][0]["content"] if data["prompts"] else "",
        device=ns.device,
        seed_fn=lambda: seeds.pop(0),
    )
    shared_utils._llm_provider = provider
    shared_utils._current_provider_name = "inprocess_hf"

    green, red, size = exp.get_red_green_sets(
        secret_key=exp.SEED_KEY, base_dir=".", g_min=exp.G_MIN, g_max=exp.G_MAX
    )
    freqs, total = exp.load_frequency_data(green, ".")
    gamma = exp.calculate_gamma(freqs, total, green)

    original_evaluate = exp.evaluate_candidate
    generations: List[Dict[str, Any]] = []
    for prompt in data["prompts"]:
        user = next(m["content"] for m in reversed(prompt["messages"]) if m["role"] == "user")
        examples = prompt_examples(user)
        record = {
            "task_id": f"{prompt['problem_key']}#0",
            "prompt": user,
            "test_list": examples,
            "test_imports": [],
            "canonical_solution": "",
        }
        seeds[:] = [prompt["seed"]] + [
            derive_seed(prompt["seed"], "promptmark-iter", t) for t in range(1, int(hp["iter_cap"]))
        ]
        evaluations: List[Dict[str, Any]] = []

        def recorded(record_: Any, code: str, store: List[Dict[str, Any]] = evaluations) -> Any:
            result = original_evaluate(record_, code)
            store.append(result)
            return result

        exp.evaluate_candidate = recorded
        with contextlib.redirect_stdout(io.StringIO()):
            selected, _ = exp.run_phase1(record, max_iterations=int(hp["iter_cap"]))
        exp.evaluate_candidate = original_evaluate
        generations.append(
            {
                "problem_key": prompt["problem_key"],
                "seed": prompt["seed"],
                "examples": examples,
                "raw_output": selected.get("full_llm_response", ""),
                "selected_iteration": selected.get("iteration"),
                "selected_code": selected.get("code", ""),
                "iterations": [
                    {
                        "correctness": bool(e.get("correctness")),
                        "tests_passed": int(e.get("tests_passed", 0)),
                        "tests_failed": int(e.get("tests_failed", 0)),
                        "meets_z": bool(e.get("meets_z")),
                        "p_exact": float(e.get("generated_p_exact", 1.0)),
                        "code": e.get("code", ""),
                        "response": e.get("full_llm_response", ""),
                    }
                    for e in evaluations
                ],
            }
        )

    codes = list(data["codes"]) + [
        {"id": f"generated:{g['problem_key']}", "language": "python", "code": g["selected_code"]}
        for g in generations
    ]
    detections = []
    for item in codes:
        entry: Dict[str, Any] = {
            "id": item["id"],
            "language": item["language"],
            "code": item["code"],
        }
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = shared_utils.detect_watermark(
                    "", item["code"], green, red, gamma,
                    comment_enabled=exp.COMMENT_ENABLED, z_threshold=exp.Z_THRESHOLD,
                )  # fmt: skip
            entry.update(
                {
                    "error": None,
                    "token_count": int(res["generated_token_count"]),
                    "green_count": int(res["generated_green_count"]),
                    "p_exact": float(res["generated_p_exact"]),
                    "score": float(res["generated_score"]),
                    "is_watermarked": bool(res["generated_is_watermarked"]),
                }
            )
        except KeyError as exc:  # errore di sintassi nel codice valutato (audit §6)
            entry["error"] = f"KeyError {exc}"
        detections.append(entry)

    out = {
        "variant": "original",
        "transformers": __import__("transformers").__version__,
        "torch": torch.__version__,
        "vocab_size": len(tokenizer.get_vocab()),
        "green_letters": sorted(green),
        "green_size": size,
        "gamma": gamma,
        "generations": generations,
        "detections": detections,
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    sys.stdout.write(
        f"wrote {out_path}: {len(generations)} generations, {len(detections)} detections\n"
    )
    return 0


def _ids(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    return [int(v) for v in value]


if __name__ == "__main__":
    sys.exit(main())
