"""Test dei modelli di dominio e delle eccezioni (SPEC §5.2, §17)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from bench.domain.enums import KNOWN_METHODS, Language, Level
from bench.domain.errors import (
    BenchError,
    ConfigError,
    ContractVersionError,
    InvariantViolation,
    MissingInputError,
    SandboxError,
    ToolError,
    WorkerError,
)
from bench.domain.models import (
    CodeSample,
    DetectionRecord,
    Problem,
    WorkerItemModel,
    WorkerRequestModel,
    WorkerResultModel,
)


def _problem(**kw: Any) -> Problem:
    data: dict[str, Any] = {
        "problem_key": "humaneval/0",
        "dataset": "humanevalplus",
        "level": "L1",
        "language": "python",
        "split": "dev",
        "prompt_text": "def f():",
        "entry_point": "f",
        "canonical_solution": "    return 1",
        "test_ref": "HumanEval/0",
        "contamination_risk": False,
        "loc_to_generate": 1,
    }
    data.update(kw)
    return Problem.model_validate(data)


def test_problem_parses_enums_and_is_frozen() -> None:
    p = _problem()
    assert p.language is Language.PYTHON
    assert p.level is Level.L1
    with pytest.raises(ValidationError):
        p.problem_key = "x"  # type: ignore[misc]


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        _problem(unexpected=1)


def test_invalid_enum_rejected() -> None:
    with pytest.raises(ValidationError):
        _problem(language="cobol")


def test_code_sample_embed_status_validated() -> None:
    base: dict[str, Any] = dict.fromkeys(CodeSample.model_fields)
    base.update(
        sample_id="s",
        problem_key="mbpp/1",
        dataset="mbppplus",
        language="python",
        level="L1",
        split="test",
        source="llm_watermarked",
        code="x = 1",
        extraction_ok=True,
        contamination_risk=False,
        embed_status="OK",
    )
    assert CodeSample.model_validate(base).embed_status == "OK"
    base["embed_status"] = "MAYBE"
    with pytest.raises(ValidationError):
        CodeSample.model_validate(base)


def test_detection_status_validated() -> None:
    data: dict[str, Any] = {
        "sample_id": "s",
        "method": "sweet",
        "model_id": "m",
        "config_hash": "c",
        "key_id": "k1",
        "status": "OK",
        "score": 1.0,
        "native_decision": None,
        "decoded_message": None,
        "bits_correct": None,
        "extra": {},
    }
    DetectionRecord.model_validate(data)
    with pytest.raises(ValidationError):
        DetectionRecord.model_validate({**data, "status": "PARTIAL"})


def test_worker_models_validate_contract_values() -> None:
    with pytest.raises(ValidationError):
        WorkerItemModel.model_validate(
            {
                "item_id": "p",
                "language": "python",
                "seed": 1,
                "prompt_messages": None,
                "code": None,
                "context_prompt": None,
                "expected_message": "0102",
                "n": 6,
            }
        )
    with pytest.raises(ValidationError):
        WorkerResultModel.model_validate(
            {
                "item_id": "p",
                "sample_index": 0,
                "status": "WEIRD",
                "raw_output": None,
                "code": None,
                "score": None,
                "native_decision": None,
                "decoded_message": None,
            }
        )
    request: dict[str, Any] = dict.fromkeys(WorkerRequestModel.model_fields, "x")
    request.update(schema_version="1.0", op="detect", key=1, hparams={}, decoding={})
    WorkerRequestModel.model_validate(request)
    with pytest.raises(ValidationError):
        WorkerRequestModel.model_validate({**request, "schema_version": "0.1"})


def test_known_methods_are_the_five_of_the_benchmark() -> None:
    assert {"sweet", "acw", "stone", "promptmark", "mcgmark"} == KNOWN_METHODS


def test_error_hierarchy() -> None:
    for exc in (
        ConfigError,
        MissingInputError,
        WorkerError,
        SandboxError,
        ToolError,
        InvariantViolation,
        ContractVersionError,
    ):
        assert issubclass(exc, BenchError)
    err = MissingInputError("data/problems.parquet", "prepare_data")
    assert "run stage 'prepare_data' first" in str(err)
    assert err.producer == "prepare_data"
