"""``scripts/report_p4.py`` e ``scripts/check_embed_identity.py`` (giro P4 su dev): identità
dell'inserimento, ablazioni affiancate a B, T2.5 per punto e per metodo, lettura a 8 e 16 bit
dalla curva in K, impercettibilità letta dalle celle con lo stesso inserimento; misure finte."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load
from tests.unit.test_report_round4 import CELLS, FakeStore, _data

script = _load("report_p4")
cei = _load("check_embed_identity")
Config = script.Config


# ---------------------------------------------------------------------- identità
def _marked(codes: dict[str, str], status: str = "OK") -> pd.DataFrame:
    return pd.DataFrame({"parent_id": list(codes), "code": list(codes.values()),
                         "embed_status": [status] * len(codes),
                         "expected_message": ["0" * 24] * len(codes)})  # fmt: skip


def test_compare_identical_and_different() -> None:
    a = _marked({"p1": "x = 1\n", "p2": "y = 2\n"})
    same = cei.compare(a, a.iloc[::-1].reset_index(drop=True))  # ordine diverso: si appaia
    assert same["identical"] and same["sha_new"] == same["sha_old"] and same["common"] == 2
    diff = cei.compare(a, _marked({"p1": "x = 1\n", "p2": "y = 3\n"}))
    assert not diff["identical"] and diff["same_code"] == 1 and diff["first_diff"] == "p2"
    missing = cei.compare(a, _marked({"p1": "x = 1\n"}))
    assert not missing["identical"] and missing["only_new"] == 1
    status = cei.compare(a, _marked({"p1": "x = 1\n", "p2": "y = 2\n"}, "PARTIAL"))
    assert not status["identical"] and status["same_embed_status"] == 0


def test_identity_run_with_store() -> None:
    from bench.pipeline.stages.watermark import watermarked_ref

    pair = cei.Pair.parse("multimark:e52b040770df:a8a296957377:B")
    table = _marked({"p1": "x = 1\n"})
    data = {}
    for h in (pair.new, pair.old):
        for level in ("L1", "L2"):
            data[str(watermarked_ref("multimark", "qwen", h, level, "python", "dev").path)] = table
    lines, ok = cei.run(FakeStore(data), [pair], ["qwen"], ["L1", "L2"], "python", "dev")
    assert ok and "B qwen L1: IDENTICAL" in "\n".join(lines)
    assert lines[-1].startswith("## result: B identical -> embedding identical")
    lines, ok = cei.run(FakeStore(data), [pair], ["qwen", "deepseek"], ["L1"], "python", "dev")
    assert not ok and any("deepseek L1: MISSING" in ln for ln in lines)
    with pytest.raises(Exception, match="expected method:new:old"):
        cei.Pair.parse("multimark:abc")


def test_identity_main(tmp_path: Path) -> None:
    existing = tmp_path / "log.txt"
    existing.write_text("x", encoding="utf-8")
    assert cei.main(["--pair", "mcss:a:b", "--report", str(existing)]) == 1


# ---------------------------------------------------------------------- misure per cella
def test_attack_detail_stats() -> None:
    t = pd.DataFrame({"attack_status": ["APPLIED", "APPLIED", "TOOL_ERROR"],
                      "attack_detail": [json.dumps({"added_loc": 4}), json.dumps({"added_loc": 8}),
                                        json.dumps({"error": "x"})]})  # fmt: skip
    s = script.attack_detail_stats(t)
    assert s["n"] == 3 and s["applied"] == pytest.approx(2 / 3) and s["added_loc"] == 6
    empty = script.attack_detail_stats(t.iloc[:0])
    assert empty["n"] == 0 and math.isnan(empty["applied"])


def test_bits_stats_are_points_of_the_curve() -> None:
    cap = script.rad._capacity()
    # p = 1e-6: rilevato fino a K = 2^8 e 2^16 (p·K ≈ 0,066 > 0,01 per 2^16), non a 2^24
    t = pd.DataFrame({"score": [6.0, 6.0, 1.0, None], "status": ["OK"] * 3 + ["FAILED"],
                      "embed_status": ["OK"] * 4, "n_sites": [40, 40, 10, 0]})  # fmt: skip
    stats = script.bits_stats(t, cap, cap.SITE_BANDS)
    row = stats["all"]
    assert row["n"] == 4 and row["K1"] == 0.5
    assert row[f"K{2**8}"] == 0.5 and row[f"K{2**16}"] == 0.0 and row[f"K{2**24}"] == 0.0
    r3 = script.r3
    for k in (1, 2**8, 2**16, 2**24):
        expected = sum(r3.sidak(s, k) >= 2 for s in (6.0, 6.0, 1.0)) / 4
        assert row[f"K{k}"] == expected
    assert [b for b, _ in script.BITS_K] == [24, 8, 16, 24]


# ---------------------------------------------------------------------- sezioni
COND = ["no attack", "T1.4 ratio=1.0 name_kind=natural", "T1.6 caps=convert",
        "T2.5 n=2 position=around"]  # fmt: skip


def _p4(items: list[Any], tpr: dict[str, dict[str, float]]) -> Any:
    rep = object.__new__(script.P4)
    rep.configs = [d.config for d in items]
    rep.data = {d.config.name: d for d in items}
    rep.cells = list(CELLS)
    rep.same = {}
    rep.unix = {d.config.name: {c: (0.6, 0.55, 0.65) for c in CELLS} for d in items}
    rep.clean, rep.extra, rep.t25 = {}, {}, {}
    for d in items:
        name, label = d.config.name, d.config.label
        rep.clean[name] = {c: {"tpr_cal": tpr[label]["no attack"], "tpr_2": 0.9} for c in CELLS}
        rep.extra[name] = {cond: {c: {"tpr_cal": tpr[label][cond], "tpr_2": 0.5} for c in CELLS}
                           for cond in COND[1:]}  # fmt: skip
        rep.t25[name] = {COND[3]: {c: {"n": 10, "applied": 1.0, "added_loc": 12.0}
                                   for c in CELLS}}  # fmt: skip

    class _R3:
        conditions = COND[:2]

    class _Point:
        def __init__(self, label: str, attack_id: str) -> None:
            self.label, self.attack_id = label, attack_id

    rep.r3 = _R3()
    rep.extra_points = [_Point(COND[2], "T1.6"), _Point(COND[3], "T2.5")]
    return rep


def test_ablation_section_next_to_b() -> None:
    items = [_data("B", "multimark", 0.34, 0.70), _data("B_V0", "multimark", 0.30, 0.62),
             _data("B_mask_off", "multimark", 0.35, 0.71)]  # fmt: skip
    tpr = {"B": dict(zip(COND, (0.9, 0.5, 0.6, 0.7), strict=True)),
           "B_V0": dict(zip(COND, (0.8, 0.6, 0.6, 0.5), strict=True)),
           "B_mask_off": dict(zip(COND, (0.9, 0.5, 0.6, 0.7), strict=True))}  # fmt: skip
    rep = _p4(items, tpr)
    assert rep.conditions == COND
    assert rep.cond_mean(items[1].config.name, COND[3]) == pytest.approx(0.5)
    lines = rep.ablation_section(items[0].config, [d.config for d in items[1:]])
    text = "\n".join(lines)
    row = next(ln for ln in lines if ln.strip().startswith("T2.5 n=2"))
    assert "0.700" in row and "0.500 (-0.200)" in row and "0.700 (+0.000)" in row
    assert "none" in text and "per cell" in text and "unixcoder AUROC (mean)" in text
    logreg = next(ln for ln in lines if "logreg AUROC max Δ from B" in ln)
    assert "-0.080" in logreg and "+0.010" in logreg
    assert rep.ablation_section(items[0].config, []) == []


def test_t25_section_per_point_and_method() -> None:
    items = [_data("mcss", "mcss", 0.9, 0.9), _data("B", "multimark", 0.34, 0.70)]
    tpr = {"mcss": dict(zip(COND, (0.95, 0.1, 0.2, 0.9), strict=True)),
           "B": dict(zip(COND, (0.9, 0.5, 0.6, 0.7), strict=True))}  # fmt: skip
    rep = _p4(items, tpr)
    lines = rep.t25_section([items[1].config, items[0].config])
    rows = [ln for ln in lines if "mean of the cells" in ln]
    assert rows[0].split()[0] == "mcss" and rows[1].split()[0] == "multimark"  # mcss prima
    assert "0.900" in rows[0] and "-0.050" in rows[0] and "-0.200" in rows[1]
    assert any("12.000" in ln for ln in lines)  # righe aggiunte in media


def test_borrowed_and_main_checks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rep = object.__new__(script.P4)
    rep.same = {"B e52b040770df": "r4_B a8a296957377"}
    assert "identity verified" in rep.borrowed_lines(True)[0]
    assert "NOT VERIFIED" in rep.borrowed_lines(False)[0]
    base = ["--config", "mcss:aaaaaaaaaaaa:mcss_new_3c47a1f", "--config",
            "multimark:bbbbbbbbbbbb:multimark", "--config", "multimark:cccccccccccc:B"]  # fmt: skip
    assert script.main([*base, "--split", "test"]) == 1
    existing = tmp_path / "log.txt"
    existing.write_text("x", encoding="utf-8")
    assert script.main([*base, "--report", str(existing)]) == 1
    assert script.main([*base, "--ablation", "nope"]) == 1
    assert "--ablation ['nope']" in capsys.readouterr().err
    assert script.main([*base, "--same", "B"]) == 1
    assert "expected NEW=OLD" in capsys.readouterr().err
    assert script.main([*base, "--multimark", str(tmp_path)]) == 1
    assert "rename_check.py not found" in capsys.readouterr().err


def test_resolve_labels_default_t25() -> None:
    args = script.parse_args(["--config", "mcss:aaaaaaaaaaaa:mcss_new_3c47a1f", "--config",
                              "multimark:bbbbbbbbbbbb:multimark", "--config",
                              "multimark:cccccccccccc:B", "--config", "mcss:dddddddddddd:mcss",
                              "--config", "multimark:eeeeeeeeeeee:B_V0", "--ablation", "B_V0",
                              "--pair", "mcss:dddddddddddd:aaaaaaaaaaaa:mcss",
                              "--same", "mcss=mcss_new_3c47a1f"])  # fmt: skip
    sel = script.resolve_labels(args)
    assert [c.label for c in sel["t25"]] == ["mcss", "B", "B_V0"]
    assert sel["same"] == {"mcss dddddddddddd": "mcss_new_3c47a1f aaaaaaaaaaaa"}


def test_one_bit_ablations_also_next_to_the_base() -> None:
    """V0 e V1 girano a 1 bit per nome: oltre che a B si affiancano alla base multimark (V2, 1 bit
    per nome), con le differenze dalla base."""
    items = [_data("multimark", "multimark", 0.40, 0.66), _data("B", "multimark", 0.34, 0.70),
             _data("B_V0", "multimark", 0.30, 0.62), _data("B_V1", "multimark", 0.20, 0.80),
             _data("B_mask_off", "multimark", 0.35, 0.71)]  # fmt: skip
    tpr = {"multimark": dict(zip(COND, (0.9, 0.6, 0.6, 0.65), strict=True)),
           "B": dict(zip(COND, (0.9, 0.5, 0.6, 0.7), strict=True)),
           "B_V0": dict(zip(COND, (0.8, 0.6, 0.6, 0.5), strict=True)),
           "B_V1": dict(zip(COND, (0.9, 0.2, 0.1, 0.6), strict=True)),
           "B_mask_off": dict(zip(COND, (0.9, 0.5, 0.6, 0.7), strict=True))}  # fmt: skip
    rep = _p4(items, tpr)
    base, b, v0, v1, mask = (d.config for d in items)
    lines = rep.ablation_section(b, [v0, v1, mask], base, [v0, v1])
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    assert len(heads) == 2 and "next to the base multimark" in lines[heads[1]]
    assert "1 bit per name" in lines[heads[1]] and "B_V0, B_V1" in lines[heads[1]]
    second = lines[heads[1] :]
    assert "B_mask_off" not in second[1] and second[1].split()[1:] == ["multimark", "B_V0", "B_V1"]
    row = next(ln for ln in second if ln.strip().startswith("T2.5 n=2"))
    assert "0.650" in row and "0.500 (-0.150)" in row and "0.600 (-0.050)" in row
    logreg = next(ln for ln in second if "logreg AUROC max Δ from multimark" in ln)
    assert "-0.040" in logreg and "+0.140" in logreg
    first = lines[: heads[1]]
    assert any("logreg AUROC max Δ from B" in ln for ln in first)
    assert rep.ablation_section(b, [v0], None, [v0]) == rep.ablation_section(b, [v0])
