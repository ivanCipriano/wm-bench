"""Percorso **originale** di SWEET per l'oracle (SPEC §21.4). Ambiente ``sweet`` (Python 3.10).

    PYTHONPATH=build/patched/sweet <python di sweet> tests/oracle/sweet_original.py \\
        --inputs tests/fixtures/oracle/sweet/inputs.json \\
        --out tests/fixtures/oracle/sweet/original.json

Riproduce i passi del repository:
- generazione come ``lm_eval/generation.py:74-78`` + ``lm_eval/utils.py:140-144``
  (``SweetLogitsProcessor`` in ``logits_processor`` di ``model.generate``);
- rilevazione come ``lm_eval/evaluator.py:130-170``: ``lm_eval.utils.calculate_entropy``,
  spostamento ``[0] + entropy[:-1]``, ``SweetDetector.detect(tokenized_text, tokenized_prefix,
  entropy)`` con ``SweetDetector`` costruito come in ``evaluator.py:247-251``.
Differenze decise nell'audit: prompt chat del framework, decoding neutro con il seme del
problema, chiave del framework (``hash_key``), n = 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--device", default="cuda:0")
    ns = parser.parse_args()

    import torch
    from lm_eval.utils import calculate_entropy
    from sweet import SweetDetector, SweetLogitsProcessor
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        GenerationConfig,
        LogitsProcessorList,
    )

    with open(ns.inputs, encoding="utf-8") as handle:
        data = json.load(handle)
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
    hp = data["native_hparams"]
    vocab = list(tokenizer.get_vocab().values())
    common = {
        "vocab": vocab,
        "gamma": hp["gamma"],
        "delta": hp["delta"],
        "seeding_scheme": hp["seeding_scheme"],
        "hash_key": data["key"],
        "select_green_tokens": hp["select_green_tokens"],
        "entropy_threshold": hp["entropy_threshold"],
    }

    generations: list[dict[str, Any]] = []
    for prompt in data["prompts"]:
        input_ids = tokenizer.apply_chat_template(
            prompt["messages"], add_generation_prompt=True, return_tensors="pt"
        ).to(ns.device)
        torch.manual_seed(prompt["seed"])
        torch.cuda.manual_seed_all(prompt["seed"])
        with torch.no_grad():
            output = model.generate(
                input_ids=input_ids,
                attention_mask=torch.ones_like(input_ids),
                num_return_sequences=1,
                generation_config=generation_config,
                logits_processor=LogitsProcessorList([SweetLogitsProcessor(**common)]),
            )
        new_text = tokenizer.batch_decode(
            output[:, input_ids.shape[1] :], skip_special_tokens=True
        )[0]
        generations.append({"problem_key": prompt["problem_key"], "new_text": new_text})

    detector = SweetDetector(tokenizer=tokenizer, z_threshold=hp["z_threshold"], **common)
    codes = list(data["codes"]) + [
        {
            "id": f"generated:{g['problem_key']}",
            "language": "python",
            "code": g["new_text"],
            "messages": p["messages"],
        }
        for g, p in zip(generations, data["prompts"])
    ]
    detections: list[dict[str, Any]] = []
    for item in codes:
        prefix = (
            list(tokenizer.apply_chat_template(item["messages"], add_generation_prompt=True))
            if item["messages"]
            else []
        )
        code_ids = list(tokenizer(item["code"], add_special_tokens=False)["input_ids"])
        tokenized_text = torch.tensor(prefix + code_ids, dtype=torch.long)
        tokenized_prefix = torch.tensor(prefix, dtype=torch.long)
        entropy = calculate_entropy(model, tokenized_text.to(ns.device))
        entropy = [0] + entropy[:-1]
        result = detector.detect(
            tokenized_text=tokenized_text, tokenized_prefix=tokenized_prefix, entropy=entropy
        )
        detections.append(
            {
                "id": item["id"],
                "language": item["language"],
                "code": item["code"],
                "messages": item["messages"],
                "invalid": bool(result.get("invalid", False)),
                "z_score": None if result.get("invalid") else float(result["z_score"]),
                "num_tokens_scored": result.get("num_tokens_scored"),
                "num_green_tokens": result.get("num_green_tokens"),
                "prediction": result.get("prediction"),
            }
        )
    out = {
        "transformers": __import__("transformers").__version__,
        "torch": torch.__version__,
        "vocab_size": len(vocab),
        "generations": generations,
        "detections": detections,
    }
    with open(ns.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    sys.stdout.write(
        f"wrote {ns.out}: {len(generations)} generations, {len(detections)} detections\n"
    )
    return 0


def _ids(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    return [int(v) for v in value]


if __name__ == "__main__":
    sys.exit(main())
