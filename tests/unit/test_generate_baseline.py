"""Test della fase generate_baseline con un backend finto (nessuna GPU)."""

from __future__ import annotations

import re
from typing import Any, ClassVar

import pytest

import bench.pipeline.stages.generate_baseline as gb
from bench.config.schema import DecodingConfig, ExperimentConfig, ModelSpec
from bench.generation.hf_generator import generation_seed
from bench.pipeline.facade import BenchmarkFacade
from tests.conftest import rebuild
from tests.datafix import HE_DEV, HE_TOTAL, MBPP_DEV, MBPP_TOTAL, build_tiny_datasets, tiny_config

MODEL = "qwen25_coder_7b"


class FakeBackend:
    """Restituisce codice che definisce l'entry point; il campione 5 non contiene codice."""

    calls: ClassVar[list[str]] = []
    fail_after: ClassVar[int | None] = None

    def __init__(self, model: ModelSpec, decoding: DecodingConfig) -> None:
        self.decoding = decoding

    def generate(self, messages: list[dict[str, str]], n: int, seed: int) -> list[str]:
        if FakeBackend.fail_after is not None and len(FakeBackend.calls) >= FakeBackend.fail_after:
            raise RuntimeError("simulated SLURM timeout")
        user = messages[1]["content"]
        entry = re.search(r"call `([^`]+)`", user).group(1)  # type: ignore[union-attr]
        FakeBackend.calls.append(entry)
        outs = [f"```\ndef {entry}(*args):\n    return {seed % 97 + i}\n```" for i in range(n - 1)]
        return [*outs, "I am not able to answer."]

    def info(self) -> dict[str, Any]:
        return {"generation_config": {"top_k": 0}, "device": "fake"}


@pytest.fixture
def facade(cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch) -> BenchmarkFacade:
    build_tiny_datasets(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    BenchmarkFacade(tcfg).run_stage("prepare_data")
    tcfg = rebuild(tcfg, models=[MODEL], languages=["python", "java"], splits=["dev", "test"])
    FakeBackend.calls = []
    FakeBackend.fail_after = None
    monkeypatch.setattr(gb, "BACKEND_FACTORY", FakeBackend)
    return BenchmarkFacade(tcfg)


def test_counts_seeds_and_extraction(facade: BenchmarkFacade) -> None:
    report = facade.run_stage("generate_baseline")
    assert report.ran == 4  # 1 modello x 2 linguaggi x 2 parti
    n = facade.cfg.decoding["level1"].n
    py_dev = facade.store.read_table(gb.baseline_ref(MODEL, "L1", "python", "dev"))
    assert len(py_dev) == (HE_DEV + MBPP_DEV) * n
    py_test = facade.store.read_table(gb.baseline_ref(MODEL, "L1", "python", "test"))
    assert len(py_test) == (HE_TOTAL - HE_DEV + MBPP_TOTAL - MBPP_DEV) * n
    for _, group in py_dev.groupby("problem_key"):
        assert sorted(group["sample_index"]) == list(range(n))
        assert group["seed"].nunique() == 1
        key = group["problem_key"].iloc[0]
        assert group["seed"].iloc[0] == generation_seed(20261001, MODEL, key, "python")
    assert set(py_dev["source"]) == {"llm_baseline"}
    assert set(py_dev["model_id"]) == {MODEL}
    assert py_dev["sample_id"].is_unique
    failed = py_dev[~py_dev["extraction_ok"]]
    assert set(failed["sample_index"]) == {n - 1} and (failed["code"] == "").all()

    manifest = facade.store.read_manifest(gb.baseline_ref(MODEL, "L1", "python", "dev"))
    assert manifest is not None
    e = manifest.extra
    assert e["extraction_rate"] == pytest.approx((n - 1) / n, abs=1e-6)
    assert e["system_prompt_sha256"] == facade.cfg.prompt.system_prompt_sha256
    assert "humanevalplus_python.j2" in e["prompt_hashes"]
    assert e["generation"]["generation_config"] == {"top_k": 0}
    assert manifest.n_rows_expected == manifest.n_rows_out


def test_second_run_is_skipped(facade: BenchmarkFacade) -> None:
    facade.run_stage("generate_baseline")
    calls = len(FakeBackend.calls)
    assert facade.run_stage("generate_baseline").skipped == 4
    assert len(FakeBackend.calls) == calls


def test_resume_after_interruption(facade: BenchmarkFacade) -> None:
    from bench.pipeline.stage import Cell

    cell = Cell(model_id=MODEL, level="L1", language="python", split="test")
    FakeBackend.fail_after = 3
    with pytest.raises(RuntimeError, match="simulated"):
        facade.run_stage("generate_baseline", cells=[cell])
    partial = facade.store.root / "baseline" / MODEL / "_partial" / "L1_python_test.jsonl"
    assert partial.is_file()
    done_before = list(FakeBackend.calls)
    FakeBackend.fail_after = None
    facade.run_stage("generate_baseline", cells=[cell])
    total = HE_TOTAL - HE_DEV + MBPP_TOTAL - MBPP_DEV
    assert len(FakeBackend.calls) == total  # nessun problema rigenerato
    assert FakeBackend.calls[:3] == done_before
    assert not partial.exists()
    manifest = facade.store.read_manifest(gb.baseline_ref(MODEL, "L1", "python", "test"))
    assert manifest is not None and manifest.extra["generation"]["device"] == "fake"


def test_partial_with_other_configuration_is_discarded(facade: BenchmarkFacade) -> None:
    from bench.pipeline.stage import Cell

    cell = Cell(model_id=MODEL, level="L1", language="java", split="dev")
    FakeBackend.fail_after = 0
    partial = facade.store.root / "baseline" / MODEL / "_partial" / "L1_java_dev.jsonl"
    with pytest.raises(RuntimeError):
        facade.run_stage("generate_baseline", cells=[cell])
    partial.write_text(
        '{"fingerprint": "old"}\n{"problem_key": "humaneval/0", "elapsed_s": 1, "samples": []}\n'
    )
    FakeBackend.fail_after = None
    facade.run_stage("generate_baseline", cells=[cell])
    df = facade.store.read_table(gb.baseline_ref(MODEL, "L1", "java", "dev"))
    assert len(df) == HE_DEV * facade.cfg.decoding["level1"].n
