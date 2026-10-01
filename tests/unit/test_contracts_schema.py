"""Test delle dataclass di contratto (SPEC §5.1)."""

from __future__ import annotations

import json

import pytest
from bench_contracts import (
    SCHEMA_VERSION,
    ContractError,
    DetectStatus,
    EmbedStatus,
    WorkerItem,
    WorkerOp,
    WorkerRequest,
    WorkerResult,
)
from bench_contracts.schema import MAX_ERROR_CHARS


def _request(**overrides: object) -> WorkerRequest:
    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "op": WorkerOp.EMBED,
        "method": "stone",
        "model_id": "qwen25_coder_7b",
        "model_path": "/models/qwen",
        "tokenizer_path": "/models/qwen",
        "hparams": {"gamma": 0.5, "delta": 2.0},
        "key": 12345,
        "key_id": "k1",
        "decoding": {"temperature": 0.2, "top_p": 0.95, "max_new_tokens": 512, "n": 6},
        "system_prompt": "You are a helpful assistant.",
        "items_path": "items.jsonl",
        "output_path": "out.jsonl",
        "device": "cuda:0",
        "log_path": "worker.log",
    }
    data.update(overrides)
    return WorkerRequest.from_dict(data)


def test_request_roundtrip_through_json() -> None:
    req = _request()
    again = WorkerRequest.from_dict(json.loads(json.dumps(req.to_dict())))
    assert again == req
    again.check_version()


def test_request_wrong_version_rejected() -> None:
    with pytest.raises(ContractError):
        _request(schema_version="0.9").check_version()


def test_unknown_field_rejected() -> None:
    with pytest.raises(ContractError, match="unknown"):
        _request(extra_field=1)


def test_missing_field_rejected() -> None:
    data = _request().to_dict()
    del data["key"]
    with pytest.raises(ContractError, match="missing"):
        WorkerRequest.from_dict(data)


def test_item_roundtrip() -> None:
    item = WorkerItem(
        item_id="p1",
        language="python",
        seed=7,
        prompt_messages=[{"role": "user", "content": "Scrivi una funzione"}],
        code=None,
        context_prompt=None,
        expected_message="0" * 24,
        n=6,
    )
    assert WorkerItem.from_dict(json.loads(json.dumps(item.to_dict()))) == item


def test_result_defaults_and_error_truncation() -> None:
    res = WorkerResult.from_dict(
        {
            "item_id": "p1",
            "sample_index": 0,
            "status": EmbedStatus.FAILED,
            "raw_output": None,
            "code": None,
            "score": None,
            "native_decision": None,
            "decoded_message": None,
            "error": "x" * (MAX_ERROR_CHARS + 50),
        }
    )
    assert res.extra == {}
    assert res.elapsed_s == 0.0
    assert res.error is not None
    assert len(res.error) == MAX_ERROR_CHARS


def test_status_constants() -> None:
    assert {"OK", "PARTIAL", "FAILED", "NOT_APPLICABLE"} == EmbedStatus.ALL
    assert {"OK", "FAILED", "NOT_APPLICABLE"} == DetectStatus.ALL
    assert {"embed", "detect"} == WorkerOp.ALL
