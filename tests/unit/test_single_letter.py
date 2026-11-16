"""Variabili di una lettera (PIPELINE v1.2.6 §4.2 punto 9).

``inventory_sites.py --single-letter``, intestazione di ``mcss_band_detail.py`` e scelta della
lettura per ``mcss`` (``single_letter_choice.py``).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_inventory_scripts import LIBRARY, _input, _load, needs_library
from tests.unit.test_mcss_selftest import _csv, _pool, _row, _write_pool

inv = _load("inventory_sites")
detail = _load("mcss_band_detail")
choice = _load("single_letter_choice")

# Un nome di una lettera bersaglio di for (i), uno no (n), uno di due lettere (ab).
CODE = "def f(xs):\n    n = 0\n    ab = 1\n    for i in xs:\n        n = n + i + ab\n    return n\n"


# ---------------------------------------------------------------- inventory_sites.py


def test_variant_names_with_single_letter() -> None:
    base = "canonical-paper_guards-on_params-on_modlevel-on"
    assert inv.variant_name("paper", True, True, True, "exclude") == base
    assert (
        inv.variant_name("paper", True, True, True, "extend_except_loop")
        == base + "_singleletter-extend_except_loop"
    )
    assert (
        inv.variant_name("kind", True, False, False, "extend_all")
        == "canonical-kind_guards-on_params-off_singleletter-extend_all"
    )
    with pytest.raises(SystemExit):
        inv.build_parser().parse_args(
            ["--input", "x", "--out-dir", "y", "--channels", "mcss8", "--single-letter", "all"]
        )


@needs_library
def test_single_letter_is_passed_to_the_library(tmp_path: Path) -> None:
    inputs = tmp_path / "in.jsonl"
    _input(inputs, [("humanevalplus", CODE)])
    base = ["--input", str(inputs), "--out-dir", str(tmp_path), "--library", str(LIBRARY)]
    base += ["--channels", "mcss8"]
    got = {}
    for mode in inv.SINGLE_LETTER_MODES:
        summary = tmp_path / f"s.{mode}.txt"
        argv = [*base, "--single-letter", mode, "--summary", str(summary)]
        argv += ["--summary-json", str(tmp_path / f"s.{mode}.json")]
        assert inv.main(argv) == 0
        from multimark.inventory import inventory_code  # importato da LIBRARY

        name = inv.variant_name("kind", True, False, False, mode)
        with (tmp_path / f"in.{name}.csv").open(encoding="utf-8", newline="") as fh:
            row = next(csv.DictReader(fh))
        expected = inventory_code(CODE, channels="mcss8", single_letter=mode)
        assert int(row["n_vars"]) == expected["n_vars"]
        got[mode] = int(row["n_vars"])
        options = json.loads((tmp_path / f"s.{mode}.json").read_text(encoding="utf-8"))["options"]
        text = summary.read_text(encoding="utf-8")
        if mode == "exclude":  # come prima: niente chiave né testo in più
            assert "single_letter" not in options and "single_letter" not in text
        else:
            assert options["single_letter"] == mode and f"single_letter={mode}" in text
    # exclude: ab; extend_except_loop: ab e n; extend_all: ab, n e i
    assert got == {"exclude": 1, "extend_except_loop": 2, "extend_all": 3}
    assert inv.SINGLE_LETTER_MODES == choice.MODES


def test_library_modes_match() -> None:
    names = LIBRARY / "multimark" / "names.py"
    if not names.is_file():
        pytest.skip("third_party/multimark not initialised")
    text = names.read_text(encoding="utf-8")
    assert 'SINGLE_LETTER_MODES = ("exclude", "extend_except_loop", "extend_all")' in text


# ---------------------------------------------------------------- scelta


def _stats(ratio: float, n_vars: float) -> dict[str, Any]:
    return {"eligible": 10, "files": 5, "problems": 5, "n_bits_v1": 35.0, "n_vars": n_vars,
            "n_sites_structural": ratio * n_vars, "ratio": ratio}  # fmt: skip


def test_choose_closest_ratio() -> None:
    stats = {
        "exclude": _stats(1.37, 10.7),
        "extend_except_loop": _stats(1.05, 12.5),
        "extend_all": _stats(0.90, 14.0),
    }
    assert choice.choose(stats) == ("extend_all", ["extend_all"])


def test_choose_tie_within_005_uses_the_mean_of_variables() -> None:
    stats = {
        "exclude": _stats(1.37, 10.7),
        "extend_except_loop": _stats(0.96, 12.2),  # distanza 0,04
        "extend_all": _stats(0.90, 14.0),  # distanza 0,02: entro 0,05 dalla minima
    }
    assert choice.choose(stats) == ("extend_except_loop", ["extend_except_loop", "extend_all"])
    stats["extend_except_loop"] = _stats(0.98, 12.2)  # distanza 0,06: 0,04 sopra la minima
    assert choice.choose(stats)[1] == ["extend_except_loop", "extend_all"]
    stats["extend_except_loop"] = _stats(1.00, 12.2)  # distanza 0,08: 0,06 sopra la minima
    assert choice.choose(stats) == ("extend_all", ["extend_all"])


def test_choose_skips_readings_without_band_files() -> None:
    nan = float("nan")
    stats = {"exclude": _stats(nan, nan), "extend_all": _stats(1.2, 11.0)}
    assert choice.choose(stats) == ("extend_all", ["extend_all"])
    assert choice.choose({"exclude": _stats(nan, nan)}) == (None, [])
    assert "outcome: undetermined" in choice.report({"exclude": _stats(nan, nan)})


def test_choice_main_on_three_tables(tmp_path: Path) -> None:
    pool = _pool(6)
    sids = sorted(pool)
    pool[sids[0]]["split"] = "test"  # escluso
    path = _write_pool(tmp_path, pool, same_cluster=[])
    # (bit, variabili, strutturali) per lettura: la fascia cambia con la lettura
    data = {
        "exclude": [(35, 10, 15)] * 3 + [(20, 5, 10)] * 3,  # rapporto 1,5
        "extend_except_loop": [(35, 12, 11)] * 4 + [(50, 20, 10)] * 2,  # 0,92: scelta
        "extend_all": [(36, 14, 8)] * 5 + [(60, 25, 10)],  # 0,57
    }
    argv = ["--pool", str(path)]
    for mode, values in data.items():
        rows = [
            _row(s, b, vars_=v, structural=st) for s, (b, v, st) in zip(sids, values, strict=True)
        ]
        _csv(tmp_path / f"{mode}.csv", rows)
        argv += ["--table", f"{mode}={tmp_path / f'{mode}.csv'}"]
    report = tmp_path / "choice.txt"
    assert choice.main([*argv, "--report", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert text.startswith("# single-letter choice for mcss")
    assert "closest to 0.92 (MCSS Table 3); on a tie within 0.05" in text
    assert "outcome: names.single_letter = extend_except_loop for mcss (closest ratio)" in text
    band = next(x for x in text.splitlines() if x.startswith("band files"))
    assert band.split()[-3:] == ["2", "3", "4"]  # sids[0] escluso in tutte
    ratio = next(x for x in text.splitlines() if x.startswith("structural / variables"))
    assert ratio.split()[-4:] == ["1.50", "0.92", "0.57", "0.92"]
    assert choice.main([*argv, "--report", str(report)]) == 1  # il log esiste già
    with pytest.raises(SystemExit):
        choice.main(["--pool", str(path), "--table", "all=x.csv"])
    dup = ["--pool", str(path)] + ["--table", f"exclude={tmp_path / 'exclude.csv'}"] * 2
    assert choice.main(dup) == 1


def test_band_detail_header_names_the_reading(tmp_path: Path) -> None:
    pool = _pool(3)
    path = _write_pool(tmp_path, pool, same_cluster=[])
    _csv(tmp_path / "t.csv", [_row(s, 35) for s in sorted(pool)])
    argv = ["--pool", str(path), "--mcss-table", str(tmp_path / "t.csv")]
    rep = tmp_path / "a.txt"
    assert detail.main([*argv, "--single-letter", "extend_all", "--report", str(rep)]) == 0
    assert "channels mcss8), single_letter=extend_all\n" in rep.read_text(encoding="utf-8")
    rep = tmp_path / "b.txt"
    assert detail.main([*argv, "--report", str(rep)]) == 0
    assert "channels mcss8)\n" in rep.read_text(encoding="utf-8")
