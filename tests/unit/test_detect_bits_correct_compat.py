"""``Detector.bits_correct`` (ADR-017 §1; PIPELINE di multimark §5.1).

Il calcolo di ``bits_correct`` passa da ``detect.py::_bits_correct`` all'adapter. Per i metodi
esistenti, che non lo ridefiniscono, il valore deve restare quello di prima: su 1.000
``WorkerResult`` casuali (``extra`` senza ``bits_correct``) il nuovo coincide con la funzione di
prima, copiata qui come ``_bits_correct_legacy``.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from bench_contracts import WorkerResult

from bench.config.schema import ExperimentConfig
from bench.methods.base import Detector
from bench.pipeline.stages.watermark import make_adapter

EXISTING = ("sweet", "acw", "stone", "promptmark", "mcgmark")


def _bits_correct_legacy(result: Any) -> int | None:
    """Copia di ``detect.py::_bits_correct`` prima di ADR-017."""
    if result.decoded_message is None or result.score is None:
        return None
    return int(result.score)


def _random_results(n: int, seed: int = 20261116) -> list[WorkerResult]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        score = rng.choice([None, 0.0, 12.0, rng.uniform(-50, 50), float(rng.randint(0, 12))])
        message = rng.choice([None, "", "0" * 12, "".join(rng.choice("01") for _ in range(12))])
        extra = rng.choice([{}, {"z": 1.5}, {"n_embedded": rng.randint(0, 99)}])
        out.append(
            WorkerResult(
                item_id=f"i{i}",
                sample_index=None,
                status=rng.choice(["OK", "FAILED", "NOT_APPLICABLE"]),
                raw_output=None,
                code=None,
                score=score,
                native_decision=rng.choice([None, True, False]),
                decoded_message=message,
                extra=extra,
            )
        )
    return out


@pytest.mark.parametrize("method", EXISTING)
def test_existing_methods_keep_the_old_bits_correct(cfg: ExperimentConfig, method: str) -> None:
    adapter = make_adapter(cfg, method)
    assert isinstance(adapter, Detector)
    assert type(adapter).bits_correct is Detector.bits_correct  # nessuno lo ridefinisce
    results = _random_results(1000)
    assert sum(r.decoded_message is not None and r.score is not None for r in results) > 300
    for result in results:
        assert adapter.bits_correct(result) == _bits_correct_legacy(result)
