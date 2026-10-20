"""HPO della M8: griglie, criterio, risoluzione degli iperparametri, ricostruzione di PromptMark
(ADR-009) e uno sweep completo su dati finti (watermark → execute → detect → calibrate → tune)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from bench_contracts import WorkerResult

import bench.hpo.grid as grid_mod
import bench.pipeline.stages.generate_baseline as gb
import bench.pipeline.stages.tune as tune_mod
import bench.pipeline.stages.watermark as wm
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import Problem, SelectedConfig
from bench.execution import sandbox as sb
from bench.hpo.criterion import Candidate, ConstrainedTprCriterion
from bench.hpo.grid import load_grid, resolve
from bench.methods.adapters.promptmark import (
    PromptMarkAdapter,
    replay_result,
    replay_selection,
)
from bench.methods.base import MethodAdapter
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import CellPlanner
from bench.pipeline.stages.calibrate import CalibrateStage, threshold_ref
from bench.pipeline.stages.detect import DetectStage
from bench.pipeline.stages.execute import ExecuteStage
from bench.pipeline.stages.tune import candidates_ref, selected_ref
from bench.pipeline.stages.watermark import WatermarkStage
from tests.conftest import rebuild
from tests.datafix import build_tiny_datasets, tiny_config
from tests.unit.test_execute import FakeSandbox
from tests.unit.test_generate_baseline import FakeBackend
from tests.unit.test_watermark import MODEL, echo_client


# ---------------------------------------------------------------------- griglie
def test_repository_grids() -> None:
    sizes = {m: len(load_grid(m).candidates()) for m in ("sweet", "stone", "acw", "mcgmark")}
    assert sizes == {"sweet": 61, "stone": 18, "acw": 4, "mcgmark": 1}
    pm = load_grid("promptmark")
    assert len(pm.candidates()) == 28 and len(pm.trajectories()) == 3
    trajectories = [p.hparams for p in pm.trajectories()]
    assert {t["green_size"] for t in trajectories} == {8, 10, 12}
    assert all(t["iter_cap"] == 5 and t["z_threshold"] == 1e6 for t in trajectories)
    replayed = [p for p in pm.candidates() if p.trajectory is not None]
    assert len(replayed) == 27
    assert all(p.trajectory in trajectories for p in replayed)
    direct = [p for p in pm.candidates() if p.trajectory is None]
    assert [p.hparams for p in direct] == [{"iter_cap": 5, "z_threshold": 2.12, "green_size": None}]
    assert pm.part("trajectories") == pm.trajectories()
    with pytest.raises(ConfigError):
        pm.part("nope")


def test_grid_errors(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text("method: y\nspace: {a: [1]}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="method must be"):
        load_grid("x", tmp_path)
    (tmp_path / "x.yaml").write_text(
        "method: x\nspace: {a: [1, 2]}\ninclude: [{a: 1}]\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="duplicated"):
        load_grid("x", tmp_path)
    (tmp_path / "x.yaml").write_text(
        "method: x\nspace: {a: [1]}\nreplay: {fixed: {b: 1}}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="replay.fixed"):
        load_grid("x", tmp_path)
    with pytest.raises(ConfigError, match="no HPO grid"):
        load_grid("missing", tmp_path)


LOCAL_GRID = """method: x
space: {gamma: [0.25], delta: [3.0, 4.0]}
local:
  - hparams: {gamma: 0.25, delta: 5.0}
    cells: [{model_id: m1, language: python}, {model_id: m2, language: java}]
  - hparams: {gamma: 0.25, delta: 6.0}
    cells: [{model_id: m1, language: python}]
