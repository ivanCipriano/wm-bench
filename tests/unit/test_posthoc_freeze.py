"""Congelamento e controllo sul test dei metodi post-hoc del registro (P6, ADR-019) su artefatti
finti: record con etichette, rifiuti del congelamento, controlli della parte di test (griglia,
``hpo.points``, hash non registrati, libreria e sha256), espansione delle celle nelle
configurazioni congelate, seconda chiave su dev prima del congelamento, ``attack_points`` e
``posthoc_datasets``; gli altri metodi non cambiano."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import ThresholdSet
from bench.hpo.freeze import FINAL_LEVELS, FreezeRecord, FrozenCell, load_freeze
from bench.hpo.posthoc_freeze import (
    aggregated_path,
    config_files,
    load_registry,
    plan_cells,
)
from bench.methods.base import MethodAdapter
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner, StageContext
from bench.pipeline.stages.calibrate import threshold_ref
from tests.conftest import rebuild

LIB = "b2b6c18ea5303ab134f84f11d8b1de1c2bb5ec5a"
MODEL, LANG = "qwen25_coder_7b", "python"
B, D, MCSS = "e52b040770df", "3fed1f1c1e24", "1d3087c73439"
V0 = "ebeb5a0753cd"  # ablazione della griglia, non registrata


@pytest.fixture
def lib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: LIB)
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")


@pytest.fixture
def mm(cfg: ExperimentConfig, lib: None) -> ExperimentConfig:
    return rebuild(cfg, stage="freeze", methods=["multimark"], models=[MODEL], languages=[LANG])


def _write(facade: BenchmarkFacade, ref: Any, obj: Any) -> None:
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "calibrate", Cell())
    manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None)
    facade.store.write_model(ref, obj, manifest)


def write_dev(facade: BenchmarkFacade, method: str, hashes: list[str], *,
              provisional: bool = False, aggregated: bool = True) -> None:  # fmt: skip
    """Soglie L1+L2 e soglie aggregate delle configurazioni registrate."""
    for h in hashes:
        thresholds = ThresholdSet(
            method=method, model_id=MODEL, language=LANG, config_hash=h, target_fpr=0.01,
            threshold=3.5, achieved_fpr_dev=0.008, n_negatives=2500, underpowered=False,
            created_at=datetime.now(UTC), negatives_ref="x", provisional=provisional,
            levels=["L1"] if provisional else list(FINAL_LEVELS),
        )  # fmt: skip
        _write(facade, threshold_ref(method, MODEL, LANG, h, FINAL_LEVELS), thresholds)
        if aggregated:
            path = facade.store.root / aggregated_path(method, MODEL, h)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"k": {"1": {"threshold": 3.5}}}), encoding="utf-8")


def test_registry_lists_the_three_configurations() -> None:
    reg = load_registry()
    assert reg.library_commit == LIB
    assert reg.labels("mcss") == {"main": MCSS}
    assert reg.labels("multimark") == {"prudent": B, "high_capacity": D}
    files = config_files("multimark")
    assert files[:3] == ["method/multimark.yaml", "hpo/multimark.yaml", "freeze/posthoc.yaml"]
    assert "attack/t2_5_dilution.yaml" in files


def test_posthoc_freeze_record(mm: ExperimentConfig) -> None:
    facade = BenchmarkFacade(mm)
    write_dev(facade, "multimark", [B, D])
    assert facade.run_stage("freeze").ran == 1
    record = load_freeze(facade.store, "multimark")
    assert isinstance(record, FreezeRecord) and record.library_commit == LIB
    cells = record.cells_for(MODEL, LANG)
    assert [(c.label, c.config_hash) for c in cells] == [("prudent", B), ("high_capacity", D)]
    assert cells[0].hparams.get("names.bits_per_name") == 2 and cells[0].threshold == 3.5
    assert record.cell(MODEL, LANG, D) is cells[1] or record.cell(MODEL, LANG, D) == cells[1]
    assert sorted(record.candidate_hashes) == sorted([B, D])
    assert set(record.files_sha256) == set(config_files("multimark"))
    assert set(record.aggregated_sha256) == {aggregated_path("multimark", MODEL, h) for h in (B, D)}
    with pytest.raises(ConfigError, match="already frozen"):
        BenchmarkFacade(rebuild(mm, force=True)).run_stage("freeze")


@pytest.mark.parametrize(
    ("kwargs", "commit", "message"),
    [
        ({"provisional": True}, LIB, "threshold is provisional"),
        ({"aggregated": False}, LIB, "no aggregated thresholds"),
        ({}, "3c47a1fee86f4f37535e45aa6ca09787d56881be", "patched library at 3c47a1f"),
    ],
)
def test_posthoc_freeze_refuses(
    mm: ExperimentConfig, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], commit: str,
    message: str,
) -> None:  # fmt: skip
    facade = BenchmarkFacade(mm)
    write_dev(facade, "multimark", [B, D], **kwargs)
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: commit)
    with pytest.raises(ConfigError, match=message):
        facade.run_stage("freeze")
    assert load_freeze(facade.store, "multimark") is None


def _test_cfg(cfg: ExperimentConfig, stage: str = "detect", **over: Any) -> ExperimentConfig:
    return rebuild(cfg, stage=stage, methods=["multimark"], models=[MODEL], languages=[LANG],
                   levels=["L1"], splits=["test"], **over)  # fmt: skip


def _frozen(mm: ExperimentConfig) -> BenchmarkFacade:
    facade = BenchmarkFacade(mm)
    write_dev(facade, "multimark", [B, D])
    facade.run_stage("freeze")
    return facade


def test_test_cells_use_the_frozen_configurations(mm: ExperimentConfig) -> None:
    from bench.pipeline.stages.detect import DetectStage

    _frozen(mm)
    cells = CellPlanner(_test_cfg(mm)).cells_for(DetectStage)
    assert sorted(c.config_hash for c in cells) == sorted([B, D])
    attacked = CellPlanner(_test_cfg(mm, attacks=["T2.5"])).cells_for(DetectStage)
    assert {(c.config_hash, c.attack_params_hash) for c in attacked if c.attack_id} == {
        (h, p) for h in (B, D) for p in ("c32f7d55a266", "9528392ed687", "a2bc6db7f357")
    }


def test_test_split_refused_before_the_freeze(mm: ExperimentConfig) -> None:
    from bench.pipeline.stages.detect import DetectStage

    with pytest.raises(ConfigError, match="multimark is not frozen"):
        CellPlanner(_test_cfg(mm)).cells_for(DetectStage)
    # stime in sola lettura: gli hash del registro, senza controlli
    cells = CellPlanner(_test_cfg(mm), posthoc_guard=False).cells_for(DetectStage)
    assert sorted(c.config_hash for c in cells) == sorted([B, D])


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"hpo": {"grid": "multimark"}}, "hpo.grid=multimark is not allowed"),
        ({"hpo": {"points": [B]}}, "hpo.points is not allowed"),
        ({"hpo": {"use_selected": False}}, "hpo.use_selected=false is not allowed"),
    ],
)
def test_test_split_refuses_sweeps(mm: ExperimentConfig, over: dict[str, Any],
                                   message: str) -> None:  # fmt: skip
    from bench.pipeline.stages.detect import DetectStage

    _frozen(mm)
    hpo = {**mm.hpo.model_dump(mode="json"), **over["hpo"]}
    with pytest.raises(ConfigError, match=message):
        CellPlanner(_test_cfg(mm, hpo=hpo)).cells_for(DetectStage)


def test_test_split_refuses_unregistered_hash(mm: ExperimentConfig) -> None:
    from bench.pipeline.stages.detect import DetectStage

    _frozen(mm)
    cfg = _test_cfg(mm)
    cell = Cell(method="multimark", model_id=MODEL, language=LANG, level="L1", split="test",
                config_hash=V0)  # fmt: skip
    with pytest.raises(ConfigError, match=f"config_hash {V0} is not registered"):
        plan_cells(cfg, DetectStage, [cell])
    ok = Cell(method="multimark", model_id=MODEL, language=LANG, level="L1", split="test",
              config_hash=B)  # fmt: skip
    assert plan_cells(cfg, DetectStage, [ok]) == [ok]


def test_test_split_checks_library_and_files(
    mm: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.hpo.posthoc_freeze as pf
    from bench.pipeline.stages.detect import DetectStage

    facade = _frozen(mm)
    cfg = _test_cfg(mm)
    real = pf.config_files_sha256

    def changed(m: str, root: Any = None) -> dict[str, str]:
        return {**real(m, root), "attack/t1_4_rename.yaml": "x"}

    monkeypatch.setattr(pf, "config_files_sha256", changed)
    with pytest.raises(ConfigError, match="configs/attack/t1_4_rename.yaml changed"):
        CellPlanner(cfg).cells_for(DetectStage)
    monkeypatch.setattr(pf, "config_files_sha256", real)
    path = facade.store.root / aggregated_path("multimark", MODEL, D)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="aggregated thresholds .* changed"):
        CellPlanner(cfg).cells_for(DetectStage)
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "0" * 40)
    with pytest.raises(ConfigError, match="patched library at 0000"):
        CellPlanner(cfg).cells_for(DetectStage)


def test_other_methods_and_dev_unchanged(cfg: ExperimentConfig, lib: None) -> None:
    from bench.pipeline.stages.detect import DetectStage

    dev = rebuild(cfg, stage="detect", methods=["multimark"], models=[MODEL], languages=[LANG],
                  levels=["L1"], splits=["dev"])  # fmt: skip
    cells = CellPlanner(dev).cells_for(DetectStage)
    assert all(c.config_hash is None for c in cells)  # dev senza griglia: il default, come prima
    stone = rebuild(cfg, stage="detect", methods=["stone"], models=[MODEL], languages=[LANG],
                    levels=["L1"], splits=["test"])  # fmt: skip
    with pytest.raises(ConfigError, match="stone is not frozen"):  # messaggio di prima (ADR-013)
        CellPlanner(stone).cells_for(DetectStage)
    sweep = rebuild(stone, hpo={**stone.hpo.model_dump(mode="json"), "grid": "stone",
                                "use_selected": False})  # fmt: skip
    assert CellPlanner(sweep).cells_for(DetectStage)  # falla di _selected: invariata (deviazioni)


def test_k2_stages_expand_on_dev_before_the_freeze(cfg: ExperimentConfig, lib: None) -> None:
    from bench.pipeline.stages.remark import CalibrateK2Stage, DetectK2Stage

    over = {"methods": ["multimark"], "models": [MODEL], "languages": [LANG]}
    det = rebuild(cfg, stage="detect_k2", levels=["L1", "L2"], splits=["dev"], **over)
    cells = CellPlanner(det).cells_for(DetectK2Stage)
    assert sorted({c.config_hash for c in cells}) == sorted([B, D])
    cal = rebuild(cfg, stage="calibrate_k2", levels=["L1", "L2"], **over)
    assert sorted(c.config_hash for c in CellPlanner(cal).cells_for(CalibrateK2Stage)) == sorted(
        [B, D]
    )


def test_record_cell_by_hash() -> None:
    cells = [FrozenCell(model_id=MODEL, language=LANG, label="a", config_hash=B),
             FrozenCell(model_id=MODEL, language=LANG, label="b", config_hash=D)]  # fmt: skip
    record = FreezeRecord(method="multimark", frozen_at=datetime.now(UTC), levels=["L1", "L2"],
                          grid_sha256=None, candidate_hashes=[B, D], cells=cells)  # fmt: skip
    assert record.cell(MODEL, LANG) == cells[0]  # come prima: la prima cella
    assert record.cell(MODEL, LANG, D) == cells[1] and record.cell(MODEL, LANG, V0) is None
    old = json.loads(record.model_dump_json())
    for key in ("library_commit", "library_patches_sha256", "files_sha256", "aggregated_sha256"):
        old.pop(key)
    for c in old["cells"]:
        c.pop("label")
    assert FreezeRecord.model_validate(old).library_commit is None  # record vecchi validi


# --------------------------------------------------------------------------- attack_points
def test_attack_points_filter(cfg: ExperimentConfig, lib: None) -> None:
    from bench.pipeline.stages.attack import AttackStage

    base = rebuild(cfg, stage="attack", methods=["mcss"], models=[MODEL], languages=[LANG],
                   levels=["L1"], splits=["dev"], attacks=["T1.4", "T2.5"])  # fmt: skip
    every = CellPlanner(base).cells_for(AttackStage)
    assert len(every) == 8 + 3
    only = rebuild(base, attack_points=["23f6be2801c3", "e2770ff72bae"])
    cells = CellPlanner(only).cells_for(AttackStage)
    t14 = sorted(c.attack_params_hash for c in cells if c.attack_id == "T1.4")
    assert t14 == sorted(["23f6be2801c3", "e2770ff72bae"])
    assert len([c for c in cells if c.attack_id == "T2.5"]) == 3  # senza punti scelti: tutti
    with pytest.raises(ConfigError, match="attack_points not among the points"):
        CellPlanner(rebuild(base, attack_points=["deadbeef0000"])).cells_for(AttackStage)


def test_posthoc_points_of_the_chains_have_the_right_parameters() -> None:
    from bench.attacks.base import grid_attacks
    from bench.chains import POSTHOC_POINTS

    want = {
        ("L2", "T1.4"): [{"ratio": 1.0, "name_kind": "natural"},
                         {"ratio": 0.5, "name_kind": "random"}],
        ("L2", "T1.5"): [{"k": 5}], ("L2", "T2.1"): [{"b": "all"}], ("L2", "T2.4"): [{"p": 0.5}],
        ("L2", "T4.1"): [{"rename": True}],
        ("L3", "T1.4"): [{"ratio": 1.0, "name_kind": "natural"}],
        ("L3", "T1.6"): [{"caps": "convert"}],
    }  # fmt: skip
    for level, attacks in POSTHOC_POINTS.items():
        for attack_id, hashes in attacks.items():
            grid = grid_attacks(attack_id, language=LANG)
            got = [{k: dict(grid[h].params)[k] for k in w}
                   for h, w in zip(hashes, want[(level, attack_id)], strict=True)]  # fmt: skip
            assert got == want[(level, attack_id)]


def test_posthoc_chain_steps() -> None:
    from bench.chains import all_steps, posthoc_steps

    steps = {s.key: s for s in all_steps("/wmb")}
    for method in ("mcss", "multimark"):
        mine = posthoc_steps(method, "/wmb")
        keys = {s.key for s in mine}
        assert all(not s.stage.startswith("freeze") and "splits=[dev]" not in s.overrides
                   for s in mine)  # fmt: skip
        attacks = {k.split("/")[2] for k in keys if k.startswith(f"{method}/L1/T")}
        assert attacks == {"T1.1", "T1.2", "T1.3", "T1.4", "T1.5", "T1.6", "T2.1", "T2.2",
                           "T2.4", "T2.5", "T3.1", "T4.1"}  # fmt: skip
        assert {k.split("/")[2] for k in keys if k.startswith(f"{method}/L3/T")} == {
            "T1.4", "T1.6", "T2.4k", "T2.5"}  # fmt: skip
        l3 = steps[f"{method}/L3/watermark"]
        assert "posthoc_datasets=[classeval]" in l3.overrides and l3.gpu is None
        assert steps[f"{method}/L1/T3.1/attack"].gpu is None
        assert steps[f"{method}/L1/T2.2/attack"].gpu == "auto"
        assert steps[f"{method}/M12/perplexity"].overrides[-1] == (
            "execution.sources=[llm_baseline,llm_watermarked]")  # fmt: skip
        t14 = steps[f"{method}/L2/T1.4/attack"].overrides
        t25 = steps[f"{method}/L2/T2.5/attack"].overrides
        assert any(o.startswith("attack_points=") for o in t14)
        assert not any(o.startswith("attack_points=") for o in t25)
    assert steps["sweet/L1/T3.1/attack"].gpu == "auto"  # metodi in generazione: come prima


def test_posthoc_datasets_option(cfg: ExperimentConfig) -> None:
    assert cfg.posthoc_datasets is None and cfg.attack_points is None
    assert rebuild(cfg, posthoc_datasets=["classeval"]).posthoc_datasets == ["classeval"]


def test_estimator_counts_and_gpu(local_root: Path) -> None:
    from bench.chains import Step
    from tests.unit.test_inventory_scripts import _load

    est = _load("estimate_closing_cost")
    assert est.cell_datasets(Cell(method="mcss", level="L3")) == ("classeval",)
    assert est.cell_datasets(Cell(method="stone", level="L3")) is None
    assert est.cell_datasets(Cell(method="mcss", level="L2")) is None
    assert not est.is_gpu(Step("multimark/L1/watermark", "ivan", "watermark", ()))
    assert est.is_gpu(Step("multimark/M12/unixcoder", "ivan", "adversarial_unixcoder", (),
                           gpu="auto"))  # fmt: skip
    assert est.is_gpu(Step("stone/L2/watermark", "ivan", "watermark", ()))
    steps = {"multimark/L1/watermark": 3600.0 * 10, "multimark/M12/perplexity": 3600.0 * 2}
    by = {"multimark/L1/watermark": Step("multimark/L1/watermark", "ivan", "watermark", (),
                                         jobs=4),
          "multimark/M12/perplexity": Step("multimark/M12/perplexity", "ivan", "perplexity", (),
                                           needs=("multimark/L1/watermark",))}  # fmt: skip
    line = est.posthoc_summary(steps, {"multimark/L1/watermark": False,
                                       "multimark/M12/perplexity": True}, by)  # fmt: skip
    words = line.split()
    assert words[words.index("GPU") + 1] == "2.0" and words[words.index("CPU") + 1] == "10.0"
    # cammino critico: 10 h divise fra 4 job + 2 h di perplexity = 4,5 h; 10 posti: 1,2 h
    assert "critico 4.5 h" in " ".join(words) and "posti 1.2 h" in " ".join(words)
    assert est.posthoc_summary({}, {}, {}) == ""


def test_p6_script() -> None:
    """``scripts/run_p6.sh``: libreria e hash del registro; nessun invio sulla parte di test (solo
    ``--dry-run`` che deve essere rifiutato); congelamento solo con l'approvazione esplicita."""
    import re

    from tests.conftest import REPO_ROOT

    text = (REPO_ROOT / "scripts" / "run_p6.sh").read_text(encoding="utf-8")
    reg = load_registry()
    assert f"EXPECTED={reg.library_commit}" in text
    assert f"MCSS={MCSS}" in text and f"PRUDENT={B}" in text and f"HIGH={D}" in text
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    submits = [ln for ln in code.splitlines() if ln.strip().startswith("submit ")]
    assert submits and not any("test" in s for s in submits)
    assert re.findall(r'"splits=\[[^\]]*\]"', code) == ['"splits=[dev]"', '"splits=[test]"']
    assert "bench submit --dry-run" in code
    assert '[ "${FREEZE_APPROVED:-}" = yes ]' in code


def test_only_datasets_keeps_classeval() -> None:
    from bench.domain.models import CodeSample, Problem
    from bench.pipeline.stages.watermark import only_datasets

    def problem(key: str, dataset: str) -> Any:
        return Problem.model_construct(problem_key=key, dataset=dataset)

    def sample(key: str, dataset: str) -> Any:
        return CodeSample.model_construct(problem_key=key, dataset=dataset)

    problems = [problem("c1", "classeval"), problem("s1", "codesearchnet")]
    parents = [sample("c1", "classeval"), sample("s1", "codesearchnet"), sample("c1", "classeval")]
    kept_p, kept_s = only_datasets(problems, parents, ["classeval"], "cell")
    assert [p.problem_key for p in kept_p] == ["c1"] and len(kept_s) == 2
    with pytest.raises(ConfigError, match="leaves no problems"):
        only_datasets(problems, parents, ["humanevalplus"], "cell")
