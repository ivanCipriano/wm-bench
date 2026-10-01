"""Fixture condivise della suite di test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Valori attesi di derive_seed, fissati una volta per tutte (SPEC §21.3): devono
# coincidere in ogni versione di Python (3.9, 3.10, 3.11) e in ogni ambiente.
SEED_VECTORS: list[tuple[list[Any], int]] = [
    ([20261001, "split", "humaneval/0"], 614248956),
    ([20261001, "gen", "qwen25_coder_7b", "mbpp/17", "python"], 351847650),
    ([20261001, "wm-key", "sweet", "k1"], 1371034547),
    ([20261001, "mcgmark-msg", "humaneval/42", "java", "deepseek_coder_6p7b", 3], 84821436),
    ([], 1616749724),
    (["àèì", None, 1.5], 1553620446),
]


@pytest.fixture
def repo_root() -> Path:
    """Radice del repository."""
    return REPO_ROOT
