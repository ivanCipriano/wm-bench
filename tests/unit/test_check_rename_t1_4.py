"""``scripts/check_rename_t1_4.py``: celle T1.4 dello store, etichette dei punti, conteggi sui
soli applicati, celle superate saltate, log che non si sovrascrive (``check_renaming`` finto)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from tests.unit.test_check_rename_dev import _fake_copy
from tests.unit.test_inventory_scripts import _load

script = _load("check_rename_t1_4")


def _touch(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_find_cells_and_labels(tmp_path: Path) -> None:
    labels = script.point_labels()
    assert len(labels) == 8 and "ratio=1.0 name_kind=natural" in labels.values()
    h = next(k for k, v in labels.items() if v == "ratio=1.0 name_kind=natural")
    _touch(tmp_path, f"attacked/T1.4/{h}/sweet/qwen/abc/L1_python_test.parquet")
    _touch(tmp_path, f"attacked/T1.4/{h}/mcss/qwen/def/L2_python_dev.parquet")
    _touch(tmp_path, f"attacked/T1.4/{h}/sweet/qwen/abc/L1_java_test.parquet")  # non Python
    _touch(tmp_path, f"attacked/T1.4/{h}/sweet/qwen/abc/L1_python_test.parquet.manifest.json")
    _touch(tmp_path, f"attacked/T1.5/{h}/sweet/qwen/abc/L1_python_test.parquet")  # altro attacco
    cells = script.find_cells(tmp_path)
    assert [(c.method, c.split, c.level, c.config_hash) for c in cells] == [
        ("mcss", "dev", "L2", "def"), ("sweet", "test", "L1", "abc")
    ]  # fmt: skip
    assert [c.method for c in script.find_cells(tmp_path, ["sweet"])] == ["sweet"]


def test_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.config import builder

    repo, commit = _fake_copy(tmp_path / "copy")
    art = tmp_path / "art"
    labels = script.point_labels()
    h = next(iter(labels))
    for rel in (f"attacked/T1.4/{h}/sweet/qwen/abc/L1_python_test.parquet",
                f"attacked/T1.4/{h}/stone/qwen/xyz/L1_python_test.parquet",
                "attacked/T1.4/0123456789ab/acw/qwen/ghi/L1_python_dev.parquet"):  # fmt: skip
        _touch(art, rel)
    cfg = SimpleNamespace(paths=SimpleNamespace(artifacts=art), models=["qwen"])
    monkeypatch.setattr(builder, "load_experiment", lambda overrides: cfg)
    attacked = pd.DataFrame({
        "sample_id": ["a1", "a2", "a3"], "parent_id": ["m1", "m2", "m3"],
        "problem_key": ["p1", "p2", "p3"], "code": ["x BUG", "x", "o"],
        "attack_status": ["APPLIED", "APPLIED", "UNCHANGED"],
    })  # fmt: skip
    marked = pd.DataFrame({"sample_id": ["m1", "m2", "m3"], "code": ["o", "o", "o"]})

    def read_cell(store: Any, c: Any) -> Any:
        return attacked, marked, {"a1": "FAILED"}, {"m1": "PASSED"}

    monkeypatch.setattr(script, "read_cell", read_cell)
    monkeypatch.setattr(script, "is_superseded", lambda store, c: c.method == "stone")
    out = tmp_path / "log.txt"
    argv = ["--multimark", str(repo), "--expect-commit", commit[:7], "--report", str(out)]
    assert script.main(argv) == 0
    text = out.read_text(encoding="utf-8")
    assert f"at commit {commit}" in text and "T1.4 not corrected" in text
    sweet = next(ln for ln in text.splitlines() if ln.startswith("  sweet"))
    # 3 campioni, 2 applicati e controllati, 1 violazione (50%) che fallisce mentre il marcato passa
    assert sweet.split()[-6:] == ["3", "2", "2", "1", "50.00%", "1"]
    assert labels[h] in sweet and "params 0123456789ab" in text
    assert "stone" not in text.split("## totals")[0] and "(1 superseded cells skipped)" in text
    assert "acw        dev  checked       2  violations      1 (50.00%)" in text
    assert script.main(argv) == 1  # il log esiste già
