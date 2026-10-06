"""Shim di MCGMark (SPEC §9.5; audit ``docs/audit/mcgmark.md``). Compatibile con Python 3.9.

Gira nell'ambiente ``mcgmark`` con ``PYTHONPATH`` = ``shims/`` + ``build/patched/mcgmark/Watermark``
(patch 0000 degli import e dei simboli mancanti, 0001 γ fisso, 0002 senza decodifica inutile
del vocabolario).

Il processor non supporta più sequenze per volta e tiene stato in variabili globali di modulo
(``First_watermark_token``, ``Bich_kaiwen_First_watermark_token``, ``_global_dict``): ogni item
è **un solo campione** (l'adapter manda un item per campione, con seme e messaggio propri) e lo
stato globale si azzera prima di ogni campione e di ogni rilevazione.

- **embed**: ``WatermarkLogitsProcessor`` costruito come in ``Watermark/watermark.py:225-234``
  (vocabolario ordinato per id), messaggio di 12 bit impostato con ``set_old_water_info``,
  ``model.generate`` con il prompt chat, il decoding neutro e il seme del campione. Il turno
  dell'assistente inizia con ``hparams.assistant_prefill`` (fence di apertura, D20): i suoi
  token stanno nel prompt, non vengono generati, e ``raw_output`` è prefill + testo generato.
  Stato: ``OK`` con almeno un ciclo completo (24 posizioni marcate, come ``dww`` del
  repository), ``PARTIAL`` altrimenti (anche con 0 posizioni).
  Con ``hparams.watermark = False`` (baseline gemella) la generazione è identica ma senza
  processor: stato ``OK``.
- **detect**: estrazione dal solo codice composta da due parti del repository:
  ``WatermarkDetector._pseudo_generate_mask`` (la macchina a stati rigiocata sui token, che
  ricostruisce le posizioni marcate) e ``WatermarkLogitsProcessor.detect`` (i bit dalle green
  list per posizione); poi, per ciclo di 24, ``informazione XOR correzione`` come la funzione
  ``detection_result`` interna a ``__call__``. Punteggio = bit del primo ciclo uguali al
  messaggio atteso (0–12, D1).
"""

from __future__ import annotations

import contextlib
import io
import os
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

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

ROUND = 24  # posizioni per ciclo: 12 di informazione + 12 di correzione (paper §4.5)
INFO = 12


def xor_decode(info_bits: str, coll_bits: str) -> str:
    """Decodifica di un ciclo, come ``detection_result`` in ``__call__`` del processor."""
    return "".join("1" if a != b else "0" for a, b in zip(info_bits, coll_bits))


