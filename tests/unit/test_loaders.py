"""Test dei loader di L1 sulla fixture tiny."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import bench.data.loaders  # noqa: F401  (popola LOADERS)
from bench.config.schema import DatasetSpec, ExperimentConfig
from bench.data.loaders.base import parse_task_number
from bench.data.loaders.humanevalpack import HumanEvalPackLoader
from bench.data.loaders.humanevalplus import HumanEvalPlusLoader
from bench.data.loaders.mbpp_original import MbppOriginalLoader
from bench.data.loaders.mbppplus import MbppPlusLoader
from bench.domain.enums import Language, Source
from bench.domain.errors import DataError
from bench.registry import LOADERS
from tests.datafix import build_tiny_datasets, tiny_config


@pytest.fixture
def tcfg(cfg: ExperimentConfig) -> ExperimentConfig:
    build_tiny_datasets(cfg.paths.datasets)
    return tiny_config(cfg)


def test_registry() -> None:
    assert {"humanevalplus", "mbppplus", "mbpp_original", "humanevalpack"} <= set(LOADERS.names())


def test_humanevalplus(tcfg: ExperimentConfig) -> None:
    loader = HumanEvalPlusLoader(tcfg.datasets["humanevalplus"])
    problems = loader.load_problems(Language.PYTHON)
    assert [p.problem_key for p in problems] == [f"humaneval/{i}" for i in range(5)]
    assert all(p.split is None and p.test_ref == f"HumanEval/{i}" for i, p in enumerate(problems))
    assert all(p.loc_to_generate and p.loc_to_generate > 0 for p in problems)
    negatives = loader.load_human_negatives(Language.PYTHON)
    assert negatives[0].code.startswith(problems[0].prompt_text)
    assert all(n.source is Source.HUMAN and not n.contamination_risk for n in negatives)
    with pytest.raises(DataError, match="not supported"):
        loader.load_problems(Language.JAVA)


def test_mbppplus_counts_whole_solution(tcfg: ExperimentConfig) -> None:
    loader = MbppPlusLoader(tcfg.datasets["mbppplus"])
    problems = loader.load_problems(Language.PYTHON)
    assert [p.problem_key for p in problems] == ["mbpp/2", "mbpp/3", "mbpp/4", "mbpp/6", "mbpp/7"]
    assert problems[0].loc_to_generate == 2  # def + return
    assert loader.load_human_negatives(Language.PYTHON) == []


def test_mbpp_original_union_and_newlines(tcfg: ExperimentConfig) -> None:
    loader = MbppOriginalLoader(tcfg.datasets["mbpp_original"])
    assert loader.load_problems(Language.PYTHON) == []
    negatives = loader.load_human_negatives(Language.PYTHON)
    assert [n.problem_key for n in negatives] == [f"mbpp/{i}" for i in range(1, 13)]
    assert all("\r" not in n.code for n in negatives)


def test_humanevalpack_maps_ids_and_js_dir(tcfg: ExperimentConfig) -> None:
    loader = HumanEvalPackLoader(tcfg.datasets["humanevalpack"])
    for lang in (Language.JAVA, Language.CPP, Language.JAVASCRIPT):
        problems = loader.load_problems(lang)
        assert [p.problem_key for p in problems] == [f"humaneval/{i}" for i in range(5)]
        assert all(p.language is lang for p in problems)
    assert loader.python_keys() == {f"humaneval/{i}" for i in range(5)}
    assert (
        str(tcfg.datasets["humanevalpack"].file("js"))
        .replace("\\", "/")
        .endswith("humanevalpack/js/test-00000-of-00001.parquet")
    )


def test_missing_column_lists_found_columns(tcfg: ExperimentConfig, tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    pd.DataFrame({"task_id": [1]}).to_parquet(path, index=False)
    spec = DatasetSpec(
        name="mbpp_original", files={r: path for r in ("train", "test", "validation", "prompt")}
    )
    with pytest.raises(DataError, match=r"missing columns \['code'\]; found \['task_id'\]"):
        MbppOriginalLoader(spec).load_human_negatives(Language.PYTHON)


def test_row_count_mismatch(tcfg: ExperimentConfig) -> None:
    spec = tcfg.datasets["humanevalplus"].model_copy(update={"expected_rows": {"data": 164}})
    with pytest.raises(DataError, match="5 rows, expected 164"):
        HumanEvalPlusLoader(spec).load_problems(Language.PYTHON)


def test_missing_file(tmp_path: Path) -> None:
    spec = DatasetSpec(name="humanevalplus", files={"data": tmp_path / "nope.jsonl"})
    with pytest.raises(DataError, match="file not found"):
        HumanEvalPlusLoader(spec).load_problems(Language.PYTHON)


def test_task_id_parsing() -> None:
    assert parse_task_number("Java/12", "Java") == 12
    with pytest.raises(DataError):
        parse_task_number("CPP/12", "Java")
