"""Tabella 5 di MCSS in ``scripts/mcss_paper_attacks.py`` (PIPELINE di multimark §4.4 punto 3):
rinomina di r variabili con le due letture (raccolta di T1.4 e parola minuscola), inversione dei
siti di un canale, modalità ``table5`` con gli ordinamenti attesi e la lettura più vicina al
paper."""

from __future__ import annotations

import ast
import json
import math
import random
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.unit.test_inventory_scripts import LIBRARY, needs_library
from tests.unit.test_mcss_paper_attacks import _selection, _synthetic

pytestmark = needs_library

POOL = ["alpha_value", "betaCount", "Gamma", "delta", "EPS_MAX", "item_list", "totalSum",
        "rowIdx", "colName", "keyMap", "tmp_buf", "nodeRef", "pathStr", "resultSet", "flagOk",
        "dataArr", "indexPos", "scoreVal", "line_no", "wordCount"]  # fmt: skip


@pytest.fixture(scope="module")
def script() -> ModuleType:
    from tests.unit.test_inventory_scripts import _load

    mod = _load("mcss_paper_attacks")
    mod.import_library(LIBRARY)
    return mod


def _marked(script: ModuleType, seed: int = 3, method: str = "mcss") -> tuple[str, Any, int]:
    from multimark.embed import embed  # type: ignore[import-not-found]

    cfg = script.profiles()[method]
    message, key = script.case_seeds(script.GLOBAL_SEED, f"s{seed}", 0)
    emb = embed(_synthetic(seed, 40), message, key, cfg)
    assert emb.status in ("OK", "PARTIAL")
    return str(emb.code), cfg, key


def _name_sites(code: str, cfg: Any, key: int) -> list[str]:
    from multimark._astindex import AstIndex  # type: ignore[import-not-found]
    from multimark.layout import build_layout  # type: ignore[import-not-found]

    layout = build_layout(AstIndex.from_code(code), cfg, key)
    return sorted({s.name.name for s in layout.slots if s.name is not None})


def test_attack_table(script: ModuleType) -> None:
    mod = script
    assert mod.TABLE5[0] == "none" and len(mod.TABLE5) == 1 + 4 + 4 + 8
    assert set(mod.TABLE5) == set(mod.ATTACKS)
    assert mod.MCSS_CHANNELS == (
        "aug_assign", "compare_dir", "equality_dir", "list_comp", "ternary", "operand_order",
        "empty_coll", "none_check",
    )  # fmt: skip


@pytest.mark.parametrize("suffix", ["", "_lower"])
@pytest.mark.parametrize("r", [2, 4])
def test_rename_r_variables(script: ModuleType, r: int, suffix: str) -> None:
    code, cfg, key = _marked(script)
    before = _name_sites(code, cfg, key)
    assert len(before) > r
    out = script.ATTACKS[f"rename{r}{suffix}"](code, cfg, key, 11, POOL)
    ast.parse(out)
    words_before = set(script._taken(code))
    new = sorted(set(script._taken(out)) - words_before)
    assert len(new) == r  # r nomi nuovi, tutti diversi
    if suffix:
        assert all(n.isalpha() and n.islower() and 5 <= len(n) <= 8 for n in new)
    else:
        assert set(new) <= set(POOL)
    gone = [n for n in before if n not in set(script._taken(out))]
    assert len(gone) == r  # le variabili scelte non compaiono più (rinomina coerente)
    assert out == script.ATTACKS[f"rename{r}{suffix}"](code, cfg, key, 11, POOL)  # determinismo
    assert out != script.ATTACKS[f"rename{r}{suffix}"](code, cfg, key, 12, POOL) or r >= len(before)


def test_rename_more_than_available_renames_all(script: ModuleType) -> None:
    code, cfg, key = _marked(script)
    before = _name_sites(code, cfg, key)
    out = script.rename_random(code, cfg, key, 5, POOL, 10_000, script._lower_name)
    assert not set(before) & set(script._taken(out))
    with pytest.raises(script.AttackError, match="no free name"):
        script._pool_name(random.Random(1), set(POOL), POOL)


def test_invert_channel(script: ModuleType) -> None:
    from multimark._astindex import AstIndex  # type: ignore[import-not-found]
    from multimark.layout import read_options  # type: ignore[import-not-found]
    from multimark.sites import find_sites  # type: ignore[import-not-found]

    code, cfg, key = _marked(script)
    sites = find_sites(AstIndex.from_code(code), read_options(cfg))
    channel = max(script.MCSS_CHANNELS, key=lambda c: sum(s.channel == c for s in sites))
    values = sorted(s.value for s in sites if s.channel == channel)
    assert values
    out = script.ATTACKS[f"invert_{channel}"](code, cfg, key, 0, ())
    after = find_sites(AstIndex.from_code(out), read_options(cfg))
    assert sorted(1 - s.value for s in after if s.channel == channel) == values
    others = sorted((s.channel, s.value) for s in sites if s.channel != channel)
    assert sorted((s.channel, s.value) for s in after if s.channel != channel) == others


def _run_table5(script: ModuleType, tmp_path: Path, *extra: str) -> tuple[int, Path]:
    sel = _selection(tmp_path / "sel.jsonl", [_synthetic(i, 40, 1 + i % 2) for i in range(3)])
    pool = tmp_path / "python.json"
    pool.write_text(json.dumps({"names": POOL, "comments": []}), encoding="utf-8")
    report = tmp_path / "P4_mcss_table5.txt"
    argv = ["--attack", "table5", "--selection", str(sel), "--report", str(report),
            "--library", str(LIBRARY), *extra]  # fmt: skip
    if "--no-pool" not in extra:
        argv += ["--names-pool", str(pool)]
    else:
        argv.remove("--no-pool")
    return script.main(argv), report


def test_table5_report(script: ModuleType, tmp_path: Path) -> None:
    code, report = _run_table5(script, tmp_path, "--methods", "mcss")
    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "names pool: " in text and "(20 names, sha256 " in text
    for attack in script.TABLE5:
        assert f"\n  {attack} " in text
    assert "rename r, the two readings side by side (mcss):" in text
    assert "## expected orderings of Table 5 on mcss" in text
    assert "operand_order is the worst" in text and "none_check is the best" in text
    assert text.count("rename 8 < 0.10: ") == 2 and "-> " in text
    assert "closer to the pattern of the paper" in text or "equally close" in text
    # i log non si sovrascrivono
    assert _run_table5(script, tmp_path, "--methods", "mcss")[0] == 1


def test_table5_needs_the_name_pool(script: ModuleType, tmp_path: Path) -> None:
    code, report = _run_table5(script, tmp_path, "--methods", "mcss", "--no-pool")
    assert code == 1 and not report.exists()


def test_rename_checks_and_verdict(script: ModuleType) -> None:
    rates = {"none": 1.0, "rename2": 0.97, "rename4": 0.96, "rename8": 0.05, "rename16": 0.0,
             "rename2_lower": 1.0, "rename4_lower": 0.90, "rename8_lower": 0.2,
             "rename16_lower": 0.1}  # fmt: skip
    pool = script.rename_checks(lambda a: rates[a], "")
    assert [ok for _, ok, _ in pool] == [True] * 5
    low = script.rename_checks(lambda a: rates[a], "_lower")
    assert [ok for _, ok, _ in low] == [True, False, False, False, True]
    assert low[1][2] == pytest.approx(0.05) and low[2][2] == pytest.approx(0.1)
    assert not script.rename_checks(lambda a: math.nan, "")[0][1]
