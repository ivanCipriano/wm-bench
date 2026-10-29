"""Test della fase generate_baseline con un backend finto (nessuna GPU)."""

from __future__ import annotations

import re
from typing import Any, ClassVar

import pandas as pd
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
        match = re.search(r"call `([^`]+)`", user)
        entry = match.group(1) if match else "main"  # CodeNet (L2): programma, nessun entry point
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


class RecordingBackend(FakeBackend):
    """Come ``FakeBackend``, con il ``max_new_tokens`` di ogni backend creato."""

    created: ClassVar[list[int]] = []

    def __init__(self, model: ModelSpec, decoding: DecodingConfig) -> None:
        super().__init__(model, decoding)
        RecordingBackend.created.append(decoding.max_new_tokens)


def test_classeval_has_its_own_decoding(
    cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D40: nella cella L3 Python ClassEval usa 2.048 token, gli altri dataset 1.024."""
    from bench.domain.enums import Language, Level, Split
    from bench.domain.models import Problem
    from bench.pipeline.stage import Cell, StageContext

    assert gb.decoding_for(cfg, "L3", "classeval").max_new_tokens == 2048
    assert gb.decoding_for(cfg, "L3", "codesearchnet").max_new_tokens == 1024
    assert gb.decoding_for(cfg, "L1", "classeval").max_new_tokens == 512
    problems = [
        Problem(problem_key=key, dataset=dataset, level=Level.L3, language=Language.PYTHON,
                split=Split.TEST, prompt_text="def f():\n", entry_point="f",
                canonical_solution=None, test_ref=None, contamination_risk=False,
                loc_to_generate=1)
        for key, dataset in (("classeval/ClassEval_0", "classeval"), ("humaneval/0",
                                                                       "humanevalplus"))
    ]  # fmt: skip
    assert set(gb.decoding_overrides(cfg, "L3", problems)) == {"classeval"}
    assert gb.decoding_overrides(cfg, "L3", problems[1:]) == {}
    tcfg = rebuild(cfg, models=[MODEL], levels=["L3"], languages=["python"], splits=["test"])
    facade = BenchmarkFacade(tcfg)
    ref = gb.problems_ref("L3", "python")
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "test", Cell())
    manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None)
    facade.store.write_table(
        ref, pd.DataFrame([p.model_dump(mode="json") for p in problems]), manifest
    )
    RecordingBackend.created = []
    monkeypatch.setattr(gb, "BACKEND_FACTORY", RecordingBackend)
    cell = Cell(model_id=MODEL, level="L3", language="python", split="test")
    facade.run_stage("generate_baseline", cells=[cell])
    assert sorted(RecordingBackend.created) == [1024, 2048]
    out = facade.store.read_manifest(gb.baseline_ref(MODEL, "L3", "python", "test"))
    assert out is not None
    assert out.extra["decoding"]["max_new_tokens"] == 1024
    assert out.extra["decoding_by_dataset"]["classeval"]["max_new_tokens"] == 2048
