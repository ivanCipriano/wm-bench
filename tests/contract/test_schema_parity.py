"""Parità tra le dataclass di bench_contracts e i modelli Pydantic speculari (SPEC §21.2)."""

from __future__ import annotations

import dataclasses
import json

import pytest
from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from pydantic import BaseModel

from bench.domain.models import WorkerItemModel, WorkerRequestModel, WorkerResultModel

pytestmark = pytest.mark.contract

PAIRS = [
    (WorkerRequest, WorkerRequestModel),
    (WorkerItem, WorkerItemModel),
    (WorkerResult, WorkerResultModel),
]


@pytest.mark.parametrize(("dc", "model"), PAIRS, ids=lambda x: x.__name__)
def test_same_fields_in_same_order(dc: type, model: type[BaseModel]) -> None:
    assert [f.name for f in dataclasses.fields(dc)] == list(model.model_fields)


@pytest.mark.parametrize(("dc", "model"), PAIRS, ids=lambda x: x.__name__)
def test_same_required_fields(dc: type, model: type[BaseModel]) -> None:
    dc_required = {
        f.name
        for f in dataclasses.fields(dc)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    model_required = {name for name, f in model.model_fields.items() if f.is_required()}
    assert dc_required == model_required


def test_roundtrip_dataclass_to_model_and_back() -> None:
    result = WorkerResult(
        item_id="s1",
        sample_index=None,
        status="OK",
        raw_output=None,
        code="x = 1",
        score=2.5,
        native_decision=True,
        decoded_message=None,
        extra={"z": 2.5},
    )
    model = WorkerResultModel.model_validate(json.loads(json.dumps(result.to_dict())))
    assert WorkerResult.from_dict(model.model_dump()) == result
