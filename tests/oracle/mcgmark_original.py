"""Percorso diretto di MCGMark per l'oracle (SPEC §21.4). Ambiente ``mcgmark`` (Python 3.10).

Si lancia due volte, con due copie del codice:

    # codice originale (solo la patch 0000: import e simboli mancanti), γ dinamico
    PYTHONPATH=build/oracle_src/mcgmark/Watermark <python di mcgmark> \\
        tests/oracle/mcgmark_original.py --variant original \\
        --inputs tests/fixtures/oracle/mcgmark/inputs.json \\
        --out tests/fixtures/oracle/mcgmark/original.json
    # codice usato dal framework (patch 0000-0002), γ = 0,5 fisso (D18)
    PYTHONPATH=build/patched/mcgmark/Watermark <python di mcgmark> \\
        tests/oracle/mcgmark_original.py --variant patched --replay-from .../original.json \\
        --inputs .../inputs.json --out tests/fixtures/oracle/mcgmark/patched.json

Varianti: ``original`` (sola 0000), ``nospeed`` (0000-0002) e ``patched`` (0000-0003); per il
codice lungo (``inputs_long.json``) si confrontano ``nospeed`` e ``patched`` con ``--no-forced``
(testo identico e tempi di generazione: speed-up della patch 0003).

Per ogni prompt (un campione, con seme e messaggio di 12 bit propri):
1. **generazione** come ``Watermark/watermark.py:225-234`` (``WatermarkLogitsProcessor`` nuovo,
   vocabolario ordinato per id) con prompt chat, decoding neutro, seme e chiave del framework;
   traccia per passo: γ scelto, posizione marcata prima e dopo, impronta dei logit restituiti;
2. **estrazione interna**: i bit che il processor stesso estrae dalle posizioni registrate durante
   la generazione (``First_watermark_token`` + ``detect(..., result_detection=True)``, come il
   blocco a ``TOKEN_LENGTH`` di ``__call__``) e i messaggi per ciclo (``detection_result``);
3. **forzatura del testo** (teacher forcing): il processor applicato passo per passo agli id di
   riferimento (i propri per ``original``, quelli di ``original.json`` per ``patched``) con i
   logit di un forward unico, così le due varianti si confrontano sugli stessi logit (test D18).
Poi l'**estrazione dal solo codice** (macchina a stati del detector + bit dalle green list)
sui testi generati e sui codici fissi, con il messaggio atteso di ciascuno.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import time
from collections import OrderedDict
from typing import Any, Dict, List

ROUND, INFO = 24, 12


def xor_decode(info_bits: str, coll_bits: str) -> str:
    return "".join("1" if a != b else "0" for a, b in zip(info_bits, coll_bits))


def decode_rounds(bits: str) -> List[str]:
    return [
        xor_decode(bits[r * ROUND : r * ROUND + INFO], bits[r * ROUND + INFO : (r + 1) * ROUND])
        for r in range(len(bits) // ROUND)
    ]


def fenced_code(text: str, prefill: str) -> str:
    """Codice del blocco aperto dal prefill, fino alla fence di chiusura (o alla fine).

    Per testi della forma prefill + codice + fence coincide con la regola D4; il test lo verifica
    con ``FencedCodeExtractor``.
    """
    body = text[len(prefill) :] if prefill and text.startswith(prefill) else text
    end = body.find("```")
    return body if end < 0 else body[:end]


def main() -> int:  # noqa: PLR0915 - script lineare: un passo per sezione del docstring
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["original", "nospeed", "patched"], required=True)
    parser.add_argument(
        "--no-forced",
        action="store_true",
        help="senza forzatura del testo né impronta dei logit (misura dei tempi, codice lungo)",
    )
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--replay-from", default=None)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--share", default="1/1", help="K/M: solo prompt e codici con indice i %% M == K - 1"
    )
    ns = parser.parse_args()
    os.environ["MCG_RESULT_DIR"] = tempfile.mkdtemp(prefix="mcg_oracle_")

    import torch
    import watermark_global as wg
    import watermark_processor as wp
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        GenerationConfig,
        LogitsProcessor,
        LogitsProcessorList,
    )

    with open(ns.inputs, encoding="utf-8") as handle:
        data = json.load(handle)
    part, parts = (int(x) for x in ns.share.split("/"))
    data["prompts"] = [p for i, p in enumerate(data["prompts"]) if i % parts == part - 1]
    data["codes"] = [c for i, c in enumerate(data["codes"]) if i % parts == part - 1]
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
    prefill = str(hp.get("assistant_prefill") or "")
    vocab_dict = tokenizer.get_vocab()
    vocab = list(OrderedDict(sorted(vocab_dict.items(), key=lambda x: x[1])).values())
    common = {
        "vocab": vocab,
        "gamma": hp["gamma"],
        "delta": hp["delta"],
        "seeding_scheme": hp["seeding_scheme"],
        "hash_key": data["key"],
        "select_green_tokens": hp["select_green_tokens"],
    }

    def reset(message: str) -> None:
        wg.First_watermark_token.clear()
        wg.Bich_kaiwen_First_watermark_token.clear()
        wg._global_dict.clear()
        wg._global_dict["water_round"] = 1
        wg._global_dict["is_ready_new_round"] = False
        wg.set_old_water_info(message)
        wg.set_waterinfo_12_global(None)

    class Traced(LogitsProcessor):  # type: ignore[misc]
        """Registra, per passo, γ, posizione marcata e impronta dei logit restituiti."""

        def __init__(self, inner: Any, digest: bool = True) -> None:
            self.inner = inner
            self.digest = digest
            self.steps: List[Dict[str, Any]] = []

        def __call__(self, input_ids: Any, scores: Any) -> Any:
            before = int(self.inner.tele_count)
            out = self.inner(input_ids, scores)
            digest = (
                hashlib.sha256(out.float().cpu().numpy().tobytes()).hexdigest()[:16]
                if self.digest
                else None
            )
            self.steps.append(
                {
                    "gamma": float(self.inner.gamma),
                    "tele_before": before,
                    "tele_after": int(self.inner.tele_count),
                    "digest": digest,
                }
            )
            return out

    def internal_bits(processor: Any) -> Dict[str, Any]:
        records = list(wg.First_watermark_token.items())
        tokens = [str(v[1]) for _, v in records]
        bits = ""
        if tokens:
            bits = processor.detect(
                tokens,
                ns.device,
                call_count_list=[v[2] for _, v in records],
                result_detection=True,
            )
        return {
            "embedded_bits": "".join(str(v[0]) for _, v in records),
            "tokens": tokens,
            "bits": bits,
            "rounds": decode_rounds(bits),
        }

    def extract(code: str, message: str) -> Dict[str, Any]:
        reset(message)
        detector = wp.WatermarkDetector(device=ns.device, tokenizer=tokenizer, **common)
        ids = tokenizer(code, add_special_tokens=False)["input_ids"]
        if ids and ids[0] == tokenizer.bos_token_id:
            ids = ids[1:]
        if prefill:  # stesso primo token della generazione (ultimo del prefill), come lo shim
            ids = [tokenizer(prefill, add_special_tokens=False)["input_ids"][-1], *ids]
        split_tokens = [tokenizer.decode(int(t), skip_special_tokens=False) for t in ids]
        detector._pseudo_generate_mask(split_tokens)
        records = list(wg.Bich_kaiwen_First_watermark_token.items())
        tokens = [str(v[1]) for _, v in records]
        bits = ""
        if tokens:
            bits = wp.WatermarkLogitsProcessor.detect(
                detector,
                tokens,
                ns.device,
                call_count_list=[v[2] for _, v in records],
                result_detection=True,
            )
        return {"tokens": tokens, "bits": bits, "rounds": decode_rounds(bits)}

    reference: Dict[str, List[int]] = {}
    if ns.replay_from:
        with open(ns.replay_from, encoding="utf-8") as handle:
            for g in json.load(handle)["generations"]:
                reference[g["problem_key"]] = g["new_ids"]

    log = io.StringIO()
    generations: List[Dict[str, Any]] = []
    for prompt in data["prompts"]:
        message = prompt["message"]
        input_ids = tokenizer.apply_chat_template(
            prompt["messages"], add_generation_prompt=True, return_tensors="pt"
        ).to(ns.device)
        if prefill:  # turno dell'assistente che inizia con la fence (D20)
            tail = tokenizer(prefill, add_special_tokens=False)["input_ids"]
            input_ids = torch.cat([input_ids, torch.tensor([tail], device=ns.device)], dim=1)
        # 1. generazione
        reset(message)
        traced = Traced(
            wp.WatermarkLogitsProcessor(tokenizer=tokenizer, **common), digest=not ns.no_forced
        )
        torch.manual_seed(prompt["seed"])
        torch.cuda.manual_seed_all(prompt["seed"])
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.no_grad(), contextlib.redirect_stdout(log):
            output = model.generate(
                input_ids=input_ids,
                attention_mask=torch.ones_like(input_ids),
                num_return_sequences=1,
                generation_config=generation_config,
                logits_processor=LogitsProcessorList([traced]),
            )
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            internal = internal_bits(traced.inner)
        new_ids = [int(t) for t in output[0, input_ids.shape[1] :].tolist()]
        new_text = (
            prefill
            + tokenizer.batch_decode(output[:, input_ids.shape[1] :], skip_special_tokens=True)[0]
        )
        # 3. forzatura del testo di riferimento sugli stessi logit
        forced_steps: List[Dict[str, Any]] = []
        if not ns.no_forced:
            ref_ids = reference.get(prompt["problem_key"], new_ids)
            reset(message)
            forced = Traced(wp.WatermarkLogitsProcessor(tokenizer=tokenizer, **common))
            full = torch.cat([input_ids, torch.tensor([ref_ids], device=ns.device)], dim=1)
            with torch.no_grad(), contextlib.redirect_stdout(log):
                logits = model(full).logits[0].float()
                start = input_ids.shape[1]
                for t in range(len(ref_ids)):
                    forced(full[:, : start + t], logits[start + t - 1].unsqueeze(0).clone())
            forced_steps = forced.steps
        generations.append(
            {
                "problem_key": prompt["problem_key"],
                "seed": prompt["seed"],
                "message": message,
                "new_ids": new_ids,
                "new_text": new_text,
                "steps": traced.steps,
                "internal": internal,
                "forced_steps": forced_steps,
                "elapsed_s": round(elapsed, 3),
            }
        )

    # 4. estrazione dal solo codice
    codes = list(data["codes"]) + [
        {
            "id": f"generated:{g['problem_key']}",
            "language": "python",
            "code": fenced_code(g["new_text"], prefill),
            "messages": p["messages"],
            "message": g["message"],
        }
        for g, p in zip(generations, data["prompts"])
    ]
    detections: List[Dict[str, Any]] = []
    with contextlib.redirect_stdout(log):
        for item in codes:
            detections.append(
                {
                    "id": item["id"],
                    "language": item["language"],
                    "code": item["code"],
                    "messages": item["messages"],
                    "message": item["message"],
                    **extract(item["code"], item["message"]),
                }
            )
    out = {
        "variant": ns.variant,
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
        f"wrote {ns.out} ({ns.variant}): {len(generations)} generations, "
        f"{len(detections)} detections\n"
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
