"""Dataclass di richiesta e risposta dei worker (SPEC §5.1 e §8).

Ogni dataclass offre ``to_dict``/``from_dict``: la serializzazione è JSON puro e
``from_dict`` rifiuta campi ignoti o mancanti, così una divergenza di contratto
emerge subito invece di perdere dati in silenzio.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar

SCHEMA_VERSION = "1.0"

# Lunghezza massima del messaggio d'errore in WorkerResult (SPEC §5.1).
MAX_ERROR_CHARS = 2000

_T = TypeVar("_T", bound="_Contract")


class ContractError(ValueError):
    """Dati non conformi al contratto (campi ignoti, mancanti o versione errata)."""


class _Contract:
    """Base comune: conversione da e verso dizionari JSON-serializzabili."""

    def to_dict(self) -> dict[str, Any]:
        """Restituisce i campi come dizionario (copia profonda)."""
        result: dict[str, Any] = dataclasses.asdict(self)  # type: ignore[call-overload]
        return result

    @classmethod
    def from_dict(cls: type[_T], data: Mapping[str, Any]) -> _T:
        """Costruisce l'istanza da un dizionario.

        Args:
            data: dizionario con esattamente i campi della dataclass (quelli con
                default possono mancare).

        Returns:
            L'istanza costruita.

        Raises:
            ContractError: se mancano campi obbligatori o ce ne sono di ignoti.
        """
        fields = dataclasses.fields(cls)  # type: ignore[arg-type]
        names = {f.name for f in fields}
        unknown = sorted(set(data) - names)
        if unknown:
            raise ContractError(f"{cls.__name__}: unknown fields {unknown}")
        required = {
            f.name
            for f in fields
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        }
        missing = sorted(required - set(data))
        if missing:
            raise ContractError(f"{cls.__name__}: missing fields {missing}")
        return cls(**dict(data))


@dataclass
class WorkerRequest(_Contract):
    """Richiesta passata al worker tramite ``request.json``."""

    schema_version: str  # es. "1.0"; il worker rifiuta versioni diverse (exit code 3)
    op: str  # WorkerOp
    method: str
    model_id: str  # es. "qwen25_coder_7b"
    model_path: str  # percorso locale dei pesi
    tokenizer_path: str
    hparams: dict[str, Any]  # già tradotti nei nomi del repository originale
    key: int  # chiave segreta del watermark
    key_id: str  # "k1" (principale) o "k2" (T3.1)
    decoding: dict[str, Any]  # temperature, top_p, max_new_tokens, n
    system_prompt: str
    items_path: str  # JSONL di WorkerItem
    output_path: str  # JSONL di WorkerResult (append, flush per riga)
    device: str  # "cuda:0" | "cpu"
    log_path: str

    def check_version(self) -> None:
        """Verifica che la versione dello schema coincida con quella del pacchetto.

        Raises:
            ContractError: se ``schema_version`` è diversa da ``SCHEMA_VERSION``.
        """
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError(f"schema_version {self.schema_version!r} != {SCHEMA_VERSION!r}")


@dataclass
class WorkerItem(_Contract):
    """Singola unità di lavoro letta da ``items_path``."""

    item_id: str  # sample_id (detect) o prompt_id (embed)
    language: str
    seed: int
    prompt_messages: Optional[list[dict[str, str]]]  # chat già costruita dall'orchestratore
    code: Optional[str]  # input per ACW (embed) e per detect
    context_prompt: Optional[str]  # contesto per SWEET in detect (SPEC §9.1)
    expected_message: Optional[str]  # stringa di "0"/"1" per MCGMark
    n: int  # numero di campioni da generare (embed da prompt)


@dataclass
class WorkerResult(_Contract):
    """Risultato scritto dal worker in ``output_path``, una riga JSONL per risultato."""

    item_id: str
    sample_index: Optional[int]  # 0..n-1 per embed da prompt
    status: str  # EmbedStatus o DetectStatus
    raw_output: Optional[str]  # testo grezzo del modello (embed)
    code: Optional[str]  # codice marcato (ACW); per gli altri lo estrae l'orchestratore
    score: Optional[float]  # detect: più alto = più probabilmente marcato
    native_decision: Optional[bool]  # decisione con la soglia nativa (solo diagnostica)
    decoded_message: Optional[str]  # MCGMark
    extra: dict[str, Any] = field(default_factory=dict)  # diagnostica specifica del metodo
    error: Optional[str] = None  # messaggio d'errore troncato a MAX_ERROR_CHARS
    elapsed_s: float = 0.0

    def __post_init__(self) -> None:
        """Tronca ``error`` a ``MAX_ERROR_CHARS`` caratteri."""
        if self.error is not None and len(self.error) > MAX_ERROR_CHARS:
            self.error = self.error[:MAX_ERROR_CHARS]
