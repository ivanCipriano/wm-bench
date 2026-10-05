"""Criteri della Milestone 5 sugli artefatti reali di STONE (L1 dev, configurazione di default)."""

from __future__ import annotations

import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.generation.hf_generator import generation_seed
from bench.pipeline.stages.generate_baseline import problems_ref
from bench.pipeline.stages.watermark import WatermarkStage, watermarked_ref
from bench.store.artifact_store import ArtifactStore
from tests.integration.conftest import languages_under_test, require

pytestmark = pytest.mark.data

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")
N = 6


@pytest.fixture(scope="module")
def store(data_cfg: ExperimentConfig) -> ArtifactStore:
    store = ArtifactStore(data_cfg.paths.artifacts)
    require(store.manifest_of(problems_ref("L1", Language.PYTHON)), "prepare_data artifacts")
    return store


@pytest.mark.parametrize(
    ("model", "lang"), [(m, lang) for m in MODELS for lang in languages_under_test()]
)
def test_stone_dev_cell(
    store: ArtifactStore, data_cfg: ExperimentConfig, model: str, lang: Language
) -> None:
    stage = WatermarkStage(data_cfg)
    cfg_hash = stage.config_hash("stone")
    ref = watermarked_ref("stone", model, cfg_hash, "L1", str(lang), "dev")
    require(store.manifest_of(ref), f"watermarked {ref.path} (run watermark)")
    assert store.verify(ref)
    problems = store.read_table(problems_ref("L1", lang))
    problems = problems[problems["split"] == "dev"]
    df = store.read_table(ref)
    assert len(df) == len(problems) * N  # I1
    assert set(df["problem_key"]) == set(problems["problem_key"])
    assert df["sample_id"].is_unique and set(df["config_hash"]) == {cfg_hash}
    for key, group in df.groupby("problem_key"):
        assert sorted(group["sample_index"]) == list(range(N)), key
        assert set(group["seed"]) == {
            generation_seed(data_cfg.global_seed, model, str(key), str(lang))
        }
    manifest = store.read_manifest(ref)
    assert manifest is not None and manifest.n_rows_out == manifest.n_rows_expected
    extra = manifest.extra
    if lang is Language.JAVASCRIPT:  # audit di STONE §4
        assert set(df["embed_status"]) == {"NOT_APPLICABLE"}
        return
    assert extra["worker"]["missing_results"] == 0
    assert set(df["embed_status"]) <= {"OK", "FAILED"}
    assert (df["embed_status"] == "OK").mean() > 0.99
    generation = extra["generation_config"]
    assert (
        generation["temperature"] == 0.2
        and generation["top_p"] == 0.95
        and generation["top_k"] == 0
    )
    assert manifest.worker_env is not None and manifest.worker_env["source_commit"]
