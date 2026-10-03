"""Fixture dei test di integrazione sui dati reali (marker ``data``).

Configurazione: ``paths=$WMB_TEST_PATHS`` (default ``cluster``). Se i dati mancano il test
viene saltato, a meno che ``WMB_REQUIRE_DATA=1`` (da impostare sul cluster): allora fallisce.
``WMB_TEST_LANGUAGES`` (es. ``python,java``) limita i linguaggi verificati; sul cluster non va
impostata, così si verificano tutti e quattro.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bench.config.builder import load_experiment
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language


def require(path: Path, what: str) -> None:
    """Salta (o fallisce con ``WMB_REQUIRE_DATA=1``) se ``path`` non esiste."""
    if path.exists():
        return
    message = f"{what} not found: {path}"
    if os.environ.get("WMB_REQUIRE_DATA") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def data_cfg() -> ExperimentConfig:
    """Configurazione reale (con i percorsi del cluster, salvo ``WMB_TEST_PATHS``)."""
    paths = os.environ.get("WMB_TEST_PATHS", "cluster")
    return load_experiment([f"paths={paths}", "stage=prepare_data", "levels=[L1]"])


def languages_under_test() -> tuple[Language, ...]:
    """Linguaggi da verificare (default: tutti e quattro)."""
    raw = os.environ.get("WMB_TEST_LANGUAGES")
    if not raw:
        return tuple(Language)
    return tuple(Language(x.strip()) for x in raw.split(",") if x.strip())
