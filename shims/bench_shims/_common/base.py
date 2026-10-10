"""Classe base degli shim (SPEC §8.4). Compatibile con Python 3.9."""

from __future__ import annotations

from typing import Any, Dict, List

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import DetectStatus, EmbedStatus


class FatalShimError(RuntimeError):
    """Errore che rende inutili gli item successivi (es. licenza di uno strumento scaduta).

    Il runner non scrive il lotto in corso, si ferma e esce con exit code 2: gli item già scritti
    restano e un nuovo invio riprende da lì.
    """


class ShimBase:
    """Shim di un metodo: carica le risorse una volta, poi elabora un item alla volta.

    Le sottoclassi ridefiniscono ``setup`` e le operazioni che supportano. ``embed`` restituisce
    ``n`` risultati per un item con prompt (metodi in generazione) o uno per un item con codice
    (metodi post-hoc); ``detect`` restituisce un risultato.
    """

    method = ""
    # Item elaborati insieme dal runner (es. ACW: Sourcery lavora su cartelle e ogni chiamata
    # costa secondi). Con 1 il runner chiama ``embed``/``detect`` item per item.
    batch_size = 1

    def setup(self, request: WorkerRequest) -> None:
        """Carica modello, tokenizer e risorse; un errore qui è fatale (exit code 2)."""
        raise NotImplementedError

    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        """Inserisce il watermark."""
        raise NotImplementedError(f"{self.method}: embed not supported")

    def detect(self, item: WorkerItem) -> WorkerResult:
        """Calcola il punteggio di rilevazione."""
        raise NotImplementedError(f"{self.method}: detect not supported")

    def embed_batch(self, items: List[WorkerItem]) -> List[List[WorkerResult]]:
        """Inserimento di un lotto (una lista di risultati per item, nello stesso ordine)."""
        return [self.embed(item) for item in items]

    def detect_batch(self, items: List[WorkerItem]) -> List[WorkerResult]:
        """Rilevazione di un lotto (un risultato per item, nello stesso ordine)."""
        return [self.detect(item) for item in items]

    # ------------------------------------------------------------------ utilità
    @staticmethod
    def result(
        item: WorkerItem,
        status: str,
        sample_index: Any = None,
        raw_output: Any = None,
        code: Any = None,
        score: Any = None,
        native_decision: Any = None,
        decoded_message: Any = None,
        extra: Any = None,
        error: Any = None,
        elapsed_s: float = 0.0,
    ) -> WorkerResult:
        """``WorkerResult`` con i campi non indicati a ``None``."""
        return WorkerResult(
            item_id=item.item_id,
            sample_index=sample_index,
            status=status,
            raw_output=raw_output,
            code=code,
            score=score,
            native_decision=native_decision,
            decoded_message=decoded_message,
            extra=dict(extra or {}),
            error=error,
            elapsed_s=elapsed_s,
        )

    @classmethod
    def failed(cls, item: WorkerItem, op: str, expected: int, error: str) -> List[WorkerResult]:
        """Risultati ``FAILED`` per un item intero (eccezione nello shim)."""
        status = DetectStatus.FAILED if op == "detect" else EmbedStatus.FAILED
        per_prompt = op == "embed" and item.prompt_messages is not None
        indices: List[Any] = list(range(expected)) if per_prompt else [None] * expected
        return [cls.result(item, status, sample_index=i, error=error) for i in indices]

    @staticmethod
    def json_safe(data: Dict[str, Any]) -> Dict[str, Any]:
        """Copia con i valori non JSON convertiti in stringa (per ``extra``)."""
        out: Dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
            elif isinstance(value, (list, tuple)):
                out[key] = [
                    v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
                    for v in value
                ]
            elif isinstance(value, dict):
                out[key] = ShimBase.json_safe(value)
            else:
                out[key] = str(value)
        return out
