"""Decoding dei worker (SPEC §8.4, ADR-006). Compatibile con Python 3.9.

La configurazione **completa** arriva nel request (``request.decoding``), costruita
dall'orchestratore con ``bench.generation.decoding.neutral_settings``: una sola fonte per
baseline e metodi. Qui si aggiungono solo i token di fine sequenza e di padding (unione di
tokenizer e modello, come nella baseline) e si fissa il seme prima di ogni ``generate``.

Chiavi del dizionario che non sono parametri di generazione:
- ``torch_dtype``: tipo dei pesi (``bfloat16`` per entrambi i generatori).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Dict, List, Optional, Tuple, Union

NON_GENERATION_KEYS = ("torch_dtype",)


def generation_settings(decoding: Dict[str, Any]) -> Dict[str, Any]:
    """Parametri di ``GenerationConfig`` (senza le chiavi di servizio)."""
    return {k: v for k, v in decoding.items() if k not in NON_GENERATION_KEYS}


def _ids(value: Union[int, Iterable[int], None]) -> List[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    return [int(v) for v in value]


def special_token_ids(
    tokenizer_eos: Optional[int],
    tokenizer_pad: Optional[int],
    model_eos: Union[int, Iterable[int], None],
) -> Tuple[List[int], int]:
    """Come ``bench.generation.decoding.special_token_ids`` (stessa regola della baseline)."""
    eos = sorted(set(_ids(tokenizer_eos)) | set(_ids(model_eos)))
    if not eos:
        raise ValueError("no eos token id in tokenizer or model config")
    pad = tokenizer_pad if tokenizer_pad is not None else eos[0]
    return eos, pad


def build_generation_config(decoding: Dict[str, Any], tokenizer: Any, model: Any) -> Any:
    """``GenerationConfig`` da zero (request + eos/pad); sostituisce quella del modello."""
    from transformers import GenerationConfig

    eos, pad = special_token_ids(
        tokenizer.eos_token_id,
        tokenizer.pad_token_id,
        getattr(model.generation_config, "eos_token_id", None),
    )
    config = GenerationConfig(**generation_settings(decoding), eos_token_id=eos, pad_token_id=pad)
    model.generation_config = config
    return config


def effective_config(generation_config: Any) -> Dict[str, Any]:
    """Configurazione effettiva in forma JSON (registrata in ``extra``)."""
    data: Dict[str, Any] = generation_config.to_dict()
    data.pop("transformers_version", None)
    return {k: data[k] for k in sorted(data)}


def set_seed(seed: int) -> None:
    """``torch.manual_seed`` e ``torch.cuda.manual_seed_all`` prima di ``generate``."""
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def torch_dtype(decoding: Dict[str, Any]) -> Any:
    """Tipo dei pesi indicato nel request (default ``bfloat16``)."""
    import torch

    name = str(decoding.get("torch_dtype", "bfloat16"))
    return {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}[name]
