"""``scripts/report_round4.py``: le due regole di v1.3.10 (prudente e ad alta capacità), il ripiego
di ``names.suffix_by_type`` e i siti medi, con misure finte (nessuno store)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

script = _load("report_round4")
Config = script.Config
CELLS = ["qwen L1", "qwen L2", "deepseek L1", "deepseek L2"]


def test_embed_extras_fallback_and_sites(tmp_path: Path) -> None:
    rows = [
        {"status": "OK", "extra": {"n_sites": 30, "n_structural_sites": 20, "n_name_sites": 10,
                                   "n_renamed": 4, "n_names_type_fallback": 1}},
        {"status": "PARTIAL", "extra": {"n_sites": 10, "n_structural_sites": 8, "n_name_sites": 2,
                                        "n_renamed": 2, "n_names_type_fallback": 0}},
        {"status": "FAILED", "extra": {"n_sites": 0, "n_structural_sites": 0, "n_name_sites": 0,
                                       "n_renamed": 0, "n_names_type_fallback": 0}},
    ]  # fmt: skip
    (tmp_path / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    extras = script.embed_extras(tmp_path)
    assert [e["status"] for e in extras] == ["OK", "PARTIAL", "FAILED"]
    f = script.fallback_stats(extras)
    assert (f["n"], f["total"], f["samples"], f["renamed"]) == (3, 1, 1, 6)
    assert f["share"] == pytest.approx(1 / 6)
    s = script.site_stats(extras)
    assert s["n_sites"] == pytest.approx(40 / 3) and s["n_name_sites"] == 4
    # opzione spenta: campo assente, nessun conteggio
    off = script.fallback_stats([{"status": "OK", "n_renamed": 3}])
    assert off["with_field"] == 0 and math.isnan(off["total"]) and math.isnan(off["share"])
    assert script.embed_extras(tmp_path / "missing") == []


class FakeStore:
    def __init__(self, data: dict[str, pd.DataFrame]) -> None:
        self.data = data

    def exists(self, ref: Any) -> bool:
        return str(ref.path) in self.data

    def read_table(self, ref: Any) -> pd.DataFrame:
        return self.data[str(ref.path)]


def test_logreg_cell() -> None:
    from bench.pipeline.stages.imperceptibility import imperceptibility_ref

    c = Config("multimark", "abc123abc123", "r4_B")
    ref = imperceptibility_ref("multimark", "qwen", "abc123abc123", "L1", "python", "dev")
    table = pd.DataFrame({"name": ["codebleu", script.LOGREG], "value": [0.5, 0.62],
                          "ci_low": [None, 0.58], "ci_high": [None, 0.66]})  # fmt: skip
    store = FakeStore({str(ref.path): table})
    assert script.logreg_cell(store, c, "qwen", "L1", "python", "dev") == (0.62, 0.58, 0.66)
    assert all(math.isnan(x) for x in script.logreg_cell(store, c, "qwen", "L2", "python", "dev"))


def _data(label: str, method: str, mean: float, logreg: float, rename: float = 0.0,
          dp: float = 1.0, fpr: float = 0.005, renames: float = 2.0) -> Any:  # fmt: skip
    d = script.ConfigData(Config(method, f"{abs(hash(label)) % 10**12:012d}", label))
    d.mean_s, d.dp_max, d.renames = mean, dp, renames
    d.fprs = {"L1": fpr, "L2": fpr}
    d.logreg = {cell: (logreg, logreg - 0.02, logreg + 0.02) for cell in CELLS}
    d.rename_rate = dict.fromkeys(CELLS, rename)
    d.fallback = {cell: script.fallback_stats([]) for cell in CELLS}
    d.sites = {cell: script.site_stats([]) for cell in CELLS}
    return d


def _round4(items: list[Any]) -> Any:
    r4 = object.__new__(script.Round4)
    r4.configs = [d.config for d in items]
    r4.data = {d.config.name: d for d in items}
    r4.cells = list(CELLS)
    return r4


def _ranked(lines: list[str], title: str) -> list[str]:
    start = next(i for i, ln in enumerate(lines) if ln.startswith(title))
    out = []
    for ln in lines[start + 1 :]:
        if not ln.startswith("    "):
            break
        out.append(ln.split()[1])
    return out


def test_rules_of_v1_3_10() -> None:
    items = [
        _data("mcss_new", "mcss", 0.95, 0.80),
        _data("multimark", "multimark", 0.60, 0.55),  # base: logreg 0.55, limite 0.65
        _data("r4_B", "multimark", 0.70, 0.60),  # prudente
        _data("r4_C", "multimark", 0.705, 0.64, renames=1.0),  # parità con r4_D: meno rinomine
        _data("r4_D", "multimark", 0.71, 0.78, renames=3.0),  # sotto mcss, sopra base + 0,10
        _data("r4_D_sfx", "multimark", 0.90, 0.79, rename=0.02),  # rinomine incoerenti > 1%
        _data("r4_C_sfx", "multimark", 0.88, 0.70, dp=3.5),  # ΔPass@1 oltre 3 pp
        _data("r4_B_sfx", "multimark", 0.85, 0.81),  # sopra mcss in ogni cella
    ]
    items[-1].logreg["qwen L1"] = (0.79, 0.77, 0.81)  # sotto mcss in una cella sola: non basta
    r4 = _round4(items)
    base, mcss = items[1].config, items[0].config
    lines = r4.ranking_section(base, mcss)
    rule1 = _ranked(lines, "## rule 1")
    rule2 = _ranked(lines, "## rule 2")
    # regola 1: r4_C (0,705) e r4_B (0,70) entro 0,01 dal migliore: vince chi rinomina meno
    assert rule1[:2] == ["r4_C", "r4_B"] and "r4_D" not in rule1 and "mcss_new" not in rule1
    # regola 2: r4_D_sfx fuori per le rinomine, r4_C_sfx per ΔPass@1, r4_B_sfx per mcss
    assert rule2[0] == "r4_C" and "r4_D" in rule2
    assert not {"r4_D_sfx", "r4_C_sfx", "r4_B_sfx", "mcss_new"} & set(rule2)
    # senza il vincolo delle rinomine la regola 1 non cambia il primo posto: nessuna riga in più
    assert not any("without the renaming constraint" in ln for ln in lines)
    assert any("one working point" in ln for ln in lines)
    table = r4.lines(base, mcss)
    assert any(ln.startswith("## constraints per configuration") for ln in table)
    row = next(ln for ln in table if ln.strip().startswith("r4_D "))
    assert "yes" in row.split() and "0.230" in row  # sotto mcss; Δbase 0,78 − 0,55
    assert "qwen L1 0.600 [0.580,0.620] (+0.050, -0.200)" in "\n".join(table)


def test_missing_measures_count_as_not_met() -> None:
    items = [_data("mcss_new", "mcss", 0.95, 0.80), _data("multimark", "multimark", 0.6, 0.55),
             _data("r4_B", "multimark", 0.7, 0.6)]  # fmt: skip
    items[2].logreg["deepseek L2"] = (math.nan, math.nan, math.nan)
    lines = _round4(items).ranking_section(items[1].config, items[0].config)
    assert _ranked(lines, "## rule 1") == ["multimark"]  # la base rispetta il proprio vincolo
    assert _ranked(lines, "## rule 2") == ["multimark"]
    assert any(ln.strip().startswith("incomplete") and "r4_B" in ln for ln in lines)


def test_extras_section_lists_only_configurations_with_the_option() -> None:
    items = [_data("multimark", "multimark", 0.6, 0.55), _data("r4_B_sfx", "multimark", 0.7, 0.6)]
    with_option = [{"status": "OK", "n_sites": 12, "n_structural_sites": 8, "n_name_sites": 4,
                    "n_renamed": 5, "n_names_type_fallback": 2}]  # fmt: skip
    items[1].fallback = {cell: script.fallback_stats(with_option) for cell in CELLS}
    items[1].sites = {cell: script.site_stats(with_option) for cell in CELLS}
    text = "\n".join(_round4(items).extras_section())
    fallback = text.split("## mean sites")[0]
    assert "r4_B_sfx" in fallback and "multimark " not in fallback.split("\n", 2)[2]
    assert "40.00%" in fallback  # 2 ripieghi su 5 nomi rinominati
    sites = text.split("## mean sites")[1]
    assert "12.000" in sites and "no embedding results" in sites  # la base senza risultati


def test_main_checks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["--config", "mcss:aaaaaaaaaaaa:mcss_new_3c47a1f", "--config",
            "multimark:bbbbbbbbbbbb:multimark"]  # fmt: skip
    assert script.main([*base, "--split", "test"]) == 1
    existing = tmp_path / "log.txt"
    existing.write_text("x", encoding="utf-8")
    assert script.main([*base, "--report", str(existing)]) == 1
    assert script.main([*base, "--base", "nope"]) == 1
    assert "--base nope" in capsys.readouterr().err
    assert script.main([*base, "--multimark", str(tmp_path)]) == 1  # nessun rename_check.py
    assert "rename_check.py not found" in capsys.readouterr().err
