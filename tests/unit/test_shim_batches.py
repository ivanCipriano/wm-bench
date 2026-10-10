"""Runner degli shim a lotti (``ShimBase.batch_size``): ordine, ripresa ed errori per lotto."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from bench_contracts import SCHEMA_VERSION, WorkerItem, WorkerRequest, iter_jsonl, write_jsonl

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "shims"))
import pytest
from bench_shims._common.base import FatalShimError, ShimBase
from bench_shims._common.runner import run


class BatchShim(ShimBase):
    method = "batchtest"
    batch_size = 3

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def detect_batch(self, items: list[WorkerItem]) -> list[Any]:
        self.batches.append([it.item_id for it in items])
        if any(it.code == "boom" for it in items):
            raise RuntimeError("tool error")
        if any(it.code == "expired" for it in items):
            raise FatalShimError("license expired")
        return [self.result(it, "OK", score=float(len(it.code or ""))) for it in items]


def _request(tmp_path: Path, items: list[WorkerItem]) -> WorkerRequest:
    write_jsonl(tmp_path / "items.jsonl", [it.to_dict() for it in items])
    return WorkerRequest(
        schema_version=SCHEMA_VERSION,
        op="detect",
        method="batchtest",
        model_id="m",
        model_path="",
        tokenizer_path="",
        hparams={},
        key=1,
        key_id="k1",
        decoding={},
        system_prompt="",
        items_path=str(tmp_path / "items.jsonl"),
        output_path=str(tmp_path / "results.jsonl"),
        device="cpu",
        log_path=str(tmp_path / "worker.log"),
    )


def _item(i: int, code: str) -> WorkerItem:
    return WorkerItem(
        item_id=f"s{i}",
        language="python",
        seed=0,
        prompt_messages=None,
        code=code,
        context_prompt=None,
        expected_message=None,
        n=1,
    )


def test_batches_keep_order_fail_together_and_resume(tmp_path: Path) -> None:
    items = [_item(i, "x" * i) for i in range(7)]
    items[4] = _item(4, "boom")
    shim = BatchShim()
    request = _request(tmp_path, items)
    assert run(shim, request) == 0
    assert shim.batches == [["s0", "s1", "s2"], ["s3", "s4", "s5"], ["s6"]]
    rows = list(iter_jsonl(request.output_path))
    assert [r["item_id"] for r in rows] == [f"s{i}" for i in range(7)]
    by_id = {r["item_id"]: r for r in rows}
    assert by_id["s2"]["status"] == "OK" and by_id["s2"]["score"] == 2.0
    # Il lotto con l'errore fallisce per intero, gli altri no.
    assert {by_id[k]["status"] for k in ("s3", "s4", "s5")} == {"FAILED"}
    assert "tool error" in by_id["s4"]["error"] and by_id["s6"]["status"] == "OK"
    # Ripresa: nessun item da rifare.
    again = BatchShim()
    assert run(again, request) == 0 and again.batches == []


def test_fatal_error_stops_without_writing_the_batch(tmp_path: Path) -> None:
    items = [_item(i, "x" * i) for i in range(7)]
    items[4] = _item(4, "expired")
    request = _request(tmp_path, items)
    with pytest.raises(FatalShimError):
        run(BatchShim(), request)
    # Solo il primo lotto è scritto: il lotto con l'errore e i successivi no.
    assert [r["item_id"] for r in iter_jsonl(request.output_path)] == ["s0", "s1", "s2"]
    # Con lo strumento di nuovo funzionante la ripresa rifà solo gli item mancanti.
    write_jsonl(tmp_path / "items.jsonl", [_item(i, "x" * i).to_dict() for i in range(7)])
    again = BatchShim()
    assert run(again, request) == 0
    assert again.batches == [["s3", "s4", "s5"], ["s6"]]
