"""Test end-to-end della fase prepare_data sulla fixture tiny (conteggi, I1, I4)."""

from __future__ import annotations

import pandas as pd
import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stages.prepare_data import (
    integration_ref,
    native_ref,
    problems_ref,
    split_ref,
)
from tests.conftest import rebuild
from tests.datafix import build_tiny_datasets, expected_tiny_counts, tiny_config

LANGS = ("python", "java", "cpp", "javascript")


@pytest.fixture
def run(cfg: ExperimentConfig) -> tuple[BenchmarkFacade, ExperimentConfig]:
    build_tiny_datasets(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    facade = BenchmarkFacade(tcfg)
    report = facade.run_stage("prepare_data")
    assert report.ran == 1
    return facade, tcfg


def test_counts_and_invariants(run: tuple[BenchmarkFacade, ExperimentConfig]) -> None:
    facade, _ = run
    store = facade.store
    exp = expected_tiny_counts()

    split = store.read_table(split_ref())
    assert len(split) == exp["split_rows"]
    assert split["problem_key"].is_unique

    py = store.read_table(problems_ref("python"))  # type: ignore[arg-type]
    assert len(py) == exp["python_problems"]
    native_py = store.read_table(native_ref("python"))  # type: ignore[arg-type]
    assert len(native_py) == exp["python_native"]
    assert (native_py["split"] == "dev").sum() == exp["python_native_dev"]
    extra = split[split["role"] == "negative_only"]
    assert (extra["split"] == "dev").sum() == exp["extra_dev"]

    for lang in ("java", "cpp", "javascript"):
        assert len(store.read_table(problems_ref(lang))) == exp["hep_problems"]  # type: ignore[arg-type]
        assert len(store.read_table(integration_ref(lang, "test"))) == exp["hep_integration_test"]  # type: ignore[arg-type]
        assert len(store.read_table(integration_ref(lang, "dev"))) == exp["hep_integration_dev"]  # type: ignore[arg-type]
    assert len(store.read_table(integration_ref("python", "dev"))) == exp["python_integration_dev"]  # type: ignore[arg-type]
    assert not store.exists(integration_ref("python", "test"))  # type: ignore[arg-type]


def test_i4_one_split_per_problem_key(run: tuple[BenchmarkFacade, ExperimentConfig]) -> None:
    facade, _ = run
    store = facade.store
    frames = [store.read_table(split_ref())]
    for lang in LANGS:
        frames.append(store.read_table(problems_ref(lang)))  # type: ignore[arg-type]
        frames.append(store.read_table(native_ref(lang)))  # type: ignore[arg-type]
    allrows = pd.concat([f[["problem_key", "split"]] for f in frames])
    per_key = allrows.groupby("problem_key")["split"].nunique()
    assert (per_key == 1).all()
    # Stesso problema HumanEval: stessa parte in tutti i linguaggi.
    he = {
        lang: store.read_table(problems_ref(lang)).set_index("problem_key")["split"]
        for lang in LANGS
    }  # type: ignore[arg-type]
    he["python"] = he["python"][he["python"].index.str.startswith("humaneval/")]
    for lang in ("java", "cpp", "javascript"):
        assert he[lang].sort_index().equals(he["python"].sort_index())


def test_integration_samples(run: tuple[BenchmarkFacade, ExperimentConfig]) -> None:
    facade, _ = run
    store = facade.store
    for lang, part in (("java", "test"), ("cpp", "dev"), ("python", "dev")):
        ref = integration_ref(lang, part)  # type: ignore[arg-type]
        df = store.read_table(ref)
        assert set(df["split"]) == {part}
        assert df["contamination_risk"].all()
        assert df["source"].eq("human").all()
        assert {"loc", "repo"} <= set(df.columns)
        manifest = store.read_manifest(ref)
        assert manifest is not None
        e = manifest.extra
        assert {"moved_between_bins", "reference", "seed", "out_of_range"} <= set(e)
        lo, hi = e["reference_range"]
        assert df["loc"].between(lo, hi).all()
        assert manifest.provenance.parser_versions["tree-sitter"] == "0.22.3"
    cpp_test = set(store.read_table(integration_ref("cpp", "test"))["repo"])  # type: ignore[arg-type]
    cpp_dev = set(store.read_table(integration_ref("cpp", "dev"))["repo"])  # type: ignore[arg-type]
    assert not cpp_test & cpp_dev


def test_problem_loc_statistics_in_manifest(run: tuple[BenchmarkFacade, ExperimentConfig]) -> None:
    facade, _ = run
    manifest = facade.store.read_manifest(problems_ref("python"))  # type: ignore[arg-type]
    assert manifest is not None
    stats = manifest.extra["loc_to_generate"]
    assert set(stats) == {"humanevalplus", "mbppplus"}
    assert stats["humanevalplus"]["n"] == 5


def test_second_run_is_skipped(run: tuple[BenchmarkFacade, ExperimentConfig]) -> None:
    facade, _ = run
    assert facade.run_stage("prepare_data").skipped == 1


def test_unsupported_levels_rejected(cfg: ExperimentConfig) -> None:
    with pytest.raises(ConfigError, match="levels=\\[L1\\]"):
        BenchmarkFacade(rebuild(cfg, levels=["L1", "L2"])).run_stage("prepare_data")
