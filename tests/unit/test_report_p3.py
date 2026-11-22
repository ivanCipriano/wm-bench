"""``scripts/report_p3.py``: ripiego del proponente neurale, tempi di inserimento e regole di
v1.3.10 su tutti i punti (i 2 neurali insieme a quelli dei giri precedenti), con misure finte."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_inventory_scripts import _load
from tests.unit.test_report_round4 import CELLS, _data, _ranked

script = _load("report_p3")


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_embed_rows_neural_and_time_stats(tmp_path: Path) -> None:
    rows = [
        {"status": "OK", "elapsed_s": 2.0, "extra": {"n_renamed": 4, "n_names_neural_fallback": 1,
         "n_neural_batches_cached": 0, "n_neural_batches_generated": 1, "n_neural_prompts": 6}},
        {"status": "PARTIAL", "elapsed_s": 4.0, "extra": {"n_renamed": 2,
         "n_names_neural_fallback": 0, "n_neural_batches_cached": 1,
         "n_neural_batches_generated": 0, "n_neural_prompts": 0}},
        {"status": "FAILED", "elapsed_s": 0.5, "extra": {"n_renamed": 0,
         "n_names_neural_fallback": 0, "n_neural_batches_cached": 0,
         "n_neural_batches_generated": 0, "n_neural_prompts": 0}},
    ]  # fmt: skip
    _write(tmp_path, rows)
    got = script.embed_rows(tmp_path)
    assert [r["status"] for r in got] == ["OK", "PARTIAL", "FAILED"]
    assert [r["elapsed_s"] for r in got] == [2.0, 4.0, 0.5]
    n = script.neural_stats(got)
    assert (n["n"], n["total"], n["samples"], n["renamed"]) == (3, 1, 1, 6)
    assert n["share"] == pytest.approx(1 / 6)
    t = script.time_stats(got)
    assert t["n"] == 3 and t["mean"] == pytest.approx(6.5 / 3) and t["median"] == 2.0
    assert t["p90"] == pytest.approx(3.6) and t["total"] == pytest.approx(6.5)
    assert (t["neural"], t["cached"], t["generated"], t["n_gen"]) == (3, 1, 1, 1)
    assert t["mean_gen"] == 2.0
    # proponente statico: nessun campo neurale
    static = [{"status": "OK", "elapsed_s": 0.1, "n_renamed": 3}]
    assert math.isnan(script.neural_stats(static)["total"])
    ts = script.time_stats(static)
    assert ts["neural"] == 0 and math.isnan(ts["generated"]) and math.isnan(ts["mean_gen"])
    assert script.embed_rows(tmp_path / "missing") == []


def _p3(items: list[Any]) -> Any:
    rep = object.__new__(script.P3)
    rep.configs = [d.config for d in items]
    rep.data = {d.config.name: d for d in items}
    rep.cells = list(CELLS)
    rep.neural = {d.config.name: {} for d in items}
    rep.times = {d.config.name: {} for d in items}
    return rep


def test_rules_rank_the_neural_points_with_the_others() -> None:
    """Le regole di v1.3.10 sono quelle del giro 4, su tutti i punti di multimark passati."""
    items = [
        _data("mcss_new", "mcss", 0.95, 0.80),
        _data("multimark", "multimark", 0.60, 0.55),
        _data("r4_B", "multimark", 0.34, 0.60),
        _data("r4_D", "multimark", 0.51, 0.78),
        _data("p3_B", "multimark", 0.40, 0.62),  # prudente: vince la regola 1
        _data("p3_D", "multimark", 0.55, 0.70),  # sotto mcss: vince la regola 2
    ]
    rep = _p3(items)
    lines = rep.ranking_section(items[1].config, items[0].config)
    assert _ranked(lines, "## rule 1")[0] == "multimark"  # mean_S 0,60 e logreg della base
    rule2 = _ranked(lines, "## rule 2")
    assert rule2[:2] == ["multimark", "p3_D"] and "mcss_new" not in rule2
    items[1].mean_s = 0.30
    lines = rep.ranking_section(items[1].config, items[0].config)
    assert _ranked(lines, "## rule 1")[0] == "p3_B" and _ranked(lines, "## rule 2")[0] == "p3_D"


def test_neural_and_time_sections() -> None:
    items = [_data("r4_B", "multimark", 0.34, 0.60), _data("p3_B", "multimark", 0.40, 0.62),
             _data("p3_D", "multimark", 0.55, 0.70)]  # fmt: skip
    rep = _p3(items)
    neural = [{"status": "OK", "elapsed_s": 3.0, "n_renamed": 5, "n_names_neural_fallback": 2,
               "n_neural_batches_cached": 0, "n_neural_batches_generated": 1}]  # fmt: skip
    cached = [{"status": "OK", "elapsed_s": 0.2, "n_renamed": 5, "n_names_neural_fallback": 2,
               "n_neural_batches_cached": 1, "n_neural_batches_generated": 0}]  # fmt: skip
    static = [{"status": "OK", "elapsed_s": 0.1, "n_renamed": 4}]
    sb, nb, nd = (d.config.name for d in items)
    for cell in CELLS:
        for name, rows in ((sb, static), (nb, neural), (nd, cached)):
            rep.neural[name][cell] = script.neural_stats(rows)
            rep.times[name][cell] = script.time_stats(rows)
    rep.times[nd]["deepseek L2"] = {}
    text = "\n".join(rep.neural_section())
    assert "p3_B" in text and "p3_D" in text and "40.00%" in text
    assert "r4_B " not in text.split("\n", 2)[2]  # statico: campo assente, non elencato
    times = rep.time_section()
    row_b = next(ln for ln in times if ln.strip().startswith("p3_B") and "qwen L1" in ln)
    assert row_b.split()[4:] == ["1", "3.000", "3.000", "3.000", "3.0", "0", "1", "1", "3.000"]
    row_d = next(ln for ln in times if ln.strip().startswith("p3_D") and "qwen L1" in ln)
    assert row_d.split()[-4:] == ["1", "0", "0", "-"]
    row_s = next(ln for ln in times if ln.strip().startswith("r4_B") and "qwen L1" in ln)
    assert row_s.split()[-4:] == ["-", "-", "-", "-"]
    assert any("p3_D" in ln and "deepseek L2" in ln and "no embedding results" in ln
               for ln in times)  # fmt: skip


def test_main_checks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["--config", "mcss:aaaaaaaaaaaa:mcss_new_3c47a1f", "--config",
            "multimark:bbbbbbbbbbbb:multimark"]  # fmt: skip
    assert script.main([*base, "--split", "test"]) == 1
    existing = tmp_path / "log.txt"
    existing.write_text("x", encoding="utf-8")
    assert script.main([*base, "--report", str(existing)]) == 1
    assert script.main([*base, "--mcss", "nope"]) == 1
    assert "--mcss nope" in capsys.readouterr().err
    assert script.main([*base, "--multimark", str(tmp_path)]) == 1
    assert "rename_check.py not found" in capsys.readouterr().err


def test_default_commit_is_the_p3_library() -> None:
    from tests.unit.test_posthoc_config_hashes import P3

    assert P3.startswith(script.P3_COMMIT) and len(script.P3_COMMIT) == 7
    assert issubclass(script.P3, script.r4.Round4)
