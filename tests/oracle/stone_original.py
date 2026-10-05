"""Percorso **originale** di STONE per l'oracle (SPEC §21.4). Ambiente ``stone`` (Python 3.9).

    PYTHONPATH=build/patched/stone/stone_implementation \\
        <python di stone> tests/oracle/stone_original.py \\
        --inputs tests/fixtures/oracle/stone/inputs.json \\
        --out tests/fixtures/oracle/stone/original.json

Usa il codice del repository come in ``run.py``: ``TransformersConfig`` +
``STONEAutoWatermark.load``
e i metodi ``generate_watermarked_text`` e ``detect_watermark`` dell'istanza ``STONE``. Le sole
differenze da ``run.py`` sono quelle decise nell'audit: vocabolario del generatore
(``vocab_size=None``), prompt chat del framework (stringa del chat template) e decoding neutro con
il seme del problema; n = 1 (una sequenza: comportamento identico all'originale anche senza 0001).
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
    from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig
    from utils.transformers_config import TransformersConfig
    from watermark.auto_watermark import STONEAutoWatermark

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
    instances: dict[str, Any] = {}

    def stone(language: str) -> Any:
        if language not in instances:
            config = TransformersConfig(
                model=model,
                tokenizer=tokenizer,
                vocab_size=hp["vocab_size"],
                device=ns.device,
                generation_config=generation_config,  # gen_kwargs di generate_watermarked_text
            )
            instances[language] = STONEAutoWatermark.load(
                "STONE",
                transformers_config=config,
                skipping_rule=hp["skipping_rule"],
                watermark_on_pl=hp["watermark_on_pl"],
                gamma=hp["gamma"],
                delta=hp["delta"],
                hash_key=data["key"],
                z_threshold=hp["z_threshold"],
                prefix_length=hp["prefix_length"],
                language=language,
            )
        return instances[language]

    generations: list[dict[str, Any]] = []
    for prompt in data["prompts"]:
        messages = prompt["messages"]
        text = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        ids_template = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        ids_text = tokenizer(text, add_special_tokens=True)["input_ids"]
        torch.manual_seed(prompt["seed"])
        torch.cuda.manual_seed_all(prompt["seed"])
        full = stone(prompt["language"]).generate_watermarked_text(text)
        prompt_decoded = tokenizer.decode(ids_text, skip_special_tokens=True)
        generations.append(
            {
                "problem_key": prompt["problem_key"],
                "prompt_ids_match": list(ids_template) == list(ids_text),
                "full_text": full,
                "new_text": full[len(prompt_decoded) :]
                if full.startswith(prompt_decoded)
                else None,
            }
        )

    detections: list[dict[str, Any]] = []
    codes = list(data["codes"]) + [
        {"id": f"generated:{g['problem_key']}", "language": "python", "code": g["new_text"]}
        for g in generations
        if g["new_text"] is not None
    ]
    for item in codes:
        try:
            result = stone(item["language"]).detect_watermark(item["code"])
            detections.append(
                {
                    "id": item["id"],
                    "language": item["language"],
                    "code": item["code"],
                    "score": float(result["score"]),
                    "is_watermarked": bool(result["is_watermarked"]),
                    "error": None,
                }
            )
        except ValueError as exc:
            detections.append(
                {
                    "id": item["id"],
                    "language": item["language"],
                    "code": item["code"],
                    "score": None,
                    "is_watermarked": None,
                    "error": f"ValueError: {exc}",
                }
            )
    out = {
        "vocab_size": stone("python").config.vocab_size,
        "transformers": __import__("transformers").__version__,
        "torch": torch.__version__,
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
