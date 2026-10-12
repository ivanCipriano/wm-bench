"""Diagnosi del logits processor di STONE in generazione (Milestone 8). Ambiente ``stone``, gpuq.

Uso (ambiente ``stone``, ``PYTHONPATH=build/patched/stone/stone_implementation``):
    python scripts/diag_stone_processor.py [--inputs tests/fixtures/oracle/stone/inputs.json]
                                           [--deltas 0 1 3 10]

La prova dose-risposta (``M8_stone_dose.txt``) dà una frazione di token verdi uguale alla baseline
con δ = 1 e 3 e molto più alta con δ = 10. Questo script guarda dentro la generazione, sui 5 prompt
dell'oracle di STONE (stesso modello, chiave, decoding neutro e costruzione di ``STONE`` dello shim
e dell'oracle), e per ogni passo registra:

- **ordine dei processor:** il massimo dei logit che entrano nel processor di STONE contro quello
  dei logit grezzi del modello (``output_logits``). Rapporto 1 = il processor lavora sui logit
  grezzi (prima di temperatura e top-p, come previsto); rapporto 1/T = 5 = la temperatura è già
  stata applicata; logit ``-inf`` in ingresso = top-p già applicato;
- **bias applicato:** se il processor ha cambiato i logit, cioè se il token più probabile non è
  sintattico (regola di STONE, ``stone.py:276-392``);
- **scarto** fra il token più probabile e il miglior token verde, nei logit in ingresso: il bias δ
  può cambiare la scelta solo se lo scarto è minore di δ;
- se il token scelto è verde, nei passi con il bias applicato.

Non usa il framework: costruisce ``STONE`` come ``tests/oracle/stone_original.py``.
Compatibile con Python 3.9 (ambiente ``stone``).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Any


def _ids(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    return [int(v) for v in value]


def report(
    delta: float, inv_t: float, c: dict[str, int], ratios: list[float], gaps: list[float]
) -> None:
    applied = max(1, c["applied"])
    ratio = statistics.median(ratios) if ratios else float("nan")
    gap = statistics.median(gaps) if gaps else float("nan")
    sys.stdout.write(
        f"\ndelta {delta:g}: steps {c['steps']}, bias applied {c['applied']}"
        f" ({c['applied'] / max(1, c['steps']):.2f})\n"
        f"  incoming/raw top-logit ratio: median {ratio:.3f}"
        f" (1 = raw logits, {inv_t:.1f} = after temperature)\n"
        f"  steps with -inf logits in input: {c['n_inf_steps']}\n"
        f"  applied steps: top token green {c['top_green'] / applied:.3f}, "
        f"gap top-best green median {gap:.2f}, "
        f"0 < gap < delta {c['flippable'] / applied:.3f}, "
        f"chosen token green {c['chosen_green'] / applied:.3f}\n"
    )


def load(inputs: str, device: str) -> tuple[Any, Any, Any, dict[str, Any]]:
    """Modello, tokenizer, configurazione di generazione e input dell'oracle."""
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig

    with open(inputs, encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    decoding = dict(data["decoding"])
    dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16}[decoding.pop("torch_dtype")]
    tokenizer = AutoTokenizer.from_pretrained(data["model_path"], local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        data["model_path"], torch_dtype=dtype, local_files_only=True
    ).to(device)
    model.eval()
    eos = sorted({tokenizer.eos_token_id} | set(_ids(model.generation_config.eos_token_id)))
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else eos[0]
    generation_config = GenerationConfig(**decoding, eos_token_id=eos, pad_token_id=pad)
    model.generation_config = generation_config
    sys.stdout.write(
        f"transformers {transformers.__version__}, torch {torch.__version__}, "
        f"temperature {generation_config.temperature}, top_p {generation_config.top_p}\n"
    )
    return model, tokenizer, generation_config, data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inputs", default="tests/fixtures/oracle/stone/inputs.json")
    parser.add_argument("--deltas", type=float, nargs="+", default=[0.0, 1.0, 3.0, 10.0])
    parser.add_argument("--device", default="cuda:0")
    ns = parser.parse_args()

    import torch
    from transformers import LogitsProcessor, LogitsProcessorList
    from utils.transformers_config import TransformersConfig
    from watermark.auto_watermark import STONEAutoWatermark

    model, tokenizer, generation_config, data = load(ns.inputs, ns.device)
    hp = data["native_hparams"]

    class Probe(LogitsProcessor):  # type: ignore[misc]
        """Avvolge il processor di STONE e registra i logit in ingresso a ogni passo."""

        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.rows: list[dict[str, Any]] = []

        def __call__(self, input_ids: Any, scores: Any) -> Any:
            incoming = scores.clone()
            out = self.inner(input_ids, scores)
            row = incoming[0].float()
            green = self.inner.utils.get_greenlist_ids(input_ids[0]).to(row.device)
            top_id = torch.argmax(row).reshape(1)
            self.rows.append(
                {
                    "applied": bool((out[0] != incoming[0]).any()),
                    "top": float(row.max()),
                    "n_inf": int((~torch.isfinite(row)).sum()),
                    "gap": max(0.0, float(row.max()) - float(row[green].max())),
                    "top_green": bool(torch.isin(top_id, green)),
                    "green": green,
                }
            )
            return out

    for delta in ns.deltas:
        config = TransformersConfig(
            model=model,
            tokenizer=tokenizer,
            vocab_size=hp["vocab_size"],
            device=ns.device,
            generation_config=generation_config,
        )
        stone = STONEAutoWatermark.load(
            "STONE",
            transformers_config=config,
            skipping_rule=hp["skipping_rule"],
            watermark_on_pl=hp["watermark_on_pl"],
            gamma=hp["gamma"],
            delta=delta,
            hash_key=data["key"],
            z_threshold=hp["z_threshold"],
            prefix_length=hp["prefix_length"],
            language="python",
        )
        counts = dict.fromkeys(
            ("steps", "applied", "n_inf_steps", "top_green", "flippable", "chosen_green"), 0
        )
        ratios: list[float] = []
        gaps: list[float] = []
        for prompt in data["prompts"]:
            probe = Probe(stone.logits_processor)
            input_ids = tokenizer.apply_chat_template(
                prompt["messages"], add_generation_prompt=True, return_tensors="pt"
            ).to(ns.device)
            torch.manual_seed(prompt["seed"])
            torch.cuda.manual_seed_all(prompt["seed"])
            with torch.no_grad():
                gen = model.generate(
                    input_ids=input_ids,
                    attention_mask=torch.ones_like(input_ids),
                    generation_config=generation_config,
                    logits_processor=LogitsProcessorList([probe]),
                    return_dict_in_generate=True,
                    output_logits=True,
                )
            new_tokens = gen.sequences[0, input_ids.shape[1] :]
            for t, (row, raw) in enumerate(zip(probe.rows, gen.logits)):  # noqa: B905 (Py 3.9)
                counts["steps"] += 1
                raw_top = float(raw[0].float().max())
                if raw_top != 0:
                    ratios.append(row["top"] / raw_top)
                counts["n_inf_steps"] += int(row["n_inf"] > 0)
                if not row["applied"]:
                    continue
                counts["applied"] += 1
                gaps.append(row["gap"])
                counts["top_green"] += int(row["top_green"])
                counts["flippable"] += int(0 < row["gap"] < max(delta, 1e-9))
                if t < len(new_tokens):
                    chosen = new_tokens[t : t + 1]
                    counts["chosen_green"] += int(bool(torch.isin(chosen, row["green"])))
        report(delta, 1 / generation_config.temperature, counts, ratios, gaps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
