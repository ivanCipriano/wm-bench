"""Criterio di accettazione della Milestone 3 sugli artefatti reali della baseline.

Richiede ``prepare_data`` e ``generate_baseline`` (L1, entrambi i modelli, dev e test).
"""

from __future__ import annotations

import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.store.artifact_store import ArtifactStore
from tests.integration.conftest import languages_under_test, require

pytestmark = pytest.mark.data

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")
N = 6
CELLS = [
    (model, lang, split)
    for model in MODELS
    for lang in languages_under_test()
    for split in ("dev", "test")
]


@pytest.fixture(scope="module")
def store(data_cfg: ExperimentConfig) -> ArtifactStore:
    store = ArtifactStore(data_cfg.paths.artifacts)
    require(store.manifest_of(problems_ref("L1", Language.PYTHON)), "prepare_data artifacts")
    return store


@pytest.mark.parametrize(("model", "lang", "split"), CELLS)
def test_baseline_cell(
    store: ArtifactStore, data_cfg: ExperimentConfig, model: str, lang: Language, split: str
) -> None:
    ref = baseline_ref(model, "L1", lang, split)
    require(store.manifest_of(ref), f"baseline {ref.path} (run generate_baseline)")
    assert store.verify(ref)
    problems = store.read_table(problems_ref("L1", lang))
    problems = problems[problems["split"] == split]
    df = store.read_table(ref)

    # 6 campioni per problema, per tutti e soli i problemi della parte (I1).
    assert len(df) == len(problems) * N
    assert set(df["problem_key"]) == set(problems["problem_key"])
    for key, indices in df.groupby("problem_key")["sample_index"]:
        assert sorted(indices) == list(range(N)), key
    assert df["sample_id"].is_unique
    assert set(df["source"]) == {"llm_baseline"} and set(df["model_id"]) == {model}
    assert set(df["split"]) == {split}
    assert (df.loc[~df["extraction_ok"], "code"] == "").all()

    manifest = store.read_manifest(ref)
    assert manifest is not None
    extra = manifest.extra
    # Tasso di estrazione riportato (criterio M3).
    assert 0.0 <= extra["extraction_rate"] <= 1.0
    assert extra["system_prompt_sha256"] == data_cfg.prompt.system_prompt_sha256
    gen = extra["generation"]["generation_config"]
    assert gen["do_sample"] is True and gen["top_k"] == 0 and gen["repetition_penalty"] == 1.0
    assert (gen["temperature"], gen["top_p"], gen["max_new_tokens"]) == (0.2, 0.95, 512)
    assert gen["no_repeat_ngram_size"] == 0
