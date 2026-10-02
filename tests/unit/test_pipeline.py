"""Test di Pipeline, osservatori, CellPlanner e facade (SPEC §7.10, criterio M1)."""

from __future__ import annotations

from typing import ClassVar

import pandas as pd
import pytest

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError, InvariantViolation, MissingInputError
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.observers import InvariantObserver, StageObserver, StageRun, TimingObserver
from bench.pipeline.pipeline import Pipeline, producers_from_registry
from bench.pipeline.stage import Cell, CellPlanner, Stage, StageContext
from bench.store.artifact_store import ArtifactStore
from bench.store.manifest import ProvenanceCollector
from bench.store.refs import ArtifactRef
from tests.conftest import rebuild


class FakeStage(Stage):
    """Fase fittizia: scrive una tabella per cella e conta le esecuzioni."""

    name: ClassVar[str] = "fake"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"fake"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method",)

    def __init__(self, rows: int = 4, declared_extra: int = 0, write: bool = True) -> None:
        self.rows = rows
        self.declared_extra = declared_extra
        self.write = write
        self.calls = 0

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [ArtifactRef.of("fake", f"fake/{cell.slug()}.parquet")]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        self.calls += 1
        if not self.write:
            return
        df = pd.DataFrame({"i": range(self.rows)})
        ref = self.outputs(cell)[0]
        manifest = ctx.make_manifest(
            ref,
            n_rows_in=self.rows,
            n_rows_out=len(df),
            n_rows_expected=self.rows + self.declared_extra,
        )
        ctx.store.write_table(ref, df, manifest)


class NeedsUpstream(FakeStage):
    name: ClassVar[str] = "needs_upstream"

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return [ArtifactRef.of("upstream", "up/data.parquet")]


class Recorder(StageObserver):
    def __init__(self) -> None:
        self.events: list[str] = []

    def on_start(self, stage: Stage, cell: Cell) -> None:
        self.events.append(f"start:{cell.key()}")

    def on_end(self, stage: Stage, cell: Cell, run: StageRun) -> None:
        self.events.append(f"end:{cell.key()}")

    def on_skip(self, stage: Stage, cell: Cell) -> None:
        self.events.append(f"skip:{cell.key()}")

    def on_error(self, stage: Stage, cell: Cell, exc: BaseException) -> None:
        self.events.append(f"error:{type(exc).__name__}")


def _pipeline(
    cfg: ExperimentConfig, stage: Stage, *observers: StageObserver
) -> tuple[Pipeline, ArtifactStore]:
    store = ArtifactStore(cfg.paths.artifacts, producers={"upstream": "make_upstream"})
    obs = [InvariantObserver(store), *observers]
    return Pipeline(
        [stage], store, obs, config=cfg, provenance=ProvenanceCollector(cfg.paths.repo)
    ), store


CELLS = [Cell(method="sweet"), Cell(method="stone")]


def test_acceptance_write_skip_force(cfg: ExperimentConfig) -> None:
    """Criterio M1: la fase scrive, al secondo lancio viene saltata, con force rieseguita."""
    stage = FakeStage()
    recorder = Recorder()
    pipeline, store = _pipeline(cfg, stage, recorder)

    first = pipeline.run(CELLS)
    assert (first.ran, first.skipped) == (2, 0)
    assert stage.calls == 2
    ref = stage.outputs(CELLS[0])[0]
    assert store.exists(ref)
    started_first = store.read_manifest(ref).started_at  # type: ignore[union-attr]

    second = pipeline.run(CELLS)
    assert (second.ran, second.skipped) == (0, 2)
    assert stage.calls == 2

    third = pipeline.run(CELLS, force=True)
    assert (third.ran, third.skipped) == (2, 0)
    assert stage.calls == 4
    assert store.read_manifest(ref).started_at > started_first  # type: ignore[union-attr]
    assert recorder.events[:4] == [
        "start:method=sweet",
        "end:method=sweet",
        "start:method=stone",
        "end:method=stone",
    ]
    assert recorder.events[4:6] == ["skip:method=sweet", "skip:method=stone"]


def test_manifest_content(cfg: ExperimentConfig) -> None:
    stage = FakeStage()
    pipeline, store = _pipeline(cfg, stage)
    pipeline.run([CELLS[0]])
    manifest = store.read_manifest(stage.outputs(CELLS[0])[0])
    assert manifest is not None
    assert manifest.stage == "fake"
    assert manifest.cell["method"] == "sweet"
    assert manifest.global_seed == 20261001
    assert manifest.config["global_seed"] == 20261001
    assert len(manifest.config_sha256) == 64
    assert manifest.n_rows_out == manifest.n_rows_expected == 4
    assert manifest.finished_at is not None and manifest.finished_at >= manifest.started_at