"""


def test_local_points_apply_only_to_their_cells(tmp_path: Path) -> None:
    """Estensione locale della griglia (D31): i punti di ``local`` valgono solo nelle celle
    elencate; senza modello e linguaggio la griglia li comprende tutti."""
    (tmp_path / "x.yaml").write_text(LOCAL_GRID, encoding="utf-8")
    grid = load_grid("x", tmp_path)
    deltas = lambda points: sorted(p.hparams["delta"] for p in points)  # noqa: E731
    assert deltas(grid.candidates()) == [3.0, 4.0, 5.0, 6.0]
    assert deltas(grid.candidates("m1", "python")) == [3.0, 4.0, 5.0, 6.0]
    assert deltas(grid.candidates("m2", "java")) == [3.0, 4.0, 5.0]
    assert deltas(grid.candidates("m2", "python")) == [3.0, 4.0]
    assert deltas(grid.candidates("m1", "java")) == [3.0, 4.0]
    local = [p for p in grid.candidates() if p.cells is not None]
    assert local[0].cells == (("m1", "python"), ("m2", "java"))
    assert local[0].applies_to("m1", None) and not local[1].applies_to("m2", None)


@pytest.mark.parametrize(
    ("local", "message"),
    [
        ("  - {hparams: {gamma: 0.25, delta: 4.0}, cells: [{model_id: m, language: python}]}\n",
         "duplicated"),
        ("  - {hparams: {gamma: 0.25, delta: 5.0}}\n", "exactly 'hparams' and 'cells'"),
        ("  - {hparams: {gamma: 0.25, delta: 5.0}, cells: []}\n", "non-empty list"),
        ("  - {hparams: {gamma: 0.25, delta: 5.0}, cells: [{model_id: m}]}\n", "non-empty list"),
    ],
)  # fmt: skip
def test_local_points_errors(tmp_path: Path, local: str, message: str) -> None:
    text = "method: x\nspace: {gamma: [0.25], delta: [3.0, 4.0]}\nlocal:\n" + local
    (tmp_path / "x.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_grid("x", tmp_path)


class FakeAdapter:
    """Adapter minimo per ``resolve``: hash = SHA-256 degli iperparametri."""

    name = "stone"

    def default_hparams(self) -> dict[str, Any]:
        return {"gamma": 0.5, "delta": 1.0}

    def config_hash(self, hp: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(hp, sort_keys=True).encode()).hexdigest()[:12]


def test_resolve_default_grid_point_and_unknown_hash() -> None:
    adapter, cfg = FakeAdapter(), SimpleNamespace()
    hp, h, point = resolve(cfg, adapter, None)  # type: ignore[arg-type]
    assert hp == {"gamma": 0.5, "delta": 1.0} and h == adapter.config_hash(hp)
    target = {"gamma": 0.25, "delta": 10.0}
    hp, h, _ = resolve(cfg, adapter, adapter.config_hash(target))  # type: ignore[arg-type]
    assert hp == target
    with pytest.raises(ConfigError, match="not in the HPO grid"):
        resolve(cfg, adapter, "deadbeef")  # type: ignore[arg-type]


# ---------------------------------------------------------------------- criterio
def _cand(h: str, tpr: float, drop: float) -> Candidate:
    detected = 0 if math.isnan(tpr) else round(tpr * 100)
    return Candidate(h, {}, 100, detected, tpr, 0.7, 0.7 + drop / 100, drop)


def test_criterion_max_tpr_within_the_constraint() -> None:
    crit = ConstrainedTprCriterion(3.0)
    sel = crit.select([_cand("a", 0.9, 5.0), _cand("b", 0.5, 2.0), _cand("c", 0.6, 3.0)])
    assert sel.candidate.config_hash == "c" and sel.constraint_satisfied and sel.n_admissible == 2
    # Parità di TPR: minima perdita, poi hash minore.
    sel = crit.select([_cand("b", 0.6, 1.0), _cand("a", 0.6, 2.0), _cand("c", 0.6, 1.0)])
    assert sel.candidate.config_hash == "b"
    # Nessun ammissibile: minima perdita, vincolo non rispettato (caso da segnalare).
    sel = crit.select([_cand("a", 0.9, 9.0), _cand("b", 0.1, 4.0)])
    assert sel.candidate.config_hash == "b" and not sel.constraint_satisfied
    with pytest.raises(ConfigError):
        crit.select([_cand("a", float("nan"), 1.0)])
    with pytest.raises(ConfigError):
        crit.select([])


# ---------------------------------------------------------------------- PromptMark, ADR-009
def _it(t: int, correct: bool, p: float) -> dict[str, Any]:
    return {
        "iteration": t,
        "correctness": correct,
        "p_exact": p,
        "meets_z": False,
        "code": f"code{t}",
        "response": f"```python\ncode{t}\n```",
        "n_free_identifiers": t + 1,
    }


TRAJECTORY = [_it(0, False, 0.5), _it(1, True, 0.04), _it(2, True, 0.01), _it(3, True, 0.3)]


def test_replay_selection() -> None:
    assert replay_selection(TRAJECTORY, 5, 2.12) == (2, 3)
    assert replay_selection(TRAJECTORY, 5, 1.64) == (1, 2)
    assert replay_selection(TRAJECTORY, 2, 2.12) == (1, 2)
    assert replay_selection(TRAJECTORY, 5, 1e6) == (1, 4)
    assert replay_selection([_it(0, False, 0.001), _it(1, False, 0.001)], 5, 2.12) == (0, 2)


def _trajectory_result(item_id: str) -> WorkerResult:
    return WorkerResult(
        item_id=item_id,
        sample_index=0,
        status="OK",
        raw_output=TRAJECTORY[1]["response"],
        code=None,
        score=None,
        native_decision=None,
        decoded_message=None,
        extra={
            "iterations": TRAJECTORY,
            "seeds": [1, 2, 3, 4],
            "n_iterations": 4,
            "n_example_tests": 2,
        },
        error=None,
    )


def test_replay_result_fields() -> None:
    out = replay_result(_trajectory_result("humaneval/0#0"), 3, 2.12)
    assert out.raw_output == TRAJECTORY[2]["response"]
    extra = out.extra
    assert extra["n_iterations"] == 3 and extra["selected_iteration"] == 2
    assert extra["selected_code"] == "code2" and extra["watermarked"] is True
    assert extra["n_free_identifiers"] == 3 and extra["seeds"] == [1, 2, 3]
    assert extra["n_retries_correctness"] == 1 and extra["n_retries_watermark"] == 1
    assert [it["meets_z"] for it in extra["iterations"]] == [False, False, True]
    assert extra["n_example_tests"] == 2  # i campi non legati alla scelta restano
    failed = replace_status(_trajectory_result("x#0"), "FAILED")
    assert replay_result(failed, 3, 2.12) is failed


def replace_status(result: WorkerResult, status: str) -> WorkerResult:
    import dataclasses

    return dataclasses.replace(result, status=status)


def test_embed_from_trajectory(
    cfg: ExperimentConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)
    monkeypatch.setattr(PromptMarkAdapter, "to_native_hparams", lambda self, hp: dict(hp))
    adapter = PromptMarkAdapter(cfg.methods_catalog["promptmark"], cfg, echo_client(cfg, 60))
    problem = Problem(
        problem_key="humaneval/0",
        dataset="humanevalplus",
        level="L1",
        language="python",
        split="dev",
        prompt_text="def f():\n",
        entry_point="f",
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )
    trajectory_dir = tmp_path / "traj" / "L1_python_dev"
    trajectory_dir.mkdir(parents=True)
    rows = [_trajectory_result(f"humaneval/0#{i}").to_dict() for i in range(2)]
    (trajectory_dir / "results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    hp = {"iter_cap": 5, "z_threshold": 1.64, "green_size": 8}
    model = cfg.models_catalog[MODEL]
    run = adapter.embed_from_trajectory([problem], hp, model, "k1", 2, trajectory_dir)
    assert run.replayed_from == "traj" and run.worker is None
    assert all(s.code.strip().endswith("code1") for s in run.samples)
    assert all(s.raw_output == TRAJECTORY[1]["response"] for s in run.samples)
    assert [m["n_iterations"] for m in run.metrics] == [2, 2]
    assert [m["n_sites"] for m in run.metrics] == [2, 2]
    with pytest.raises(ConfigError, match="lacks"):
        adapter.embed_from_trajectory([problem], hp, model, "k1", 3, trajectory_dir)


def test_replay_without_correctness_loop_depends_only_on_p_and_eta() -> None:
    """L2 (D27): con ``test_list`` vuota ogni iterazione è corretta; lo stop dipende solo da p e η
    e la candidata senza stop è la prima, come in ``run_phase1`` (ADR-009 valida anche su L2)."""
    its = [_it(0, True, 0.5), _it(1, True, 0.04), _it(2, True, 0.01), _it(3, True, 0.3)]
    assert replay_selection(its, 5, 2.12) == (2, 3)
    assert replay_selection(its, 5, 1.64) == (1, 2)
    assert replay_selection(its, 2, 2.58) == (0, 2)  # nessuno stop: la prima (best)
    assert replay_selection(its, 5, 1e6) == (0, 4)
    extra = replay_result(
        dataclasses.replace(_trajectory_result("codenet/p0#0"), extra={"iterations": its}),
        5,
        1.64,
    ).extra
    assert extra["n_retries_correctness"] == 0 and extra["n_retries_watermark"] == 1


def test_embed_from_trajectory_checks_the_correctness_loop(
    cfg: ExperimentConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)
    monkeypatch.setattr(PromptMarkAdapter, "to_native_hparams", lambda self, hp: dict(hp))
    adapter = PromptMarkAdapter(cfg.methods_catalog["promptmark"], cfg, echo_client(cfg, 60))
    problem = Problem(
        problem_key="codenet/p00001",
        dataset="codenet",
        level="L2",
        language="python",
        split="dev",
        prompt_text="Read N.",
        entry_point=None,
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )
    trajectory_dir = tmp_path / "traj" / "L2_python_dev"
    trajectory_dir.mkdir(parents=True)
    hp = {"iter_cap": 5, "z_threshold": 1.64, "green_size": 8}
    model = cfg.models_catalog[MODEL]

    def write(loop: bool | None) -> None:
        rows = []
        for i in range(2):
            row = _trajectory_result(f"codenet/p00001#{i}").to_dict()
            if loop is not None:
                row["extra"]["correctness_loop"] = loop
            rows.append(row)
        (trajectory_dir / "results.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    write(None)  # traiettoria senza il campo: ciclo attivo, come su L1
    with pytest.raises(ConfigError, match="correctness loop differs"):
        adapter.embed_from_trajectory([problem], hp, model, "k1", 2, trajectory_dir)
    write(False)
    run = adapter.embed_from_trajectory([problem], hp, model, "k1", 2, trajectory_dir)
    assert run.native_hparams["correctness_loop"] is False


# ---------------------------------------------------------------------- sweep completo
GRID = "method: stone\nspace:\n  gamma: [0.5]\n  delta: [1.0, 2.0]\n"


@pytest.fixture
def sweep(
    cfg: ExperimentConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> BenchmarkFacade:
    grids = tmp_path / "grids"
    grids.mkdir()
    (grids / "stone.yaml").write_text(GRID, encoding="utf-8")

    def fake_grid_path(method: str, root: Path | None = None) -> Path:
        return (root or grids) / f"{method}.yaml"

    monkeypatch.setattr(grid_mod, "grid_path", fake_grid_path)
    monkeypatch.setattr(tune_mod, "grid_path", fake_grid_path)
    build_tiny_datasets(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    BenchmarkFacade(tcfg).run_stage("prepare_data")
    tcfg = rebuild(tcfg, methods=["stone"], models=[MODEL], languages=["python"], splits=["dev"])
    FakeBackend.calls, FakeBackend.fail_after = [], None
    FakeSandbox.jobs, FakeSandbox.files = [], []
    FakeSandbox.fail_after, FakeSandbox.broken = None, False
    monkeypatch.setattr(gb, "BACKEND_FACTORY", FakeBackend)
    monkeypatch.setattr(wm, "WORKER_FACTORY", echo_client)
    monkeypatch.setattr(sb, "make_sandbox", lambda config: FakeSandbox())
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)
    base = BenchmarkFacade(tcfg)
    for stage in ("generate_baseline", "evalplus_groundtruth"):
        base.run_stage(stage)
    base_exec = rebuild(
        tcfg, execution={**tcfg.execution.model_dump(), "sources": ["llm_baseline"]}
    )
    BenchmarkFacade(base_exec).run_stage("execute")
    swept = rebuild(
        tcfg,
        hpo={**tcfg.hpo.model_dump(), "grid": "stone"},
        execution={**tcfg.execution.model_dump(), "sources": ["llm_watermarked"]},
    )
    return BenchmarkFacade(swept)


def test_sweep_cells_and_tune(sweep: BenchmarkFacade) -> None:
    cfg = sweep.cfg
    planner = CellPlanner(cfg)
    hashes = {c.config_hash for c in planner.cells_for(WatermarkStage)}
    assert len(hashes) == 2 and None not in hashes
    for stage_cls in (ExecuteStage, DetectStage, CalibrateStage):
        assert {c.config_hash for c in planner.cells_for(stage_cls)} == hashes, stage_cls
    for stage in ("watermark", "execute", "detect", "calibrate"):
        assert sweep.run_stage(stage).ran == 2, stage
    for h in hashes:
        assert sweep.store.exists(threshold_ref("stone", MODEL, "python", str(h)))
    assert sweep.run_stage("tune").ran == 1
    selected = sweep.store.read_model(selected_ref("stone", MODEL, "python"), SelectedConfig)
    table = sweep.store.read_table(candidates_ref("stone", MODEL, "python"))
    assert len(table) == 2 and set(table["config_hash"]) == hashes
    assert selected.n_candidates == 2 and selected.provisional and selected.levels == ["L1"]
    assert table["selected"].sum() == 1
    chosen = table[table["selected"]].iloc[0]
    assert chosen["config_hash"] == selected.config_hash
    assert json.loads(chosen["hparams_json"]) == selected.hparams
    # La scelta segue il criterio sulla tabella dei candidati.
    expect = ConstrainedTprCriterion(cfg.hpo.max_pass1_drop_pp).select(
        [
            Candidate(
                r.config_hash,
                json.loads(r.hparams_json),
                int(r.n_positives),
                int(r.n_detected),
                float(r.tpr_dev),
                float(r.pass1_dev),
                float(r.pass1_baseline_dev),
                float(r.pass1_drop_pp),
            )
            for r in table.itertuples()
        ]
    )
    assert expect.candidate.config_hash == selected.config_hash
    assert selected.constraint_satisfied == expect.constraint_satisfied
    assert isinstance(table, pd.DataFrame)


def test_local_point_in_sweep_and_tune(sweep: BenchmarkFacade, tmp_path: Path) -> None:
    """Un punto di ``local`` per la cella dello sweep entra in watermark → tune; uno per
    un'altra cella non entra né nello sweep né nella selezione (D31)."""
    (tmp_path / "grids" / "stone.yaml").write_text(
        GRID
        + "local:\n"
        + f"  - hparams: {{gamma: 0.5, delta: 3.0}}\n    cells: [{{model_id: {MODEL}, "
        + "language: python}]\n"
        + "  - hparams: {gamma: 0.5, delta: 4.0}\n    cells: [{model_id: other_model, "
        + "language: python}]\n",
        encoding="utf-8",
    )
    planner = CellPlanner(sweep.cfg)
    adapter = wm.make_adapter(sweep.cfg, "stone")
    local = adapter.config_hash({"gamma": 0.5, "delta": 3.0})
    elsewhere = adapter.config_hash({"gamma": 0.5, "delta": 4.0})
    for stage_cls in (WatermarkStage, ExecuteStage, DetectStage, CalibrateStage):
        hashes = {c.config_hash for c in planner.cells_for(stage_cls)}
        assert len(hashes) == 3 and local in hashes and elsewhere not in hashes, stage_cls
    for stage in ("watermark", "execute", "detect", "calibrate"):
        assert sweep.run_stage(stage).ran == 3, stage
    assert sweep.run_stage("tune").ran == 1
    table = sweep.store.read_table(candidates_ref("stone", MODEL, "python"))
    selected = sweep.store.read_model(selected_ref("stone", MODEL, "python"), SelectedConfig)
    assert len(table) == 3 and local in set(table["config_hash"])
    assert elsewhere not in set(table["config_hash"]) and selected.n_candidates == 3
