"""Shim di ACW: limite di tempo dei lotti e ripiego campione per campione (D42)."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from bench_contracts import WorkerItem
from bench_contracts.enums import EmbedStatus

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "shims"))
acw = importlib.import_module("bench_shims.acw.__main__")
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="fork and process groups are POSIX-only")


def _item(item_id: str, code: str) -> WorkerItem:
    return WorkerItem(item_id=item_id, language="python", seed=0, prompt_messages=None, code=code,
                      context_prompt=None, expected_message=None, n=1)  # fmt: skip


def _shim(tmp_path: Path) -> Any:
    shim = acw.AcwShim.__new__(acw.AcwShim)  # senza setup: niente Sourcery
    shim.workdir = str(tmp_path)
    shim.rules = [1, 2]
    return shim


def test_slow_item_fails_alone_and_others_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il lotto scade per colpa di un campione: gli altri si rifanno da soli, quello è FAILED."""
    shim = _shim(tmp_path)

    def fake_in_child(func: Any, timeout_s: float) -> tuple[bool, Any]:
        out = func()  # il core qui sotto segnala il campione lento con None
        return (False, None) if out is None else (True, out)

    def core(items: list[WorkerItem]) -> Any:
        if any(it.code == "huge" for it in items):
            return None
        extra = {"sourcery_calls_batch": 1, "sourcery_calls_per_sample": 1 / len(items)}
        return [shim.result(it, EmbedStatus.OK, code=it.code, extra=dict(extra)) for it in items], 1

    def on_timeout(item: WorkerItem, extra: dict[str, Any]) -> Any:
        return shim.result(item, EmbedStatus.FAILED, error=acw.TIMEOUT_ERROR, extra=extra)

    monkeypatch.setattr(shim, "_in_child", fake_in_child)
    items = [_item("a", "x = 1"), _item("b", "huge"), _item("c", "y = 2")]
    results, calls = shim._guarded(items, core, on_timeout)
    assert [r.item_id for r in results] == ["a", "b", "c"]  # stesso ordine
    assert [r.status for r in results] == ["OK", "FAILED", "OK"]
    assert results[1].error == "sourcery timeout" and results[1].extra["sourcery_timeout"]
    assert all(r.extra["batch_timeout_fallback"] for r in results)
    assert calls == 2  # una chiamata per ognuno dei due campioni rifatti da soli
    shim._add_calls(results, calls, 1)
    assert results[0].extra["sourcery_calls_batch"] == 3
    assert "sourcery_calls_batch" not in results[1].extra


def test_batch_within_limit_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = _shim(tmp_path)
    monkeypatch.setattr(shim, "_in_child", lambda func, timeout_s: (True, func()))
    items = [_item("a", "x = 1")]
    out = ([shim.result(items[0], EmbedStatus.OK, extra={})], 4)
    results, calls = shim._guarded(items, lambda its: out, lambda it, ex: None)
    assert calls == 4 and "batch_timeout_fallback" not in results[0].extra


@POSIX
def test_run_with_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    """Allo scadere muore anche il processo lanciato dalla shell (come Sourcery)."""
    marker = tmp_path / "pid"

    def slow() -> None:
        proc = subprocess.Popen(["sleep", "60"])
        marker.write_text(str(proc.pid))
        proc.wait()

    start = time.time()
    assert acw.run_with_timeout(slow, 2) == (False, None)
    assert time.time() - start < 20
    pid = int(marker.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert acw.run_with_timeout(lambda: 41 + 1, 10) == (True, 42)
    with pytest.raises(RuntimeError, match="ZeroDivisionError"):
        acw.run_with_timeout(lambda: 1 / 0, 10)
