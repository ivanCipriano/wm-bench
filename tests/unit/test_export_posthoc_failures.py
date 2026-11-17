"""``scripts/export_posthoc_failures.py``: casi falliti, incoerenti o non coperti dei record di
``scripts/mcss_paper_attacks.py``, con codice, siti scritti, siti letti e differenze."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.unit.test_inventory_scripts import LIBRARY, _load, needs_library
from tests.unit.test_mcss_paper_attacks import _selection, _synthetic

pytestmark = needs_library


@pytest.fixture(scope="module")
def attacks() -> ModuleType:
    return _load("mcss_paper_attacks")


@pytest.fixture(scope="module")
def export() -> ModuleType:
    return _load("export_posthoc_failures")


def _records(attacks: ModuleType, sel: Path, tmp_path: Path) -> Path:
    records = tmp_path / "records.jsonl"
    argv = ["--selection", str(sel), "--report", str(tmp_path / "report.txt"), "--records",
            str(records), "--library", str(LIBRARY)]  # fmt: skip
    assert attacks.main(argv) == 0
    return records


def _export(export: ModuleType, records: Path, sel: Path, out: Path) -> list[dict[str, Any]]:
    argv = ["--records", str(records), "--selection", str(sel), "--out", str(out),
            "--library", str(LIBRARY)]  # fmt: skip
    assert export.main(argv) == 0
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def test_partial_and_empty_cases_are_exported(
    attacks: ModuleType, export: ModuleType, tmp_path: Path
) -> None:
    codes = [_synthetic(1, 34), _synthetic(2, 4), "def f():\n    return 1\n"]
    sel = _selection(tmp_path / "sel.jsonl", codes)
    records = _records(attacks, sel, tmp_path)
    out = tmp_path / "failures.jsonl"
    rows = _export(export, records, sel, out)
    by = {(r["sample_id"], r["method"]): r for r in rows}
    assert ("s0", "mcss") not in by  # caso riuscito: non esportato
    assert all(r["reproduced"] is not False for r in rows)
    assert all(r["message_matches_record"] for r in rows)
    for method in ("mcss", "multimark"):
        empty = by[("s2", method)]
        assert empty["reasons"] == ["embed_no_sites"] and empty["marked_code_kind"] == "none"
        short = by[("s1", method)]
        assert "not_covered" in short["reasons"] and short["marked_code_kind"] == "final"
        assert short["original_code"] == codes[1] and short["marked_code"] != codes[1]
        diff = short["differences"]
        voted = {v["position"] for s in short["detection_sites"] for v in s["votes"]}
        assert sorted(set(range(32)) - voted) == diff["uncovered_positions"]
        assert diff["lost_sites"] == [] and diff["value_differs"] == []
        structural = [s for s in short["embedding_sites"] if s["kind"] == "structural"]
        assert structural and all(s["written_value"] in (0, 1) for s in structural)
        assert all(len(s["positions"]) >= 1 for s in short["embedding_sites"])
        assert len(diff["tally"]) == 32
    assert export.main(["--records", str(records), "--selection", str(sel), "--out", str(out),
                        "--library", str(LIBRARY)]) == 1  # fmt: skip


def test_rewrite_mismatch_keeps_the_last_attempt(
    attacks: ModuleType, export: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attacks.import_library(LIBRARY)
    import multimark.embed as emb
    from multimark.rewrite import RewriteError

    def broken(code: str, index: Any, requests: Any) -> Any:
        raise RewriteError("forced by the test")

    monkeypatch.setattr(emb, "write_structural", broken)
    sel = _selection(tmp_path / "sel.jsonl", [_synthetic(3, 30)])
    records = _records(attacks, sel, tmp_path)
    rows = _export(export, records, sel, tmp_path / "failures.jsonl")
    assert {r["method"] for r in rows} == {"mcss", "multimark"}
    for r in rows:
        assert r["reasons"] == ["embed_rewrite_mismatch"] and r["reproduced"] is True
        assert r["embed"]["error"] == "forced by the test"
        assert r["marked_code_kind"] == "last_attempt"  # codice dopo le rinomine
        assert r["embedding_sites"] and r["differences"] is not None
