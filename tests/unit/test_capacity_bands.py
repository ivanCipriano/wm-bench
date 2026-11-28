"""``bench.metrics.capacity_bands``: fasce a giri di MCGMark e righe per fascia (spostate da
``scripts/report_capacity.py`` il 28 novembre 2026, stesso comportamento)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from bench.metrics.capacity_bands import (
    LOC_BANDS,
    MCGMARK_SITE_BANDS,
    SITE_BANDS,
    band_of,
    band_rows,
)
from tests.unit.test_inventory_scripts import _load


def _table() -> pd.DataFrame:
    """Otto positivi di MCGMark (12 bit): sotto le 24 posizioni la rilevazione fallisce."""
    return pd.DataFrame({
        "problem_key": [f"p{i % 3}" for i in range(8)],
        "score": [12.0, 3.0, 5.0, 12.0, 12.0, 12.0, 12.0, 11.0],
        "status": ["FAILED", "FAILED", "FAILED", "OK", "OK", "OK", "OK", "OK"],
        "embed_status": ["PARTIAL", "PARTIAL", "PARTIAL", "OK", "OK", "OK", "OK", "OK"],
        "bits_correct": [None, None, None, 12, 12, 12, 12, 11],
        "exec_status": ["PASSED"] * 7 + ["FAILED"],
        "n_sites": [0, 5, 23, 24, 47, 48, 100, 30],
        "loc_to_generate": [None] * 8,
    })  # fmt: skip


def test_mcgmark_bands_follow_the_24_position_rounds() -> None:
    assert [band_of(v, MCGMARK_SITE_BANDS) for v in (0, 1, 23, 24, 47, 48, 95, 96, 500)] == [
        None, "1-23", "1-23", "24-47", "24-47", "48-95", "48-95", "96+", "96+",
    ]  # fmt: skip
    assert band_of(None, MCGMARK_SITE_BANDS) is None
    assert band_of(math.nan, MCGMARK_SITE_BANDS) is None


def test_band_rows_with_the_mcgmark_bands() -> None:
    rows = band_rows(_table(), "n_sites", MCGMARK_SITE_BANDS, 12, 10.0, seed=1)
    assert [r["band"] for r in rows] == ["1-23", "24-47", "48-95", "96+", "n_sites=0/none"]
    assert [r["n"] for r in rows] == [2, 3, 1, 1, 1]
    below, first = rows[0], rows[1]
    assert below["tpr_at_fpr"] == 0.0 and below["message_accuracy"] == 0.0  # nessun giro completo
    assert first["tpr_at_fpr"] == 1.0
    assert first["message_accuracy"] == pytest.approx(2 / 3)  # 12, 12, 11 bit su 12
    assert first["bit_accuracy"] == pytest.approx((1 + 1 + 11 / 12) / 3)
    assert first["pass_at_1"] == pytest.approx(2 / 3)
    loc = band_rows(_table(), "loc_to_generate", LOC_BANDS, 12, 10.0, seed=1)
    assert [r["n"] for r in loc] == [0] * len(LOC_BANDS) + [8]  # dato assente: tutto fuori
    assert math.isnan(loc[0]["tpr_at_fpr"])


def test_the_script_reexports_the_moved_functions() -> None:
    """``scripts/report_capacity.py`` e chi lo carica (``report_attacks_dev.py``) vedono le stesse
    fasce e funzioni di prima."""
    script = _load("report_capacity")
    assert script.SITE_BANDS == SITE_BANDS
    assert script.SITE_BANDS == ((1, 7), (8, 12), (13, 19), (20, 31), (32, 55), (56, None))
    assert script.LOC_BANDS == LOC_BANDS
    assert script.band_rows is band_rows and callable(script.cell_table)
