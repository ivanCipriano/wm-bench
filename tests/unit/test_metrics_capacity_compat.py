"""Lunghezza del messaggio per metodo e ``message_accuracy_detected`` (ADR-017 §2; PIPELINE §5.2).

Su una tabella sintetica in stile MCGMark (12 bit) ``message_accuracy``, ``bit_accuracy`` ed
``embedding_success_rate`` della nuova ``_capacity`` coincidono con quelli della funzione di prima,
copiata qui come ``_capacity_legacy``; ``message_accuracy_detected`` è una riga in più. Con 24 bit
(``mcss``, ``multimark``) la lunghezza viene dall'adapter.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any

import pandas as pd
import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.metrics.stats import ratio_interval, wilson
from bench.metrics.uncertainty import ClusterBootstrap
from bench.pipeline.stages.metrics import MetricsStage
from bench.pipeline.stages.watermark import make_adapter

Row = tuple[str, float, tuple[float, float], str, int]
LEGACY_MESSAGE_BITS = 12


def _capacity_legacy(
    add: Callable[..., None], boot: Callable[[str], ClusterBootstrap], positives: pd.DataFrame
) -> None:
    """Copia di ``MetricsStage._capacity`` prima di ADR-017 (``MESSAGE_BITS = 12``)."""
    bits = pd.to_numeric(positives["bits_correct"], errors="coerce").fillna(0)
    bits = bits.where(positives["status"] == "OK", 0)
    n = len(positives)
    full = int((bits == LEGACY_MESSAGE_BITS).sum())
    add("message_accuracy", full / n, wilson(full, n), "wilson", n)
    table = positives.assign(bit_frac=bits / LEGACY_MESSAGE_BITS)
    add(
        "bit_accuracy",
        float(table["bit_frac"].mean()),
        ratio_interval(
            table, "bit_frac", boot("bit_accuracy").seed, boot("bit_accuracy").n_resamples
        ),
        "cluster_bootstrap",
        n,
    )
    ok = int((positives["embed_status"] == "OK").sum())
    add("embedding_success_rate", ok / n, wilson(ok, n), "wilson", n)


def _boot(name: str) -> ClusterBootstrap:
    return ClusterBootstrap(sum(map(ord, name)), n_resamples=200)


def _positives(n: int, bits: int, seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for i in range(n):
        status = rng.choice(["OK"] * 8 + ["FAILED"])
        embed = rng.choice(["OK"] * 6 + ["PARTIAL", "FAILED"])
        correct = rng.choice([None, bits, bits, rng.randint(0, bits)])
        rows.append(
            {
                "problem_key": f"p{i % 17}",
                "status": status,
                "embed_status": embed,
                "score": rng.uniform(0, 12),
                "bits_correct": correct,
            }
        )
    return pd.DataFrame(rows)


def _run(fn: Callable[..., None], *args: Any) -> dict[str, Row]:
    rows: list[Row] = []
    fn(lambda *a: rows.append(a), _boot, *args)
    return {r[0]: r for r in rows}


def _same(a: Row, b: Row) -> bool:
    def eq(x: float, y: float) -> bool:
        return (math.isnan(x) and math.isnan(y)) or abs(x - y) <= 1e-12

    return (
        a[0] == b[0]
        and eq(a[1], b[1])
        and eq(a[2][0], b[2][0])
        and eq(a[2][1], b[2][1])
        and a[3:] == b[3:]
    )


@pytest.mark.parametrize("seed", range(5))
def test_mcgmark_capacity_is_unchanged(seed: int) -> None:
    positives = _positives(300, LEGACY_MESSAGE_BITS, seed)
    old = _run(_capacity_legacy, positives)
    new = _run(MetricsStage._capacity, positives, LEGACY_MESSAGE_BITS, 6.0)
    assert set(new) - {"message_accuracy_detected"} == set(old)
    for name, row in old.items():
        assert _same(new[name], row), name


def test_message_accuracy_detected_counts_only_the_detected() -> None:
    positives = pd.DataFrame(
        {
            "problem_key": ["a", "b", "c", "d", "e"],
            "status": ["OK", "OK", "OK", "OK", "FAILED"],
            "embed_status": ["OK", "OK", "PARTIAL", "FAILED", "OK"],
            "score": [9.0, 8.0, 7.0, 9.0, 9.0],
            "bits_correct": [24, 20, 24, 24, 24],
        }
    )
    rows = _run(MetricsStage._capacity, positives, 24, 7.5)
    # rilevati: a, b (c sotto soglia, d con inserimento FAILED, e con rilevazione FAILED)
    name, value, ci, how, n = rows["message_accuracy_detected"]
    assert (value, n, how) == (0.5, 2, "wilson") and ci == wilson(1, 2)
    assert rows["message_accuracy"][1] == pytest.approx(3 / 5) and rows["message_accuracy"][4] == 5
    assert rows["bit_accuracy"][1] == pytest.approx((24 + 20 + 24 + 24 + 0) / 24 / 5)
    # nessun rilevato: la metrica non è definita e non si emette (I7)
    assert "message_accuracy_detected" not in _run(MetricsStage._capacity, positives, 24, 100.0)


def test_message_bits_come_from_the_adapter(cfg: ExperimentConfig) -> None:
    stage = MetricsStage(cfg)
    assert stage._message_bits("mcgmark") == 12
    assert make_adapter(cfg, "stone").message_bits is None
    stage._adapters["fake"] = type("Fake", (), {"message_bits": None})()
    with pytest.raises(ConfigError, match="message_bits"):
        stage._message_bits("fake")
