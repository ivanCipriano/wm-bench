"""Configurazione di decoding neutra e identica per tutti (SPEC §2.1; ADR-006).

Decisione dell'utente: oltre a temperature, top_p, max_new_tokens e N (SPEC §2.1) tutto è
disattivato (top_k = 0, repetition_penalty = 1.0, no_repeat_ngram_size = 0, campionamento
attivo) e i ``generation_config.json`` dei modelli si ignorano: dal modello si prendono
solo i token di fine sequenza e di padding. Gli shim dei metodi (M5, M6) devono usare la
stessa configurazione.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from bench.config.schema import DecodingConfig

# Parametri non fissati da SPEC §2.1: valori neutri (nessun effetto sulla distribuzione).
NEUTRAL_OVERRIDES: dict[str, Any] = {
    "do_sample": True,
    "num_beams": 1,
    "top_k": 0,
    "typical_p": 1.0,
    "epsilon_cutoff": 0.0,
    "eta_cutoff": 0.0,
    "min_p": None,
    "repetition_penalty": 1.0,
    "encoder_repetition_penalty": 1.0,
    "no_repeat_ngram_size": 0,
    "length_penalty": 1.0,
    "bad_words_ids": None,
    "suppress_tokens": None,
    "begin_suppress_tokens": None,
    "forced_bos_token_id": None,
    "forced_eos_token_id": None,
    "min_new_tokens": None,
    "min_length": 0,
}


def neutral_settings(decoding: DecodingConfig) -> dict[str, Any]:
    """Tutti i parametri di generazione, esclusi i token speciali."""
    return {
        **NEUTRAL_OVERRIDES,
        "temperature": decoding.temperature,
        "top_p": decoding.top_p,
        "max_new_tokens": decoding.max_new_tokens,
        "num_return_sequences": decoding.n,
    }


def _ids(value: int | Iterable[int] | None) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    return [int(v) for v in value]


def special_token_ids(
    tokenizer_eos: int | None,
    tokenizer_pad: int | None,
    model_eos: int | Iterable[int] | None,
) -> tuple[list[int], int]:
    """Token di fine sequenza (unione di tokenizer e modello) e di padding.

    Raises:
        ValueError: se non c'è alcun token di fine sequenza.
    """
    eos = sorted(set(_ids(tokenizer_eos)) | set(_ids(model_eos)))
    if not eos:
        raise ValueError("no eos token id in tokenizer or model config")
    pad = tokenizer_pad if tokenizer_pad is not None else eos[0]
    return eos, pad


def neutral_generation_config(
    decoding: DecodingConfig, eos_token_id: list[int], pad_token_id: int
) -> Any:
    """``transformers.GenerationConfig`` costruita da zero con i valori neutri."""
    from transformers import GenerationConfig

    return GenerationConfig(
        **neutral_settings(decoding),
        eos_token_id=list(eos_token_id),
        pad_token_id=pad_token_id,
    )


def effective_config(generation_config: Any) -> dict[str, Any]:
    """Configurazione effettiva in forma JSON (per il manifest)."""
    data: dict[str, Any] = generation_config.to_dict()
    data.pop("transformers_version", None)
    return {k: data[k] for k in sorted(data)}
