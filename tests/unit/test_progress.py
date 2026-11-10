"""``scripts/progress.py``: campioni attesi e fatti di un'esecuzione dei worker."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from tests.conftest import REPO_ROOT


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "wmb_progress", REPO_ROOT / "scripts" / "progress.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


progress = _load()


def _jsonl(path: Path, rows: list[dict[str, object]], tail: str = "") -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + tail, encoding="utf-8")


def test_expected_counts_samples_of_prompt_items(tmp_path: Path) -> None:
    items = tmp_path / "items.jsonl"
    _jsonl(items, [{"item_id": "p1", "prompt_messages": [{"role": "user"}], "n": 6},
                   {"item_id": "s1", "prompt_messages": None, "code": "x", "n": 1},
                   {"item_id": "s2", "prompt_messages": None, "code": "y", "n": 6}])  # fmt: skip
    assert progress.expected(items) == 8  # 6 dal prompt, 1 per ogni codice


def test_done_counts_each_sample_once(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    rows = [{"item_id": "a", "sample_index": 0, "elapsed_s": 1.0},
            {"item_id": "a", "sample_index": 1, "elapsed_s": 3.0},
            {"item_id": "a", "sample_index": 0, "elapsed_s": 2.0}]  # fmt: skip
    _jsonl(results, rows, tail='{"item_id": "b", "sampl')  # riga a metà: ignorata
    assert progress.done(results) == (2, 2.5)
    assert progress.done(tmp_path / "missing.jsonl") == (0, None)
