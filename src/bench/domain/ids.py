"""Identificatori canonici: problem_key, sample_id, config_hash (SPEC §5.3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import xxhash
from bench_contracts import SCHEMA_VERSION, derive_seed

__all__ = [
    "SAMPLE_ID_FIELDS",
    "canonical_json",
    "config_hash",
    "derive_seed",
    "problem_key",
    "sample_id",
]

# Ordine dei campi della concatenazione canonica di sample_id (SPEC §5.3).
SAMPLE_ID_FIELDS: tuple[str, ...] = (
    "source",
    "problem_key",
    "language",
    "model_id",
    "method",
    "config_hash",
    "key_id",
    "sample_index",
    "parent_id",
    "attack_id",
    "attack_params_hash",
)

CONFIG_HASH_LENGTH = 12


def canonical_json(obj: Any) -> str:
    """Serializza in JSON canonico: chiavi ordinate, separatori compatti, UTF-8, niente NaN."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def problem_key(family: str, problem_id: str | int) -> str:
    """Costruisce la chiave canonica cross-linguaggio ``"{famiglia}/{id}"``.

    Args:
        family: famiglia del problema (``humaneval``, ``mbpp``, ``codenet``, ...).
        problem_id: identificativo del problema nella famiglia.
    """
    if not family or "/" in family:
        raise ValueError(f"invalid problem family: {family!r}")
    return f"{family}/{problem_id}"


def sample_id(
    *,
    source: str,
    problem_key: str,
    language: str,
    model_id: str | None = None,
    method: str | None = None,
    config_hash: str | None = None,
    key_id: str | None = None,
    sample_index: int | None = None,
    parent_id: str | None = None,
    attack_id: str | None = None,
    attack_params_hash: str | None = None,
) -> str:
    """Calcola ``sample_id``: xxh3_128 esadecimale della concatenazione canonica.

    I campi sono uniti con ``|`` nell'ordine di ``SAMPLE_ID_FIELDS``; i campi assenti
    valgono stringa vuota.
    """
    values: list[object | None] = [
        source,
        problem_key,
        language,
        model_id,
        method,
        config_hash,
        key_id,
        sample_index,
        parent_id,
        attack_id,
        attack_params_hash,
    ]
    payload = "|".join("" if v is None else str(v) for v in values)
    return xxhash.xxh3_128_hexdigest(payload.encode("utf-8"))


def config_hash(
    hparams: Mapping[str, Any],
    submodule_commit: str,
    contract_version: str = SCHEMA_VERSION,
) -> str:
    """Hash di 12 caratteri della configurazione effettiva di un metodo.

    È lo SHA-256 (troncato) del JSON canonico di iperparametri effettivi, versione del
    contratto e commit del submodule del metodo (ADR-003).
    """
    payload = canonical_json(
        {
            "hparams": dict(hparams),
            "contract_version": contract_version,
            "submodule_commit": submodule_commit,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:CONFIG_HASH_LENGTH]
