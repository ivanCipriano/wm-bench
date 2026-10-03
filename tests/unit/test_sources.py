"""Test delle sorgenti di integrazione: CodeSearchNet e The Stack (sintetici)."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import pytest

from bench.config.schema import ExperimentConfig
from bench.data.sources import CodeSearchNetSource, TheStackCppSource
from bench.domain.enums import Language
from tests.datafix import STACK_FILES, build_tiny_datasets, tiny_config


@pytest.fixture
def tcfg(cfg: ExperimentConfig) -> ExperimentConfig:
    build_tiny_datasets(cfg.paths.datasets)
    return tiny_config(cfg)


def test_codesearchnet_candidates(tcfg: ExperimentConfig) -> None:
    spec = tcfg.datasets["codesearchnet"]
    path = spec.file("java/validation")
    frame = pd.read_parquet(path)
    frame.loc[0, "whole_func_string"] = "public int broken( {"
    frame.loc[0, "func_code_string"] = "different"
    frame.to_parquet(path, index=False)
    cands, stats = CodeSearchNetSource(spec).candidates(Language.JAVA, "validation")
    assert stats.rows == len(frame)
    assert stats.dropped_parse_error == 1
    assert stats.candidates == len(frame) - 1
    assert stats.extra["whole_equals_code_fraction"] < 1.0
    assert all(c.key.startswith("csn/java/") and c.loc >= 3 for c in cands)
    assert len({c.key for c in cands}) == len(cands)
    again, _ = CodeSearchNetSource(spec).candidates(Language.JAVA, "validation")
    assert [c.key for c in cands] == [c.key for c in again]
    assert CodeSearchNetSource(spec).repos(Language.JAVA, "test") == {
        f"org/test-repo{i}" for i in range(25)
    }


def test_thestack_partition_is_by_repository(tcfg: ExperimentConfig) -> None:
    stack = TheStackCppSource(
        tcfg.datasets["thestack_cpp"], tcfg.negatives.thestack_partition, tcfg.global_seed
    )
    assert stack.bucket("org/x") == stack.bucket("org/x")
    buckets = Counter(stack.bucket(f"r{i}") for i in range(4000))
    assert set(buckets) == {"l3_test", "l1_test", "dev", "promptmark"}
    assert all(800 < n < 1200 for n in buckets.values())  # frazioni 0,25

    pools, stats = stack.candidates(["l1_test", "dev"])
    assert stats.rows == 2 * STACK_FILES
    repos = {b: {c.repo for c in pools[b]} for b in pools}
    assert repos["l1_test"] and repos["dev"]
    assert not repos["l1_test"] & repos["dev"]
    assert all(stack.bucket(c.repo) == b for b in pools for c in pools[b])
    assert all(c.key.startswith("thestack/") for b in pools for c in pools[b])
