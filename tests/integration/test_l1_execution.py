"""Criteri di accettazione della Milestone 4 sugli artefatti reali dell'esecuzione.

Richiede ``evalplus_groundtruth`` ed ``execute`` (canoniche e baseline L1).
"""

from __future__ import annotations

import pytest
import yaml

from bench.config.builder import config_dir
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.pipeline.stage import Cell
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.store.artifact_store import ArtifactStore
from tests.integration.conftest import languages_under_test, require

pytestmark = pytest.mark.data

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")


@pytest.fixture(scope="module")
def store(data_cfg: ExperimentConfig) -> ArtifactStore:
    store = ArtifactStore(data_cfg.paths.artifacts)
    require(store.manifest_of(problems_ref("L1", Language.PYTHON)), "prepare_data artifacts")
    return store


def known_anomalies() -> set[tuple[str, str]]:
    data = yaml.safe_load(
        (config_dir() / "execution" / "known_anomalies.yaml").read_text(encoding="utf-8")
    )
    return {(a["language"], a["problem_key"]) for a in data.get("anomalies") or []}


@pytest.mark.parametrize("lang", languages_under_test())
def test_canonical_solutions_pass(store: ArtifactStore, lang: Language) -> None:
    cell = Cell(source="canonical", level="L1", language=str(lang))
    ref = execution_ref(cell)
    require(store.manifest_of(ref), f"execution {ref.path} (run execute)")
    assert store.verify(ref)
    problems = store.read_table(problems_ref("L1", lang))
    df = store.read_table(ref)
    assert len(df) == len(problems) and set(df["problem_key"]) == set(problems["problem_key"])
    failing = {(str(lang), k) for k in df.loc[df["status"] != "PASSED", "problem_key"]}
    undocumented = failing - known_anomalies()
    assert (
        not undocumented
    ), f"canonical failures not in known_anomalies.yaml: {sorted(undocumented)}"


@pytest.mark.parametrize(
    ("model", "lang", "split"),
    [(m, lang, s) for m in MODELS for lang in languages_under_test() for s in ("dev", "test")],
)
def test_one_record_per_baseline_sample(
    store: ArtifactStore, model: str, lang: Language, split: str
) -> None:
    cell = Cell(source="llm_baseline", model_id=model, level="L1", language=str(lang), split=split)
    ref = execution_ref(cell)
    require(store.manifest_of(ref), f"execution {ref.path} (run execute)")
    assert store.verify(ref)
    samples = store.read_table(baseline_ref(model, "L1", lang, split))
    df = store.read_table(ref)
    assert len(df) == len(samples)  # I1
    assert set(df["sample_id"]) == set(samples["sample_id"])
    assert df["sample_id"].is_unique
    manifest = store.read_manifest(ref)
    assert manifest is not None and manifest.sandbox_image_hash
    assert set(df["sandbox_image_hash"]) == {manifest.sandbox_image_hash}
    # Nessun errore della sandbox (sarebbero esecuzioni da ripetere, non esiti del codice).
    assert manifest.extra["status_counts"]["SANDBOX_ERROR"] == 0
