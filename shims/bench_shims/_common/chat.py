"""Prompt chat dei worker (SPEC §8.4). Compatibile con Python 3.9.

I messaggi arrivano già costruiti dall'orchestratore (``PromptBuilder``, stesso system
prompt e template della baseline): qui si applica solo il chat template nativo.
"""

from __future__ import annotations

from typing import Any, Dict, List


def encode_chat(tokenizer: Any, messages: List[Dict[str, str]], device: str) -> Any:
    """``input_ids`` del prompt con ``add_generation_prompt=True`` (come la baseline)."""
    return tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt"
    ).to(device)


def decode_new_tokens(tokenizer: Any, output: Any, prompt_length: int) -> List[str]:
    """Solo i token generati dopo il prompt, senza token speciali (come la baseline)."""
    new_tokens = output[:, prompt_length:]
    return [str(t) for t in tokenizer.batch_decode(new_tokens, skip_special_tokens=True)]
