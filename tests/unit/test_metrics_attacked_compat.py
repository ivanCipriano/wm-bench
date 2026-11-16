"""Capacità sotto attacco e attribuzioni sbagliate (ADR-017 §3; PIPELINE di multimark §5.3).

``wrong_attribution_rate_<nome>`` su tabelle sintetiche: solo i positivi rilevati con il punteggio
secondario, confronto di ``extra["best_<nome>"]`` con il messaggio atteso del campione, Wilson;
nessuna riga per i metodi senza ``best_<nome>`` (tutti quelli esistenti). Che le metriche esistenti
di una cella MCGMark attaccata non cambino lo verifica ``test_metrics_compat.py`` (catena con T1.5).
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pandas as pd

from bench.config.schema import ExperimentConfig
from bench.domain.models import ThresholdSet
from bench.metrics.stats import wilson
from bench.pipeline.stage import Cell
from bench.pipeline.stages.metrics import MetricsStage

Row = tuple[str, float, tuple[float, float], str, int]
CELL = Cell(method="fake", model_id="m", level="L1", language="python", split="dev")


class FakeRegistryAdapter:
    """Adapter con due punteggi secondari del registro (come ``mcss``/``multimark``)."""

    secondary_scores: ClassVar[dict[str, str]] = {
        "nreg16": "score_nreg16",
        "nreg1024": "score_nreg1024",
        "decode": "score_decode",
    }

    def secondary_score(self, name: str, extra: dict[str, Any]) -> float | None:
        value = extra.get(self.secondary_scores[name])
        return None if value is None else float(value)


def _thresholds(**secondary: float) -> ThresholdSet:
    return ThresholdSet.model_construct(
        threshold=1.0, secondary={k: {"threshold": v} for k, v in secondary.items()}
    )


def _tables(extras: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    ids = [f"s{i}" for i in range(len(extras))]
    positives = pd.DataFrame(
        {
            "sample_id": ids,
            "status": ["OK"] * len(ids),
            "embed_status": ["OK"] * len(ids),
            "score": [5.0] * len(ids),
            "extra_json": [json.dumps(e) for e in extras],
        }
    )
    samples = pd.DataFrame({"sample_id": ids, "expected_message": ["0" * 24] * len(ids)})
    return positives, samples


def _run(stage: MetricsStage, positives: pd.DataFrame, samples: pd.DataFrame, th: Any) -> dict:
    rows: list[Row] = []
    stage._wrong_attribution(CELL, lambda *a: rows.append(a), positives, samples, th)
    return {r[0]: r for r in rows}


def test_wrong_attribution_counts_detected_with_a_wrong_best(cfg: ExperimentConfig) -> None:
    stage = MetricsStage(cfg)
    stage._adapters["fake"] = FakeRegistryAdapter()
    right, wrong = "0" * 24, "1" * 24
    extras = [
        {"score_nreg16": 9.0, "best_nreg16": right, "score_nreg1024": 9.0, "best_nreg1024": right},
        {"score_nreg16": 9.0, "best_nreg16": wrong, "score_nreg1024": 9.0, "best_nreg1024": wrong},
        {"score_nreg16": 9.0, "best_nreg16": wrong, "score_nreg1024": 2.0, "best_nreg1024": wrong},
        {"score_nreg16": 1.0, "best_nreg16": wrong, "score_nreg1024": 1.0, "best_nreg1024": wrong},
    ]
    positives, samples = _tables(extras)
    rows = _run(stage, positives, samples, _thresholds(nreg16=5.0, nreg1024=5.0, decode=0.5))
    # nreg16: rilevati s0, s1, s2 → 2 sbagliati su 3; nreg1024: rilevati s0, s1 → 1 su 2
    assert rows["wrong_attribution_rate_nreg16"][1:] == (2 / 3, wilson(2, 3), "wilson", 3)
    assert rows["wrong_attribution_rate_nreg1024"][1:] == (1 / 2, wilson(1, 2), "wilson", 2)
    assert "wrong_attribution_rate_decode" not in rows  # nessun best_decode
    # nessun rilevato: nessuna riga (I7)
    assert _run(stage, positives, samples, _thresholds(nreg16=99, nreg1024=99, decode=9)) == {}


def test_no_rows_for_methods_without_best_messages(cfg: ExperimentConfig) -> None:
    stage = MetricsStage(cfg)
    positives, samples = _tables([{"z_nonsyntax": 3.0}, {}])
    rows: list[Row] = []
    for method in ("mcgmark", "stone", "sweet", "acw", "promptmark"):
        cell = Cell(method=method, model_id="m", level="L1", language="python", split="dev")
        stage._wrong_attribution(cell, rows.append, positives, samples, None)  # type: ignore[arg-type]
    assert rows == []