def test_missing_input_names_the_producer(cfg: ExperimentConfig) -> None:
    pipeline, _ = _pipeline(cfg, NeedsUpstream())
    with pytest.raises(MissingInputError, match="run stage 'make_upstream' first"):
        pipeline.run(CELLS)


def test_invariant_observer_detects_lost_row(cfg: ExperimentConfig) -> None:
    recorder = Recorder()
    pipeline, _ = _pipeline(cfg, FakeStage(declared_extra=1), recorder)
    with pytest.raises(InvariantViolation, match="I1 violated"):
        pipeline.run([CELLS[0]])
    assert recorder.events[-1] == "error:InvariantViolation"


def test_invariant_observer_checks_real_row_count(cfg: ExperimentConfig) -> None:
    class Liar(FakeStage):
        def run(self, cell: Cell, ctx: StageContext) -> None:
            ref = self.outputs(cell)[0]
            df = pd.DataFrame({"i": range(3)})
            ctx.store.write_table(
                ref, df, ctx.make_manifest(ref, n_rows_in=4, n_rows_out=4, n_rows_expected=4)
            )

    pipeline, _ = _pipeline(cfg, Liar())
    with pytest.raises(InvariantViolation, match="contains 3 rows"):
        pipeline.run([CELLS[0]])


def test_stage_that_writes_nothing_is_an_invariant_violation(cfg: ExperimentConfig) -> None:
    pipeline, _ = _pipeline(cfg, FakeStage(write=False))
    with pytest.raises(InvariantViolation, match="did not write"):
        pipeline.run([CELLS[0]])


def test_stage_exception_propagates_and_notifies(cfg: ExperimentConfig) -> None:
    class Broken(FakeStage):
        def run(self, cell: Cell, ctx: StageContext) -> None:
            raise RuntimeError("boom")

    recorder = Recorder()
    pipeline, _ = _pipeline(cfg, Broken(), recorder)
    with pytest.raises(RuntimeError, match="boom"):
        pipeline.run([CELLS[0]])
    assert recorder.events == ["start:method=sweet", "error:RuntimeError"]


def test_timing_observer(cfg: ExperimentConfig) -> None:
    timing = TimingObserver(cfg.paths.artifacts)
    pipeline, _ = _pipeline(cfg, FakeStage(), timing)
    pipeline.run(CELLS)
    assert [r.cell for r in timing.runs] == ["method=sweet", "method=stone"]
    assert timing.path is not None
    assert len(timing.path.read_text(encoding="utf-8").splitlines()) == 2


def test_cell_planner(cfg: ExperimentConfig) -> None:
    small = rebuild(
        cfg, methods=["sweet", "acw"], models=["qwen25_coder_7b"], languages=["python", "java"]
    )

    class Grid(FakeStage):
        cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "language")

    cells = CellPlanner(small).cells_for(Grid)
    assert len(cells) == 4
    assert cells[0] == Cell(method="sweet", model_id="qwen25_coder_7b", language="python")

    class NoAxes(FakeStage):
        cell_axes: ClassVar[tuple[str, ...]] = ()

    assert CellPlanner(small).cells_for(NoAxes) == [Cell()]

    class Bad(FakeStage):
        cell_axes: ClassVar[tuple[str, ...]] = ("config_hash",)

    with pytest.raises(ConfigError, match="cannot plan"):
        CellPlanner(small).cells_for(Bad)


def test_cell_key_and_slug() -> None:
    assert Cell().key() == "all"
    cell = Cell(method="sweet", attack_id="T1.4")
    assert cell.key() == "method=sweet__attack_id=T1.4"
    assert Cell(method="a/b c").slug() == "method=a_b_c"


def test_facade_selftest_runs_then_skips(cfg: ExperimentConfig) -> None:
    facade = BenchmarkFacade(cfg)
    first = facade.run_stage("selftest")
    assert (first.ran, first.skipped) == (1, 0)
    second = facade.run_stage("selftest")
    assert (second.ran, second.skipped) == (0, 1)
    forced = facade.run_stage("selftest", force=True)
    assert forced.ran == 1
    log = cfg.paths.artifacts / "_logs" / "selftest" / "all.log"
    assert "done in" in log.read_text(encoding="utf-8")
    assert (cfg.paths.artifacts / "_selftest" / "selftest.parquet").is_file()
    assert producers_from_registry()["selftest"] == "selftest"


def test_facade_unknown_stage(cfg: ExperimentConfig) -> None:
    with pytest.raises(ConfigError, match="unknown stage 'nope'"):
        BenchmarkFacade(cfg).run_stage("nope")
