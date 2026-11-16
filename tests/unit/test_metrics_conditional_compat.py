"""``conditional_preservation`` per ogni ``CodeEmbedder`` (ADR-017 §4; PIPELINE di multimark §5.4).

La condizione passa da ``method == "acw"`` a ``isinstance(adapter, CodeEmbedder)``. Fra i metodi
esistenti l'unico ``CodeEmbedder`` è ACW, quindi la metrica compare esattamente nelle stesse celle
di prima; il valore di ACW sulla catena finta resta identico (``test_metrics_compat.py``). I nuovi
metodi post-hoc (``mcss``, ``multimark``) la ricevono (``test_posthoc_smoke.py``).
"""

from __future__ import annotations

import pytest

from bench.config.schema import ExperimentConfig
from bench.pipeline.stages.metrics import MetricsStage

EXISTING = ("sweet", "acw", "stone", "promptmark", "mcgmark")


@pytest.mark.parametrize("method", EXISTING)
def test_same_methods_as_before(cfg: ExperimentConfig, method: str) -> None:
    assert MetricsStage(cfg)._post_hoc(method) == (method == "acw")
