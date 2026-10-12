"""Script della M8: stima dei costi dell'HPO e verifiche sul punteggio di STONE (calcoli)."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from tests.conftest import REPO_ROOT


def _load(name: str) -> ModuleType:
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"wmb_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # le dataclass cercano il modulo per nome
    spec.loader.exec_module(module)
    return module


cost = _load("estimate_hpo_cost")
stone = _load("diag_stone_score")


# ---------------------------------------------------------------------- griglie
def test_grid_sizes_and_required_values() -> None:
    grids = cost.grids()
    sizes = {name: len(configs) for name, (_, configs) in grids.items()}
    assert sizes == {
        "sweet_full_61": 61,
        "sweet_reduced_25": 25,
        "stone_10": 10,
        "promptmark_28": 28,
        "acw_4": 4,
        "mcgmark_1": 1,
    }
    for name, (method, configs) in grids.items():
        assert cost.DEFAULTS[method] in configs, name
        assert len({json.dumps(c, sort_keys=True) for c in configs}) == len(configs), name
    # Valori richiesti: paper di SWEET (γ = 0,25, δ = 3) e δ del repository di STONE.
    for name in ("sweet_full_61", "sweet_reduced_25"):
        assert any(c["gamma"] == 0.25 and c["delta"] == 3.0 for c in grids[name][1])
    assert {c["delta"] for c in grids["stone_10"][1]} >= {0.5, 1.0}


# ---------------------------------------------------------------------- modelli di costo
def test_cost_factors() -> None:
    assert cost.acw_factor(43) == pytest.approx(1.0)
    assert cost.acw_factor(10) < cost.acw_factor(20) < 1.0
    iterations = [5, 5, 1, 3]
    assert cost.gen_factor("promptmark", {"iter_cap": 5}, iterations) == pytest.approx(1.0)
    assert cost.gen_factor("promptmark", {"iter_cap": 1}, iterations) == pytest.approx(4 / 14)
    assert cost.gen_factor("sweet", {"gamma": 0.1}, []) == 1.0


def test_pack_jobs_splits_long_cells_and_fills_budget() -> None:
    jobs = cost.pack_jobs([3000.0, 3000.0, 3000.0], budget=7000.0, startup=100.0)
    assert len(jobs) == 2 and all(j <= 7000.0 for j in jobs)
    long = cost.pack_jobs([20000.0], budget=7000.0, startup=100.0)
    assert len(long) == 3 and sum(long) == pytest.approx(20000.0 + 3 * 100.0)


def test_startup_seconds(tmp_path: Path) -> None:
    log = tmp_path / "worker.log"
    log.write_text(
        "\n===== 2026-10-10 17:13:10 python -m bench_shims.acw\n"
        "2026-10-10 17:13:24,180 INFO bench_shims: acw detect: 576 items, 0 already done\n",
        encoding="utf-8",
    )
    assert cost.startup_seconds(log) == [14.0]


def test_grid_cost_skips_default_on_l1_and_shares_negatives() -> None:
    unit = cost.Unit(
        gen_s=10.0,
        n_pos=10,
        det_s={"positives": 1.0, "neg_human": 1.0, "neg_llm": 1.0},
        n_det={"positives": 10, "neg_human": 100, "neg_llm": 10},
        exec_s=2.0,
    )
    units = {("sweet", "m", "python"): unit}
    configs = [
        cost.DEFAULTS["sweet"],
        {"gamma": 0.5, "delta": 2.0, "entropy_threshold": 0.5},  # stessa rilevazione del default
        {"gamma": 0.25, "delta": 2.0, "entropy_threshold": 0.5},
    ]
    plain = cost.grid_cost("sweet", configs, units, 60.0, "L1", share_negatives=False)
    shared = cost.grid_cost("sweet", configs, units, 60.0, "L1", share_negatives=True)
    assert plain.gpu_gen_h == pytest.approx(2 * 10 * 10.0 / 3600)
    assert plain.gpu_det_h == pytest.approx(2 * 120 * 1.0 / 3600)
    # Con la condivisione la prima configurazione riusa i negativi del default.
    assert shared.gpu_det_h == pytest.approx((10 + 120) / 3600)
    assert plain.cpu_exec_h == pytest.approx(2 * 10 * 2.0 / 3600)
    l2 = cost.grid_cost("sweet", configs, units, 60.0, "L2", share_negatives=False)
    assert l2.gpu_gen_h == pytest.approx(3 * cost.L2_COUNTS["positives"] * 10.0 / 3600)


# ---------------------------------------------------------------------- STONE
def test_null_and_residual_sum_to_the_official_z() -> None:
    gamma, n_syn, n_ns, green = 0.5, 40.0, 100.0, 60.0
    official = (green - gamma * n_syn) / math.sqrt(gamma * (1 - gamma) * n_syn)
    null = stone.null_z(n_syn, n_ns, gamma)
    residual = stone.residual_z(green, n_syn, n_ns, gamma)
    assert null + residual == pytest.approx(official)
    # Codice senza watermark (verdi = γ·N_ns): z ufficiale positivo se N_ns > N_syn.
    assert stone.residual_z(gamma * n_ns, n_syn, n_ns, gamma) == pytest.approx(0.0)
    assert null > 0
    assert math.isnan(stone.null_z(0.0, 10.0, gamma))


def test_expand_and_auroc() -> None:
    rows = []
    for i in range(10):
        extra = {"n_syntax": 20, "n_nonsyntax": 30, "n_green_nonsyntax": 15 + i, "z_nonsyntax": i}
        rows.append({"status": "OK", "score": float(i), "extra_json": json.dumps(extra)})
    rows.append({"status": "FAILED", "score": None, "extra_json": "{}"})
    df = stone.expand(pd.DataFrame(rows), 0.5)
    assert len(df) == 10
    assert df["green_frac"].iloc[0] == pytest.approx(0.5)
    assert df["z_null"].nunique() == 1
    high, low = df["z_official"].iloc[5:], df["z_official"].iloc[:5]
    assert stone.auroc(high, low) == pytest.approx(1.0)
    assert math.isnan(stone.auroc(high.iloc[:2], low))


# ------------------------------------------------- PromptMark, griglia ricostruita
replay_mod = _load("verify_promptmark_replay")


def _it(correct: bool, p: float, text: str) -> dict[str, object]:
    return {"correctness": correct, "p_exact": p, "response": text}


def test_replay_stops_like_run_phase1() -> None:
    its = [
        _it(False, 0.5, "r0"),
        _it(True, 0.04, "r1"),
        _it(True, 0.01, "r2"),
        _it(True, 0.3, "r3"),
    ]
    # η = 2,12 (p < 0,017): stop alla terza iterazione.
    assert replay_mod.replay(its, 5, 2.12) == (2, 3)
    # η = 1,64 (p < 0,05): stop prima, alla seconda.
    assert replay_mod.replay(its, 5, 1.64) == (1, 2)
    # Massimo 2 iterazioni con η = 2,12: nessuno stop, best = la prima corretta.
    assert replay_mod.replay(its, 2, 2.12) == (1, 2)
    # Mai corretta: best = la prima iterazione.
    wrong = [_it(False, 0.001, "a"), _it(False, 0.001, "b")]
    assert replay_mod.replay(wrong, 5, 2.12) == (0, 2)
    # Soglia irraggiungibile: nessuno stop.
    assert replay_mod.replay(its, 5, replay_mod.UNREACHABLE_Z) == (1, 4)


def test_replay_compare_detects_differences() -> None:
    its = [_it(False, 0.5, "r0"), _it(True, 0.01, "r1"), _it(True, 0.3, "r2")]
    trajectory = {"iterations": its}
    actual = {
        "n_iterations": 2,
        "selected_iteration": 1,
        "raw_output": "r1",
        "iterations": its[:2],
    }
    assert replay_mod.compare(trajectory, actual, 5, 2.12) == []
    changed = {**actual, "raw_output": "r1 "}
    assert replay_mod.compare(trajectory, changed, 5, 2.12) == ["raw_output differs"]
