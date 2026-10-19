"""Fasi degli attacchi (M10, ADR-012) su dati finti: celle per configurazione dell'attacco,
fase ``attack`` (I1, ``parent_id``, stati, distanza CodeBLEU, intensità nel manifest),
riferimenti di ``execute``, ``detect`` e ``metrics`` per gli attaccati, nomi dei job."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from bench.attacks.base import grid_attacks
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Source
from bench.domain.ids import sample_id
from bench.domain.models import CodeSample, Problem
from bench.pipeline import submit as sub
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner, StageContext
from bench.pipeline.stages.attack import AttackStage, attacked_ref
from bench.pipeline.stages.detect import DetectStage
from bench.pipeline.stages.execute import ExecuteStage
from bench.pipeline.stages.generate_baseline import problems_ref
from bench.pipeline.stages.metrics import MetricsStage
from bench.pipeline.stages.watermark import WatermarkStage, watermarked_ref
from tests.conftest import rebuild

METHOD, MODEL, LANG = "stone", "qwen25_coder_7b", "python"
CODES = [
    "def f(xs, k):\n    total = 0\n    for x in xs:\n        total = total + x * k\n"
    "    return total\n",
    "def f(xs, k):\n    return sum(xs) * k\n",
    "",  # estrazione fallita: codice vuoto
]


@pytest.fixture
def acfg(cfg: ExperimentConfig) -> ExperimentConfig:
    return rebuild(
        cfg,
        stage="attack",
        methods=[METHOD],
        models=[MODEL],
        languages=[LANG],
        levels=["L1"],
        splits=["dev"],
        attacks=["T1.4", "T1.5"],
    )


def test_cells_expand_over_the_attack_grids(acfg: ExperimentConfig) -> None:
    planner = CellPlanner(acfg)
    cells = planner.cells_for(AttackStage)
    assert len(cells) == 8 + 3
    assert {c.attack_params_hash for c in cells} == set(grid_attacks("T1.4")) | set(
        grid_attacks("T1.5")
    )
    attacked = rebuild(acfg, execution=acfg.execution.model_copy(update={"sources": ["attacked"]}))
    assert len(CellPlanner(attacked).cells_for(ExecuteStage)) == 11
    baseline = rebuild(
        acfg, execution=acfg.execution.model_copy(update={"sources": ["llm_baseline"]})
    )
    (only,) = CellPlanner(baseline).cells_for(ExecuteStage)
    assert only.attack_id is None and only.attack_params_hash is None
    assert len(planner.cells_for(DetectStage)) == 11
    # Senza attacchi le celle restano quelle di prima della M10.
    plain = rebuild(acfg, attacks=[])
    assert CellPlanner(plain).cells_for(AttackStage) == []
    (cell,) = CellPlanner(plain).cells_for(DetectStage)
    assert cell.attack_id is None
    assert sub.stage_methods(acfg, "attack", cells) == ["stone_T14", "stone_T15"]


def _write_inputs(facade: BenchmarkFacade) -> list[CodeSample]:
    cfg = facade.cfg
    cfg_hash = WatermarkStage(cfg).config_hash(METHOD)
    problem = Problem(
        problem_key="mbpp/1",
        dataset="mbppplus",
        level="L1",
        language=LANG,
        split="dev",
        prompt_text="",
        entry_point="f",
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )
    parents = [
        CodeSample(
            sample_id=sample_id(
                source="llm_watermarked",
                problem_key="mbpp/1",
                language=LANG,
                model_id=MODEL,
                method=METHOD,
                config_hash=cfg_hash,
                key_id="k1",
                sample_index=i,
            ),  # fmt: skip
            problem_key="mbpp/1",
            dataset="mbppplus",
            language=LANG,
            level="L1",
            split="dev",
            source=Source.LLM_WATERMARKED,
            model_id=MODEL,
            method=METHOD,
            config_hash=cfg_hash,
            key_id="k1",
            sample_index=i,
            seed=i,
            raw_output=code,
            code=code,
            extraction_ok=bool(code),
            embed_status="OK" if code else "FAILED",
            expected_message=None,
            parent_id=None,
            attack_id=None,
            attack_params_hash=None,
            attack_status=None,
            contamination_risk=False,
        )
        for i, code in enumerate(CODES)
    ]
    ctx = StageContext(cfg, facade.store, facade.provenance, "watermark", Cell(method=METHOD))
    for ref, df in (
        (problems_ref("L1", LANG), pd.DataFrame([problem.model_dump(mode="json")])),
        (
            watermarked_ref(METHOD, MODEL, cfg_hash, "L1", LANG, "dev"),
            pd.DataFrame([p.model_dump(mode="json") for p in parents]),
        ),
    ):
        manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=len(df), n_rows_expected=None)
        facade.store.write_table(ref, df, manifest)
    return parents


def test_attack_stage_writes_one_child_per_marked_sample(acfg: ExperimentConfig) -> None:
    cfg = rebuild(acfg, attacks=["T1.5"])
    facade = BenchmarkFacade(cfg)
    parents = _write_inputs(facade)
    report = facade.run_stage("attack")
    assert report.ran == 3
    stage = AttackStage(cfg)
    for cell in CellPlanner(cfg).cells_for(AttackStage):
        (ref,) = stage.outputs(cell)
        table = facade.store.read_table(ref)
        assert len(table) == len(parents)  # I1
        assert list(table["parent_id"]) == [p.sample_id for p in parents]
        assert set(table["source"]) == {"attacked"}
        assert not set(table["sample_id"]) & {p.sample_id for p in parents}
        assert table["sample_id"].is_unique
        assert list(table["embed_status"]) == ["OK", "OK", "FAILED"]  # I2: dal genitore
        statuses = list(table["attack_status"])
        assert statuses[0] == "APPLIED" and statuses[2] == "UNCHANGED"
        assert table.loc[2, "codebleu_distance"] == 0.0
        assert table.loc[0, "codebleu_distance"] > 0.0
        extra = facade.store.read_manifest(ref).extra  # type: ignore[union-attr]
        assert extra["attack_id"] == "T1.5"
        assert extra["changed_fraction"] == pytest.approx(statuses.count("APPLIED") / 3)
        assert "n_edits" in extra["detail_means_changed"]
    # Rilancio: celle complete, nessun lavoro.
    assert facade.run_stage("attack").ran == 0


def test_execute_detect_metrics_refs_for_attacked_cells(acfg: ExperimentConfig) -> None:
    cfg = rebuild(acfg, attacks=["T1.5"])
    cell = CellPlanner(cfg).cells_for(AttackStage)[0]
    h, cfg_hash = str(cell.attack_params_hash), WatermarkStage(cfg).config_hash(METHOD)
    attacked = attacked_ref("T1.5", h, METHOD, MODEL, cfg_hash, "L1", LANG, "dev")
    assert attacked.path.as_posix() == (
        f"attacked/T1.5/{h}/{METHOD}/{MODEL}/{cfg_hash}/L1_{LANG}_dev.parquet"
    )
    exe = ExecuteStage(cfg)
    exe_cell = replace(cell, source="attacked")
    assert attacked in exe.inputs(exe_cell)
    assert exe.outputs(exe_cell)[0].path.as_posix() == (
        f"execution/attacked/T1.5/{h}/{METHOD}/{MODEL}/{cfg_hash}/L1_{LANG}_dev.parquet"
    )
    det = DetectStage(cfg)
    assert det.cell_subjects(cell) == (f"attacked/T1.5/{h}",)
    assert attacked in det.inputs(cell)
    assert det.outputs(cell)[0].path.as_posix() == (
        f"detection/{METHOD}/{MODEL}/{cfg_hash}/attacked/T1.5/{h}/L1_{LANG}_dev.parquet"
    )
    met = MetricsStage(cfg)
    refs = met._refs(cell)
    assert {"attacked_det", "exec_attacked", "exec_marked", "neg_human"} <= set(refs)
    assert met.outputs(cell)[0].path.as_posix().startswith(f"metrics/attacked/T1.5/{h}/{METHOD}/")
