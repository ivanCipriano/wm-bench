"""Contratti condivisi tra l'orchestratore wm-bench e i worker dei metodi (SPEC §5.1).

Vincoli: solo libreria standard, Python >= 3.9, niente ``match`` né ``X | Y`` nei tipi
valutati a runtime. Lo stesso codice gira in tutti e 6 gli ambienti.
"""

from __future__ import annotations

from bench_contracts.enums import DetectStatus, EmbedStatus, WorkerOp
from bench_contracts.jsonl import append_jsonl, iter_jsonl, read_done_ids, write_jsonl
from bench_contracts.schema import (
    SCHEMA_VERSION,
    ContractError,
    WorkerItem,
    WorkerRequest,
    WorkerResult,
)
from bench_contracts.seeds import derive_seed

__version__ = "1.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "ContractError",
    "DetectStatus",
    "EmbedStatus",
    "WorkerItem",
    "WorkerOp",
    "WorkerRequest",
    "WorkerResult",
    "append_jsonl",
    "derive_seed",
    "iter_jsonl",
    "read_done_ids",
    "write_jsonl",
]
