"""Equivalenza per riga del processor di STONE (patch 0001; SPEC §21.4). Ambiente ``stone``.

    PYTHONPATH=build/patched/stone/stone_implementation \\
        <python di stone> tests/oracle/stone_rowwise.py \\
        --tokenizer <percorso del modello> [--device cuda:0]

Per Python, C++ e Java: un blocco di 6 sequenze in cui l'argmax di 3 righe è un token sintattico
e quello delle altre 3 no. Il processor applicato al blocco deve dare, riga per riga, gli stessi
logit modificati (e quindi la stessa decisione) del processor applicato alla riga da sola.
Stampa un JSON su stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# Token il cui argmax rende la riga sintattica (non marcata) o no, per linguaggio.
SYNTAX = {
    "python": ["def", " (", " return"],
    "cpp": [" int", " {", " return"],
    "java": [" public", " {", " return"],
}
OTHER = {
    "python": [" foo", " numbers", " total"],
    "cpp": [" foo", " numbers", " total"],
    "java": [" foo", " numbers", " total"],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--vocab-width", type=int, default=0, help="width of the logits (0: tokenizer)"
    )
    ns = parser.parse_args()

    import torch
    from transformers import AutoTokenizer
    from utils.transformers_config import TransformersConfig
    from watermark.auto_watermark import STONEAutoWatermark

    tokenizer = AutoTokenizer.from_pretrained(ns.tokenizer, local_files_only=True)
    width = ns.vocab_width or len(tokenizer)
    report: dict[str, Any] = {"vocab_width": width, "languages": {}}
    ok = True
    for language in ("python", "cpp", "java"):
        config = TransformersConfig(
            model=None, tokenizer=tokenizer, vocab_size=None, device=ns.device
        )
        processor = STONEAutoWatermark.load(
            "STONE",
            transformers_config=config,
            skipping_rule="all_pl",
            watermark_on_pl="False",
            gamma=0.5,
            delta=1.0,
            hash_key=12345,
            z_threshold=10.0,
            prefix_length=0,
            language=language,
        ).logits_processor
        targets: list[int] = []
        expected_biased: list[bool] = []
        for syn, oth in zip(SYNTAX[language], OTHER[language]):
            targets += [
                tokenizer.encode(syn, add_special_tokens=False)[0],
                tokenizer.encode(oth, add_special_tokens=False)[0],
            ]
            expected_biased += [False, True]
        generator = torch.Generator().manual_seed(0)
        ids = torch.randint(0, len(tokenizer), (6, 16), generator=generator).to(ns.device)
        scores = torch.randn(6, width, generator=generator).to(ns.device)
        for row, token in enumerate(targets):
            scores[row, token] = 50.0  # argmax della riga
        batched = processor(ids.clone(), scores.clone())
        single = torch.cat(
            [processor(ids[i : i + 1].clone(), scores[i : i + 1].clone()) for i in range(6)]
        )
        biased = [bool((batched[i] - scores[i]).abs().sum().item() > 0) for i in range(6)]
        equal = bool(torch.equal(batched, single))
        report["languages"][language] = {
            "rows_equal": equal,
            "biased": biased,
            "expected_biased": expected_biased,
            "targets": [tokenizer.decode([t]) for t in targets],
        }
        ok = ok and equal and biased == expected_biased
    report["ok"] = ok
    sys.stdout.write(json.dumps(report) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
