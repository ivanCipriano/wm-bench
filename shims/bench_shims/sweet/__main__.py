"""Shim di SWEET (SPEC §9.1; audit ``docs/audit/sweet.md``). Compatibile con Python 3.9.

Gira nell'ambiente ``sweet`` con ``PYTHONPATH`` = ``shims/`` + ``build/patched/sweet``.
Importa **solo** ``sweet.py`` e ``watermark.py`` del repository: ``lm_eval`` tira dentro
dipendenze inutili (Cython, ``fcntl``, ``mosestokenizer``) e ``evaluator.py`` contiene
``pdb.set_trace()``.

- **embed**: ``SweetLogitsProcessor`` costruito come in ``lm_eval/generation.py:74-78``
  (``vocab = list(tokenizer.get_vocab().values())``) e passato a ``model.generate`` con il
  prompt chat del framework, il decoding neutro del request, il seme del problema e
  ``num_return_sequences = n``;
- **detect**: i passi di ``lm_eval/evaluator.py:130-170``: entropia per token con un forward
  del modello (``calculate_entropy``), spostata di una posizione (``[0] + entropy[:-1]``),
  poi ``SweetDetector.detect`` con ``prefix_len`` = lunghezza del contesto.

Contesto della rilevazione (SPEC §9.1): gli id del prompt chat (``item.prompt_messages``,
gli stessi della generazione), oppure ``item.context_prompt`` come testo, oppure vuoto (D3).
Il codice valutato è quello estratto (D4).
"""

from __future__ import annotations

from typing import Any, Dict, List

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


def calculate_entropy(model: Any, tokenized_text: Any) -> List[float]:
    """Copia di ``lm_eval/utils.py::calculate_entropy`` (il modulo non si importa: vedi sopra)."""
    import torch

    with torch.no_grad():
        output = model(torch.unsqueeze(tokenized_text, 0), return_dict=True)
        probs = torch.softmax(output.logits, dim=-1)
        entropy = -torch.where(probs > 0, probs * probs.log(), probs.new([0.0])).sum(dim=-1)
        return entropy[0].cpu().tolist()


class SweetShim(ShimBase):
    """Inserimento e rilevazione con l'implementazione ufficiale di SWEET."""

    method = "sweet"

    def setup(self, request: WorkerRequest) -> None:
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.request = request
        self.hp = dict(request.hparams)
        self.device = request.device
        self.tokenizer = AutoTokenizer.from_pretrained(
            request.tokenizer_path, local_files_only=True
        )
        # Il modello serve anche in rilevazione: l'entropia richiede un forward (audit §2).
        self.model = AutoModelForCausalLM.from_pretrained(
            request.model_path, torch_dtype=torch_dtype(request.decoding), local_files_only=True
        ).to(self.device)
        self.model.eval()
        self.vocab = list(self.tokenizer.get_vocab().values())
        self.generation_config = None
        if request.op == "embed":
            self.generation_config = build_generation_config(
                request.decoding, self.tokenizer, self.model
            )
        self._logged_config = False

    def _common(self) -> Dict[str, Any]:
        return {
            "vocab": self.vocab,
            "gamma": float(self.hp["gamma"]),
            "delta": float(self.hp["delta"]),
            "seeding_scheme": self.hp["seeding_scheme"],
            "hash_key": int(self.request.key),
            "select_green_tokens": bool(self.hp["select_green_tokens"]),
            "entropy_threshold": float(self.hp["entropy_threshold"]),
        }

    def processor(self) -> Any:
        """``SweetLogitsProcessor`` nuovo (il generatore casuale si crea alla prima chiamata)."""
        from sweet import SweetLogitsProcessor

        return SweetLogitsProcessor(**self._common())

    def detector(self) -> Any:
        """``SweetDetector`` come in ``lm_eval/evaluator.py:247-251`` (chiave del framework)."""
        from sweet import SweetDetector

        return SweetDetector(
            tokenizer=self.tokenizer, z_threshold=float(self.hp["z_threshold"]), **self._common()
        )

    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        import torch
        from transformers import LogitsProcessorList

        input_ids = encode_chat(self.tokenizer, item.prompt_messages or [], self.device)
        set_seed(item.seed)
        with torch.no_grad():
            output = self.model.generate(
                input_ids=input_ids,
                attention_mask=torch.ones_like(input_ids),
                generation_config=self.generation_config,
                logits_processor=LogitsProcessorList([self.processor()]),
                num_return_sequences=item.n,
            )
        texts = decode_new_tokens(self.tokenizer, output, input_ids.shape[1])
        extra: Dict[str, Any] = {"vocab_size": len(self.vocab)}
        if not self._logged_config:
            extra["generation_config"] = effective_config(self.generation_config)
            self._logged_config = True
        return [
            self.result(
                item, EmbedStatus.OK, sample_index=i, raw_output=text, extra=extra if i == 0 else {}
            )
            for i, text in enumerate(texts)
        ]

    def context_ids(self, item: WorkerItem) -> List[int]:
        """Id del contesto: prompt chat (come in generazione), testo libero o vuoto (D3)."""
        if item.prompt_messages:
            return list(
                self.tokenizer.apply_chat_template(item.prompt_messages, add_generation_prompt=True)
            )
        if item.context_prompt:
            return list(self.tokenizer(item.context_prompt, add_special_tokens=False)["input_ids"])
        return []

    def detect(self, item: WorkerItem) -> WorkerResult:
        import torch

        prefix = self.context_ids(item)
        code_ids = list(self.tokenizer(item.code or "", add_special_tokens=False)["input_ids"])
        tokenized_text = torch.tensor(prefix + code_ids, dtype=torch.long)
        tokenized_prefix = torch.tensor(prefix, dtype=torch.long)
        if len(code_ids) == 0:
            return self.result(
                item, DetectStatus.FAILED, error="no code tokens", extra={"n_tokens": 0}
            )
        entropy = calculate_entropy(self.model, tokenized_text.to(self.device))
        entropy = [0] + entropy[
            :-1
        ]  # come evaluator.py: entropy[i] = entropia che ha generato il token i
        result = self.detector().detect(
            tokenized_text=tokenized_text, tokenized_prefix=tokenized_prefix, entropy=entropy
        )
        extra = {
            "n_tokens": len(code_ids),
            "prefix_len": len(prefix),
            "num_tokens_scored": result.get("num_tokens_scored"),
            "num_green_tokens": result.get("num_green_tokens"),
        }
        if result.get("invalid"):
            return self.result(
                item, DetectStatus.FAILED, error="no generated tokens to score", extra=extra
            )
        z = float(result["z_score"])
        if result.get("num_tokens_scored") == 0:
            # Nessun token sopra la soglia di entropia: il repository restituisce -100 (audit §10).
            extra["z_score_raw"] = z
            return self.result(
                item, DetectStatus.FAILED, error="no token above the entropy threshold", extra=extra
            )
        return self.result(
            item,
            DetectStatus.OK,
            score=z,
            native_decision=bool(result.get("prediction")),
            extra=extra,
        )


if __name__ == "__main__":
    raise SystemExit(main(SweetShim))