def decode_rounds(bits: str) -> List[str]:
    """Messaggi decodificati dai cicli completi di 24 bit."""
    return [
        xor_decode(bits[r * ROUND : r * ROUND + INFO], bits[r * ROUND + INFO : (r + 1) * ROUND])
        for r in range(len(bits) // ROUND)
    ]


def matches(a: str, b: str) -> int:
    """Bit uguali in posizione."""
    return sum(1 for x, y in zip(a, b) if x == y)


class McgmarkShim(ShimBase):
    """Inserimento e rilevazione con l'implementazione di MCGMark."""

    method = "mcgmark"

    def setup(self, request: WorkerRequest) -> None:
        # Variabili lette da watermark_global all'import (patch 0000): risultati del repository
        # nella cartella di lavoro.
        run_dir = os.path.dirname(os.path.abspath(request.output_path))
        os.environ["MCG_RESULT_DIR"] = os.path.join(run_dir, "mcgmark_results")
        os.makedirs(os.environ["MCG_RESULT_DIR"], exist_ok=True)
        import watermark_global
        import watermark_processor
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.wg = watermark_global
        self.wp = watermark_processor
        self.request = request
        self.hp = dict(request.hparams)
        self.device = request.device
        self.tokenizer = AutoTokenizer.from_pretrained(
            request.tokenizer_path, local_files_only=True
        )
        vocab_dict = self.tokenizer.get_vocab()
        # Come watermark.py:225-228: id del vocabolario in ordine.
        self.vocab = list(OrderedDict(sorted(vocab_dict.items(), key=lambda x: x[1])).values())
        self.model = None
        self.generation_config = None
        if request.op == "embed":
            self.model = AutoModelForCausalLM.from_pretrained(
                request.model_path, torch_dtype=torch_dtype(request.decoding), local_files_only=True
            ).to(self.device)
            self.model.eval()
            self.generation_config = build_generation_config(
                request.decoding, self.tokenizer, self.model
            )
        self._logged_config = False

    # ------------------------------------------------------------------ stato globale
    def reset_state(self, message: str) -> None:
        """Azzera lo stato globale di modulo del repository e imposta il messaggio di 12 bit."""
        self.wg.First_watermark_token.clear()
        self.wg.Bich_kaiwen_First_watermark_token.clear()
        del self.wg.second_watermark_token[:]
        self.wg._global_dict.clear()
        self.wg._global_dict["water_round"] = 1
        self.wg._global_dict["is_ready_new_round"] = False
        self.wg.set_old_water_info(message)
        self.wg.set_waterinfo_12_global(None)

    def _common(self) -> Dict[str, Any]:
        return {
            "vocab": self.vocab,
            "gamma": float(self.hp["gamma"]),
            "delta": float(self.hp["delta"]),  # ignorato dal repository (bias = scarto dei logit)
            "seeding_scheme": self.hp["seeding_scheme"],
            "hash_key": int(self.request.key),
            "select_green_tokens": bool(self.hp["select_green_tokens"]),
        }

    @staticmethod
    def _message(item: WorkerItem) -> str:
        message = item.expected_message or ""
        if len(message) != INFO or set(message) - {"0", "1"}:
            raise ValueError(f"expected_message must be 12 bits, got {message!r}")
        return message

    # ------------------------------------------------------------------ inserimento
    def prompt_ids(self, item: WorkerItem, prefill: str) -> Any:
        """Prompt chat con ``add_generation_prompt`` seguito dai token del prefill."""
        import torch

        input_ids = encode_chat(self.tokenizer, item.prompt_messages or [], self.device)
        if not prefill:
            return input_ids
        ids = self.tokenizer(prefill, add_special_tokens=False)["input_ids"]
        tail = torch.tensor([ids], dtype=input_ids.dtype, device=input_ids.device)
        return torch.cat([input_ids, tail], dim=1)

    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        import torch
        from transformers import LogitsProcessorList

        if item.n != 1:
            raise ValueError("MCGMark generates one sample per item (n must be 1)")
        message = self._message(item)
        watermark = bool(self.hp.get("watermark", True))
        prefill = str(self.hp.get("assistant_prefill") or "")
        self.reset_state(message)
        processors = []
        if watermark:
            processors.append(
                self.wp.WatermarkLogitsProcessor(tokenizer=self.tokenizer, **self._common())
            )
        input_ids = self.prompt_ids(item, prefill)
        set_seed(item.seed)
        log = io.StringIO()
        with torch.no_grad(), contextlib.redirect_stdout(log):  # il repository stampa a ogni token
            output = self.model.generate(  # type: ignore[union-attr]
                input_ids=input_ids,
                attention_mask=torch.ones_like(input_ids),
                generation_config=self.generation_config,
                logits_processor=LogitsProcessorList(processors),
                num_return_sequences=1,
            )
        text = prefill + decode_new_tokens(self.tokenizer, output, input_ids.shape[1])[0]
        records = list(self.wg.First_watermark_token.items()) if watermark else []
        bits = "".join(str(v[0]) for _, v in records)
        if not watermark:
            status = EmbedStatus.OK
        else:
            status = EmbedStatus.OK if len(records) >= ROUND else EmbedStatus.PARTIAL
        extra: Dict[str, Any] = {
            "message": message,
            "watermark": watermark,
            "n_embedded": len(records),
            "embedded_bits": bits,
            "embedded_tokens": [str(v[1]) for _, v in records],
            "n_generated_tokens": int(output.shape[1] - input_ids.shape[1]),
            "seed_scheme": "per_sample",
        }
        if not self._logged_config:
            extra["generation_config"] = effective_config(self.generation_config)
            self._logged_config = True
        return [self.result(item, status, sample_index=0, raw_output=text, extra=extra)]

    # ------------------------------------------------------------------ rilevazione
    def extract(self, code: str, message: str) -> Tuple[str, List[str], List[str]]:
        """Bit per posizione marcata, messaggi per ciclo e token marcati ricostruiti dal codice."""
        self.reset_state(message)
        detector = self.wp.WatermarkDetector(
            device=self.device, tokenizer=self.tokenizer, **self._common()
        )
        ids = self.tokenizer(code, add_special_tokens=False)["input_ids"]
        if ids and ids[0] == self.tokenizer.bos_token_id:
            ids = ids[1:]
        split_tokens = [self.tokenizer.decode(int(t), skip_special_tokens=False) for t in ids]
        with contextlib.redirect_stdout(io.StringIO()):
            detector._pseudo_generate_mask(split_tokens)
        records = list(self.wg.Bich_kaiwen_First_watermark_token.items())
        tokens = [str(v[1]) for _, v in records]
        if not tokens:
            return "", [], []
        with contextlib.redirect_stdout(io.StringIO()):
            bits = self.wp.WatermarkLogitsProcessor.detect(
                detector,
                tokens,
                self.device,
                call_count_list=[v[2] for _, v in records],
                result_detection=True,
            )
        return bits, decode_rounds(bits), tokens

    def detect(self, item: WorkerItem) -> WorkerResult:
        message = self._message(item)
        bits, rounds, tokens = self.extract(item.code or "", message)
        extra: Dict[str, Any] = {
            "message": message,
            "n_eligible": len(tokens),
            "tokens": tokens,
            "bits": bits,
            "rounds": rounds,
            "round_matches": [matches(r, message) for r in rounds],
        }
        if not rounds:
            # Nessun ciclo completo: estrazione impossibile (punteggio minimo in M7, SPEC §9.5).
            return self.result(
                item, DetectStatus.FAILED, error="no complete 24-position round", extra=extra
            )
        first: Optional[str] = rounds[0]
        return self.result(
            item,
            DetectStatus.OK,
            score=float(matches(first, message)),
            native_decision=first == message,
            decoded_message=first,
            extra=extra,
        )


if __name__ == "__main__":
    raise SystemExit(main(McgmarkShim))
