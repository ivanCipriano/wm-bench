"""LLM locale degli attacchi T2.2 e T2.3 (SPEC §12): ``Llama-3.1-8B-Instruct`` dalla snapshot.

Decodifica greedy (temperatura 0) a lotti, con padding a sinistra; il testo si ricostruisce con
``clean_up_tokenization_spaces=False``: il tokenizer di Llama 3.1 lo ha a ``True`` in tutti gli
ambienti e toglierebbe lo spazio prima di ``!``, ``?``, ``,``, ``.`` anche nel codice e nelle
stringhe (``a != b`` → ``a!= b``; controllo del 24 ottobre 2026,
``M11_check_tokenizer_cleanup.txt``). Il risultato dipende dalla composizione dei lotti, che è
deterministica (ordine dei campioni della cella).
"""

from __future__ import annotations

import logging
import platform
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from bench.config.schema import ModelSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Completion:
    """Testo generato per un prompt e numero di token nuovi (troncato se = ``max_new_tokens``)."""

    text: str
    new_tokens: int
    truncated: bool


class LocalLLM:
    """Modello e tokenizer locali (offline), caricati alla prima generazione.

    Args:
        spec: modello (``path`` = snapshot della cache HF).
        max_new_tokens: limite dei token generati per prompt.
        batch_size: prompt per chiamata a ``generate``.
        device: dispositivo (default: ``cuda``).
    """

    def __init__(
        self, spec: ModelSpec, max_new_tokens: int, batch_size: int, device: str = "cuda"
    ) -> None:
        self.spec = spec
        self.max_new_tokens = max_new_tokens
        self.batch_size = batch_size
        self.device = device
        self._model: Any = None
        self._tokenizer: Any = None
        self._generation_config: Any = None

    def _load(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig

        dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}[
            self.spec.dtype
        ]
        tokenizer = AutoTokenizer.from_pretrained(
            str(self.spec.tokenizer_path), local_files_only=True
        )
        tokenizer.padding_side = "left"
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            str(self.spec.path), torch_dtype=dtype, local_files_only=True
        ).to(self.device)
        model.eval()
        self._generation_config = GenerationConfig(
            do_sample=False,  # temperatura 0: decodifica greedy
            num_beams=1,
            max_new_tokens=self.max_new_tokens,
            repetition_penalty=1.0,
            eos_token_id=model.generation_config.eos_token_id,
            pad_token_id=tokenizer.pad_token_id,
        )
        model.generation_config = self._generation_config  # niente default del modello
        self._model, self._tokenizer = model, tokenizer
        logger.info("attacker %s loaded on %s", self.spec.model_id, self.device)

    def generate(self, conversations: Sequence[list[dict[str, str]]]) -> list[Completion]:
        """Una risposta per conversazione, nell'ordine dato."""
        import torch

        if self._model is None:
            self._load()
        out: list[Completion] = []
        for start in range(0, len(conversations), self.batch_size):
            batch = list(conversations[start : start + self.batch_size])
            texts = [
                self._tokenizer.apply_chat_template(m, add_generation_prompt=True, tokenize=False)
                for m in batch
            ]
            enc = self._tokenizer(
                texts, return_tensors="pt", padding=True, add_special_tokens=False
            ).to(self.device)
            with torch.no_grad():
                ids = self._model.generate(**enc, generation_config=self._generation_config)
            new = ids[:, enc["input_ids"].shape[1] :]
            for row in new:
                n = int((row != self._tokenizer.pad_token_id).sum())
                text = self._tokenizer.decode(
                    row, skip_special_tokens=True, clean_up_tokenization_spaces=False
                )
                out.append(Completion(text, n, n >= self.max_new_tokens))
        return out

    def info(self) -> dict[str, Any]:
        """Modello, snapshot e decoding (per il manifest)."""
        import transformers

        info: dict[str, Any] = {
            "model_id": self.spec.model_id,
            "hf_repo": self.spec.hf_repo,
            "snapshot": self.spec.snapshot,
            "dtype": self.spec.dtype,
            "decoding": {
                "do_sample": False,
                "num_beams": 1,
                "max_new_tokens": self.max_new_tokens,
                "batch_size": self.batch_size,
                "padding_side": "left",
                "clean_up_tokenization_spaces": False,
            },
            "transformers": transformers.__version__,
            "python": platform.python_version(),
        }
        if self._model is not None:
            import torch

            info["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        return info
