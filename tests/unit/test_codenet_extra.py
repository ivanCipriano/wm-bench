"""Lista delle sottomissioni CodeNet in più per la verifica di mcss (``codenet_extra.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.data.codenet import pick_submissions
from bench.domain.enums import Language
from tests.unit.test_inventory_scripts import _load

extra = _load("codenet_extra")

SEED = 20261001


def _rows(n: int) -> list[dict[str, str]]:
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


def test_problem_groups_dev_and_unused_outside_test_clusters() -> None:
    splits: dict[str, str | None] = {"p00001": "dev", "p00002": "test", "p00003": "test"}
    candidates = ["p00001", "p00002", "p00003", "p00010", "p00011", "p00012", "p00013"]
    clusters = {"p00002": 0, "p00010": 0, "p00001": 1, "p00011": 1, "p00012": 2}
    groups, dropped = extra.problem_groups(candidates, splits, clusters)
    # p00010 sta nel cluster del test p00002; p00011 in quello di un dev: resta
    assert groups == {"p00001": "dev", "p00011": "unused", "p00012": "unused", "p00013": "unused"}
    assert dropped == ["p00010"]


def test_plan_extends_the_m9_picks_and_skips_present_files() -> None:
    metadata = {"p00001": _rows(80), "p00002": _rows(20)}
    m9 = {
        pid: pick_submissions(SEED, pid, Language.PYTHON, rows, set(), {}, 10)
        for pid, rows in metadata.items()
    }
    present = {s.relpath(pid) for pid, subs in m9.items() for s in subs}
    groups = {"p00002": "unused", "p00001": "dev"}
    paths, summary = extra.plan(SEED, groups, metadata, set(), {}, present, 60)
    # le prime 10 con limit 60 sono quelle di M9 (stesso ordine, stessi filtri)
    for pid, subs in m9.items():
        longer = pick_submissions(SEED, pid, Language.PYTHON, metadata[pid], set(), {}, 60)
        assert longer[:10] == subs
    dev, unused = summary["groups"]["dev"], summary["groups"]["unused"]
    assert summary["limit"] == 60 and dev["problems"] == 1 and unused["problems"] == 1
    assert dev["picked"] == 60 and dev["to_extract"] == 50 and dev["problems_below_limit"] == 0
    assert unused["picked"] == 18 and unused["to_extract"] == 8  # 20 accettate, 18 utenti
    assert unused["problems_below_limit"] == 1 and dev["already_present"] == 10
    assert summary["to_extract"] == 58 and summary["problems"]["p00002"]["group"] == "unused"
    assert len(paths) == 58 and paths == sorted(paths)
    assert all(p.startswith("Project_CodeNet/data/p0000") and p.endswith(".py") for p in paths)
    assert not set(paths) & {f"Project_CodeNet/data/{x}" for x in present}
    text = extra.report(summary, SEED, ["p00010"])
    assert "[dev] to extract: 50" in text and "[unused] to extract: 8" in text
    assert "same cluster as a test problem): 1" in text
    assert "one pass over the archive): 58" in text


def test_plan_counts_m9_files_outside_the_picks() -> None:
    present = {"p00001/Python/s123456789.py"}
    _, summary = extra.plan(SEED, {"p00001": "dev"}, {"p00001": _rows(15)}, set(), {}, present)
    assert summary["groups"]["dev"]["present_not_picked"] == 1
    assert summary["problems"]["p00001"]["present_not_picked"] == ["p00001/Python/s123456789.py"]


def test_write_if_new_refuses_changes(tmp_path: Path) -> None:
    path = tmp_path / "list.txt"
    assert extra.write_if_new(path, b"a\n") == "written"
    assert extra.write_if_new(path, b"a\n") == "unchanged"
    with pytest.raises(extra.ListError):
        extra.write_if_new(path, b"b\n")
