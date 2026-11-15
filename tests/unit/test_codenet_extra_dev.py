"""Lista delle sottomissioni CodeNet in più dei problemi di sviluppo (``codenet_extra_dev.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.data.codenet import pick_submissions
from bench.domain.enums import Language
from tests.unit.test_inventory_scripts import _load

extra = _load("codenet_extra_dev")

SEED = 20261001


def _rows(pid: str, n: int) -> list[dict[str, str]]:
    rows = [
        {
            "submission_id": f"s{i:09}",
            "user_id": f"u{i % (n - 2)}",  # due utenti ripetuti: saltati
            "language": "Python",
            "status": "Accepted",
            "original_language": "Python (3.8.2)",
            "filename_ext": "py",
        }
        for i in range(n)
    ]
    rows.append({**rows[0], "submission_id": "s999999998", "status": "Wrong Answer"})
    rows.append({**rows[0], "submission_id": "s999999999", "original_language": "Python (2.7)"})
    return rows


def test_plan_extends_the_m9_picks_and_skips_present_files() -> None:
    metadata = {"p00001": _rows("p00001", 80), "p00002": _rows("p00002", 20)}
    m9 = {
        pid: pick_submissions(SEED, pid, Language.PYTHON, rows, set(), {}, 10)
        for pid, rows in metadata.items()
    }
    present = {s.relpath(pid) for pid, subs in m9.items() for s in subs}
    paths, summary = extra.plan(SEED, ["p00002", "p00001"], metadata, set(), {}, present, 60)
    # le prime 10 con limit 60 sono quelle di M9 (stesso ordine, stessi filtri)
    for pid, subs in m9.items():
        longer = pick_submissions(SEED, pid, Language.PYTHON, metadata[pid], set(), {}, 60)
        assert longer[:10] == subs
    assert summary["dev_problems"] == 2 and summary["limit"] == 60
    assert summary["picked"] == 60 + 18  # p00002: 20 accettate, 18 utenti distinti
    assert summary["already_present"] == 20 and summary["to_extract"] == 58
    assert summary["present_not_picked"] == 0 and summary["problems_below_limit"] == 1
    assert len(paths) == 58 and paths == sorted(paths)
    assert all(p.startswith("Project_CodeNet/data/p0000") and p.endswith(".py") for p in paths)
    assert not set(paths) & {f"Project_CodeNet/data/{x}" for x in present}
    assert "to extract: 58" in extra.report(summary, SEED)


def test_plan_counts_m9_files_outside_the_picks() -> None:
    metadata = {"p00001": _rows("p00001", 15)}
    present = {"p00001/Python/s123456789.py"}
    _, summary = extra.plan(SEED, ["p00001"], metadata, set(), {}, present, 60)
    assert summary["present_not_picked"] == 1
    assert summary["problems"]["p00001"]["present_not_picked"] == ["p00001/Python/s123456789.py"]


def test_write_if_new_refuses_changes(tmp_path: Path) -> None:
    path = tmp_path / "list.txt"
    assert extra.write_if_new(path, b"a\n") == "written"
    assert extra.write_if_new(path, b"a\n") == "unchanged"
    with pytest.raises(extra.ListError):
        extra.write_if_new(path, b"b\n")
