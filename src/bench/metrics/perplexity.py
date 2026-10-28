"""NLL per token con un modello osservatore (SPEC §13.4; ADR-015): ``StarCoder2-7B``, bf16, solo il
codice (senza prompt), troncamento a 2.048 token.

Il codice si tokenizza senza token speciali e si antepone il token BOS del tokenizer (per
StarCoder2 ``<|endoftext|>``), così anche il primo token del codice ha un contesto e viene contato:
NLL medio = somma delle log-verosimiglianze negative dei token del codice / numero di token del
codice (al più 2.047 dopo il BOS). Il codice vuoto non si valuta (``nll`` = NaN, D37).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MAX_TOKENS = 2048


def token_nll(
    model: Any, tokenizer: Any, codes: Sequence[str], batch_size: int = 8, device: str = "cuda:0"
) -> list[tuple[float, int]]:
    """``(NLL medio per token, token valutati)`` per ogni codice, nello stesso ordine."""
    import torch

    bos = tokenizer.bos_token_id if tokenizer.bos_token_id is not None else tokenizer.eos_token_id
    pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else bos
    out: list[tuple[float, int]] = [(float("nan"), 0)] * len(codes)
    todo = [i for i, c in enumerate(codes) if c and c.strip()]
    ids = {
        i: [bos, *tokenizer(codes[i], add_special_tokens=False)["input_ids"]][:MAX_TOKENS]
        for i in todo
    }
    todo.sort(key=lambda i: len(ids[i]))  # lotti di lunghezza simile
    model.eval()
    for s in range(0, len(todo), batch_size):
        batch = todo[s : s + batch_size]
        width = max(len(ids[i]) for i in batch)
        input_ids = torch.full((len(batch), width), pad, dtype=torch.long)
        mask = torch.zeros((len(batch), width), dtype=torch.long)
        for r, i in enumerate(batch):
            input_ids[r, : len(ids[i])] = torch.tensor(ids[i])
            mask[r, : len(ids[i])] = 1
        input_ids, mask = input_ids.to(device), mask.to(device)
        with torch.no_grad():
            logits = model(input_ids=input_ids, attention_mask=mask).logits.float()
        logp = torch.log_softmax(logits[:, :-1], dim=-1)
        target = input_ids[:, 1:]
        nll = -logp.gather(-1, target.unsqueeze(-1)).squeeze(-1) * mask[:, 1:]
        for r, i in enumerate(batch):
            n = int(mask[r, 1:].sum())
            out[i] = (float(nll[r].sum()) / n, n) if n else (float("nan"), 0)
    return out
