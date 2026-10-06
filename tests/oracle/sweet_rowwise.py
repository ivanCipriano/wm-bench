"""Equivalenza per riga del processor di SWEET (cluster_info §11; SPEC §21.4). Ambiente ``sweet``.

    PYTHONPATH=build/patched/sweet <python di sweet> tests/oracle/sweet_rowwise.py \\
        --tokenizer <percorso del modello> --vocab-width <larghezza dei logit>

Un blocco di 6 sequenze: 3 righe con distribuzione piatta (entropia alta: il bias si applica)
e 3 righe con un logit dominante (entropia bassa: nessun bias). Il processor applicato al
blocco deve dare, riga per riga, gli stessi logit del processor applicato alla riga da sola.
Il generatore casuale del processor è su CPU: si prova anche con i tensori su GPU.
Stampa un JSON su stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--vocab-width", type=int, default=0)
    parser.add_argument("--device", default="cuda:0")
    ns = parser.parse_args()

    import torch
    from sweet import SweetLogitsProcessor
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(ns.tokenizer, local_files_only=True)
    vocab = list(tokenizer.get_vocab().values())
    width = ns.vocab_width or len(vocab)
    report: dict[str, Any] = {"vocab_size": len(vocab), "vocab_width": width, "devices": {}}
    ok = True
    for device in ("cpu", ns.device):
        generator = torch.Generator().manual_seed(0)
        ids = torch.randint(0, len(vocab), (6, 16), generator=generator)
        scores = torch.randn(6, width, generator=generator) * 0.01  # piatte: entropia alta
        for row in (1, 3, 5):
            scores[row, int(ids[row, -1])] = 60.0  # un logit dominante: entropia ~0
        ids, scores = ids.to(device), scores.to(device)

        def fresh() -> Any:
            return SweetLogitsProcessor(
                vocab=vocab, gamma=0.5, delta=0.5, entropy_threshold=0.5, hash_key=12345
            )

        batched = fresh()(ids.clone(), scores.clone())
        single = torch.cat(
            [fresh()(ids[i : i + 1].clone(), scores[i : i + 1].clone()) for i in range(6)]
        )
        biased = [bool((batched[i] - scores[i]).abs().sum().item() > 0) for i in range(6)]
        equal = bool(torch.equal(batched, single))
        expected = [True, False, True, False, True, False]
        report["devices"][device] = {
            "rows_equal": equal,
            "biased": biased,
            "expected_biased": expected,
        }
        ok = ok and equal and biased == expected
    report["ok"] = ok
    sys.stdout.write(json.dumps(report) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
