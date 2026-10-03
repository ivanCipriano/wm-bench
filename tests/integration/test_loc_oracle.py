"""Oracle della SPEC §10.3: righe da generare di HumanEval+ e MBPP+ sui file reali."""

from __future__ import annotations

import statistics

import pytest

from bench.config.schema import ExperimentConfig
from bench.data.loaders.humanevalplus import HumanEvalPlusLoader
from bench.data.loaders.mbppplus import MbppPlusLoader
from bench.domain.enums import Language
from tests.integration.conftest import require

pytestmark = pytest.mark.data

# Valori del protocollo (SPEC §10.3), tolleranza ±0,1.
TARGETS = {"humanevalplus": (5.1, 4.4), "mbppplus": (4.0, 3.7)}
TOLERANCE = 0.1


@pytest.mark.parametrize("dataset", sorted(TARGETS))
def test_loc_statistics_match_the_protocol(data_cfg: ExperimentConfig, dataset: str) -> None:
    spec = data_cfg.datasets[dataset]
    require(spec.file("data"), f"{dataset} file")
    loader_cls = HumanEvalPlusLoader if dataset == "humanevalplus" else MbppPlusLoader
    locs = [p.loc_to_generate or 0 for p in loader_cls(spec).load_problems(Language.PYTHON)]
    mean, std = statistics.fmean(locs), statistics.pstdev(locs)
    target_mean, target_std = TARGETS[dataset]
    assert abs(mean - target_mean) <= TOLERANCE, (mean, std)
    assert abs(std - target_std) <= TOLERANCE, (mean, std)
