"""Shim di STONE (SPEC §9.3; audit ``docs/audit/stone.md``). Compatibile con Python 3.9.

Gira nell'ambiente ``stone`` con ``PYTHONPATH`` = ``shims/`` +
``build/patched/stone/stone_implementation`` (gli import del repository sono assoluti:
``watermark``, ``utils``, ``visualize``, ``exceptions``).

- **embed**: il processor ufficiale (``STONE.logits_processor``, con la patch 0001 per riga)
  passato a ``model.generate`` con il prompt chat del framework, il decoding neutro del request,
  il seme del problema e ``num_return_sequences = n``;
- **detect**: ``STONE.detect_watermark(code)`` dà lo ``score`` ufficiale; ``score_sequence``
  dà flag e pesi per lo z-score con il denominatore del paper (``z_nonsyntax`` in ``extra``,
  decisione dell'utente, audit §5).

La green list usa il vocabolario del generatore (``vocab_size=None`` → ``len(tokenizer)``,
audit §7). Una istanza ``STONE`` per linguaggio; il modello si carica una volta.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import DetectStatus, EmbedStatus

from bench_shims._common.base import ShimBase
from bench_shims._common.chat import decode_new_tokens, encode_chat
from bench_shims._common.decoding import (
    build_generation_config,
    effective_config,
    set_seed,
    torch_dtype,
)
from bench_shims._common.runner import main

SUPPORTED_LANGUAGES = ("python", "cpp", "java")


def z_nonsyntax(n_green: int, n_nonsyntax: int, gamma: float) -> Optional[float]:
    """Z-score del paper (Algoritmo 2): ``(N_GE - g*N_E) / sqrt(g(1-g)*N_E)``.

    ``None`` se N_E < 1.
    """
    if n_nonsyntax < 1:
        return None
    return (n_green - gamma * n_nonsyntax) / math.sqrt(gamma * (1 - gamma) * n_nonsyntax)


class StoneShim(ShimBase):
    """Inserimento e rilevazione con l'implementazione ufficiale di STONE."""

    method = "stone"

    def setup(self, request: WorkerRequest) -> None:
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.request = request
        self.hp = dict(request.hparams)
        self.device = request.device
        self.tokenizer = AutoTokenizer.from_pretrained(
            request.tokenizer_path, local_files_only=True
        )
        self.model = None
        if request.op == "embed":
            self.model = AutoModelForCausalLM.from_pretrained(
                request.model_path, torch_dtype=torch_dtype(request.decoding), local_files_only=True
            ).to(self.device)
            self.model.eval()
            self.generation_config = build_generation_config(
                request.decoding, self.tokenizer, self.model
            )
        self._instances: Dict[str, Any] = {}
        self._logged_config = False

    def stone(self, language: str) -> Any:
        """Istanza ``STONE`` del linguaggio, costruita come in ``run.py`` (salvo il vocabolario)."""
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"STONE does not support {language!r} (audit §4)")
        if language not in self._instances:
            from utils.transformers_config import TransformersConfig
            from watermark.auto_watermark import STONEAutoWatermark

            config = TransformersConfig(
                model=self.model,
                tokenizer=self.tokenizer,
                vocab_size=self.hp.get("vocab_size"),  # None -> len(tokenizer) (audit §7)
                device=self.device,
            )
            self._instances[language] = STONEAutoWatermark.load(
                "STONE",
                transformers_config=config,
                skipping_rule=self.hp["skipping_rule"],
                watermark_on_pl=self.hp["watermark_on_pl"],
                gamma=float(self.hp["gamma"]),
                delta=float(self.hp["delta"]),
                hash_key=int(self.request.key),
                z_threshold=float(self.hp["z_threshold"]),
                prefix_length=int(self.hp["prefix_length"]),
                language=language,
            )
        return self._instances[language]

    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        import torch
        from transformers import LogitsProcessorList

        stone = self.stone(item.language)
        input_ids = encode_chat(self.tokenizer, item.prompt_messages or [], self.device)
        set_seed(item.seed)
        with torch.no_grad():
            output = self.model.generate(  # type: ignore[union-attr]
                input_ids=input_ids,
                attention_mask=torch.ones_like(input_ids),
                generation_config=self.generation_config,
                logits_processor=LogitsProcessorList([stone.logits_processor]),
                num_return_sequences=item.n,
            )
        texts = decode_new_tokens(self.tokenizer, output, input_ids.shape[1])
        extra: Dict[str, Any] = {"vocab_size": stone.config.vocab_size}
        if not self._logged_config:
            # Configurazione effettiva (SPEC §21.4 punto 4), una volta per worker.
            extra["generation_config"] = effective_config(self.generation_config)
            self._logged_config = True
        return [
            self.result(
                item, EmbedStatus.OK, sample_index=i, raw_output=text, extra=extra if i == 0 else {}
            )
            for i, text in enumerate(texts)
        ]

    def detect(self, item: WorkerItem) -> WorkerResult:
        stone = self.stone(item.language)
        code = item.code or ""
        try:
            official = stone.detect_watermark(code)  # z-score del codice ufficiale (audit §5)
        except ValueError as exc:  # nessun token sintattico: T < 1 (audit §10)
            return self.result(item, DetectStatus.FAILED, error=f"ValueError: {exc}")
        encoded = self.tokenizer(code, return_tensors="pt", add_special_tokens=False)["input_ids"][
            0
        ]
        _, flags, weights = stone.utils.score_sequence(encoded.to(stone.config.device))
        prefix = int(self.hp["prefix_length"])
        n_nonsyntax = sum(1 for w in weights[prefix:] if w == 1)
        n_green = sum(1 for f, w in zip(flags, weights) if f == 1 and w == 1)
        n_syntax = len(encoded) - prefix - n_nonsyntax
        gamma = float(self.hp["gamma"])
        return self.result(
            item,
            DetectStatus.OK,
            score=float(official["score"]),
            native_decision=bool(official["is_watermarked"]),
            extra={
                "z_nonsyntax": z_nonsyntax(n_green, n_nonsyntax, gamma),
                "n_tokens": len(encoded),
                "n_syntax": n_syntax,
                "n_nonsyntax": n_nonsyntax,
                "n_green_nonsyntax": n_green,
                "vocab_size": stone.config.vocab_size,
            },
        )


if __name__ == "__main__":
    raise SystemExit(main(StoneShim))
