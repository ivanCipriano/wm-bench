"""Test della lettura/scrittura JSONL e della ripresa (SPEC §8.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from bench_contracts import (
    DetectStatus,
    WorkerResult,
    append_jsonl,
    iter_jsonl,
    read_done_ids,
    write_jsonl,
)


def _result(item_id: str) -> WorkerResult:
    return WorkerResult(
        item_id=item_id,
        sample_index=None,
        status=DetectStatus.OK,
        raw_output=None,
        code="def f():\n    return 'è'\n",
        score=1.25,
        native_decision=True,
        decoded_message=None,
        extra={"z": 1.25},
    )


def test_roundtrip_without_loss(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    results = [_result(f"s{i}") for i in range(5)]
    assert write_jsonl(path, (r.to_dict() for r in results)) == 5
    back = [WorkerResult.from_dict(d) for d in iter_jsonl(path)]
    assert back == results


def test_append_and_resume(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    assert read_done_ids(path) == set()
    append_jsonl(path, _result("a").to_dict())
    append_jsonl(path, _result("b").to_dict())
    assert read_done_ids(path) == {"a", "b"}


def test_truncated_last_line_ignored(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    append_jsonl(path, _result("a").to_dict())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"item_id": "b", "sta')
    assert read_done_ids(path) == {"a"}


def test_corrupted_middle_line_raises(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    path.write_text('{"item_id": "a"}\nnot json\n{"item_id": "b"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        list(iter_jsonl(path))


def test_nan_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        append_jsonl(tmp_path / "out.jsonl", {"score": float("nan")})
