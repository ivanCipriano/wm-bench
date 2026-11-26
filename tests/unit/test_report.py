"""Report della M14 (``bench.report``): raccolta, tabelle, LaTeX, ROC e annotazioni."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bench.config.builder import config_dir
from bench.config.schema import ExperimentConfig
from bench.hpo.freeze import FINAL_LEVELS, FreezeRecord, FrozenCell, freeze_ref
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, StageContext
from bench.pipeline.stages.imperceptibility import imperceptibility_ref
from bench.pipeline.stages.metrics import metrics_ref
from bench.report import (
    _elapsed,
    build_report,
    load_annotations,
    main_table,
    roc_points,
    to_latex,
)


def _write(facade: BenchmarkFacade, ref: object, obj: object) -> None:
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "test", Cell())
    manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None)  # type: ignore[arg-type]
    if isinstance(obj, pd.DataFrame):
        facade.store.write_table(ref, obj, manifest)  # type: ignore[arg-type]
    else:
        facade.store.write_model(ref, obj, manifest)  # type: ignore[arg-type]


def test_roc_points() -> None:
    fpr, tpr = roc_points(np.array([3.0, 2.0]), np.array([1.0, 2.0]))
    assert fpr[0] == tpr[0] == 0.0 and fpr[-1] == tpr[-1] == 1.0
    assert list(zip(fpr[1:-1], tpr[1:-1], strict=True)) == [(0.0, 0.5), (0.5, 1.0), (1.0, 1.0)]


def test_report_from_artifacts(cfg: ExperimentConfig, tmp_path: Path) -> None:
    facade = BenchmarkFacade(cfg)
    cell = FrozenCell(model_id="m", language="python", config_hash="h1")
    record = FreezeRecord(method="stone", frozen_at=datetime.now(UTC), levels=list(FINAL_LEVELS),
                          grid_sha256=None, candidate_hashes=[], cells=[cell])  # fmt: skip
    _write(facade, freeze_ref("stone"), record)
    rows = pd.DataFrame({
        "name": ["tpr_at_fpr", "auroc", "pass_at_1", "tpr_at_fpr|codesearchnet"],
        "value": [0.5, 0.9, 0.7, 0.4], "ci_low": [0.4, 0.8, 0.6, None],
        "ci_high": [0.6, 1.0, 0.8, None], "n": [10, 20, 5, 4],
        "ci_method": ["cluster_bootstrap"] * 4,
    })  # fmt: skip
    _write(facade, metrics_ref("stone", "m", "h1", "L3", "python", "test", FINAL_LEVELS), rows)
    imp = pd.DataFrame({
        "name": ["codebleu", "adversarial_logreg_auroc_oof", "adversarial_unixcoder_auroc_oof"],
        "value": [0.8, 0.6, 0.7], "ci_low": [0.7, 0.55, 0.6], "ci_high": [0.9, 0.65, 0.8],
        "n": [5, 50, 50], "ci_method": ["cluster_bootstrap"] * 3,
    })  # fmt: skip
    _write(facade, imperceptibility_ref("stone", "m", "h1", "L1", "python", "test"), imp)
    out = tmp_path / "report"
    written = build_report(facade.store, cfg, out, config_dir() / "report" / "annotations.yaml")
    long = pd.read_csv(out / "tables" / "metrics_long.csv")
    assert len(long) == 7 and set(long["level"]) == {"L1", "L3"}
    assert set(long.loc[long["level"] == "L1", "source"]) == {"imperceptibility"}
    assert "D37" in long.loc[long["level"] == "L1", "deviations"].iloc[0]
    assert "D37" not in str(long.loc[long["level"] == "L3", "deviations"].iloc[0])
    assert not (out / "tables" / "main_L1.csv").read_text(encoding="utf-8").strip()  # solo M12
    imp_table = pd.read_csv(out / "tables" / "imperceptibility_L1.csv")
    assert imp_table.loc[0, "codebleu"] == "0.800 [0.700, 0.900]"
    assert "figures/imperceptibility_adversarial_L1" in written
    by_ds = pd.read_csv(out / "tables" / "main_L3_by_dataset.csv")
    assert list(by_ds["dataset"]) == ["codesearchnet"] and by_ds["contamination_risk"].all()
    assert float(by_ds.loc[0, "tpr_at_fpr"]) == 0.4  # senza IC
    assert long.loc[long["metric"] == "tpr_at_fpr|codesearchnet", "contamination_risk"].all()
    assert "D40" in long.loc[long["level"] == "L3", "deviations"].iloc[0]  # annotazioni di L3
    main = pd.read_csv(out / "tables" / "main_L3.csv")
    assert main.loc[0, "tpr_at_fpr"] == "0.500 [0.400, 0.600]"
    tex = (out / "tables" / "main_L3.tex").read_text(encoding="utf-8")
    assert r"\toprule" in tex and r"tpr\_at\_fpr" in tex
    assert "tables/efficiency" in written and (out / "README.md").is_file()
    assert "I6" in (out / "README.md").read_text(encoding="utf-8")


def test_annotations_and_empty_tables() -> None:
    notes = load_annotations(config_dir() / "report" / "annotations.yaml")
    d27 = next(a for a in notes if a.deviation == "D27")
    assert d27.applies("promptmark", "L3", None) and not d27.applies("stone", "L3", None)
    d19 = next(a for a in notes if a.deviation == "D19")
    assert d19.applies("sweet", "L1", None, language="javascript")
    assert not d19.applies("sweet", "L1", None, language="python")
    d37 = next(a for a in notes if a.deviation == "D37")
    assert d37.applies("stone", "L1", None, "imperceptibility")
    assert not d37.applies("stone", "L1", None, "metrics")
    assert main_table(pd.DataFrame(columns=["level", "attack", "metric"]), "L1").empty
    assert to_latex(pd.DataFrame(), "x").startswith("%")


def test_elapsed_counts_each_sample_once(tmp_path: Path) -> None:
    """Dopo una ripresa lo stesso campione ricompare: vale l'ultima riga."""
    path = tmp_path / "results.jsonl"
    lines = [
        '{"item_id": "a", "sample_index": 0, "elapsed_s": 5.0}',
        '{"item_id": "a", "sample_index": 1, "elapsed_s": 1.0}',
        '{"item_id": "a", "sample_index": 0, "elapsed_s": 2.0}',
        "",
        '{"item_id": "b", "sample_index": null, "elapsed_s": 3.0}',
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    assert _elapsed(path) == (6.0, 3)


# --------------------------------------------------------------------------- post-hoc (P6)
def _rows(values: dict[str, float]) -> pd.DataFrame:
    names = list(values)
    return pd.DataFrame({
        "name": names, "value": [values[n] for n in names],
        "ci_low": [values[n] - 0.1 for n in names], "ci_high": [values[n] + 0.1 for n in names],
        "n": [10] * len(names), "ci_method": ["cluster_bootstrap"] * len(names),
    })  # fmt: skip


def _store_with_posthoc(cfg: ExperimentConfig, with_posthoc: bool) -> BenchmarkFacade:
    """STONE congelato (una configurazione) e, se richiesto, multimark con due configurazioni
    etichettate; metriche senza attacco (L1, L3), con T1.4 (L1), T2.4k (L2) e T2.5 (L2)."""
    from bench.attacks.base import grid_attacks

    facade = BenchmarkFacade(cfg)
    t14 = next(iter(grid_attacks("T1.4")))
    k_points = list(grid_attacks("T2.4k"))[:2]
    t25 = next(iter(grid_attacks("T2.5")))
    configs: list[tuple[str, list[tuple[str | None, str]]]] = [("stone", [(None, "h1")])]
    if with_posthoc:
        configs.append(
            ("multimark", [("prudent", "e52b040770df"), ("high_capacity", "3fed1f1c1e24")])
        )
    for method, cells in configs:
        frozen = [FrozenCell(model_id="m", language="python", config_hash=h, label=label)
                  for label, h in cells]  # fmt: skip
        record = FreezeRecord(method=method, frozen_at=datetime.now(UTC),
                              levels=list(FINAL_LEVELS), grid_sha256=None, candidate_hashes=[],
                              cells=frozen)  # fmt: skip
        _write(facade, freeze_ref(method), record)
        for i, (_, h) in enumerate(cells):
            base = 0.5 + 0.1 * i
            for level in ("L1", "L3"):
                ref = metrics_ref(method, "m", h, level, "python", "test", FINAL_LEVELS)
                _write(facade, ref, _rows({"tpr_at_fpr": base, "auroc": 0.9, "pass_at_1": 0.7,
                                           "tpr_at_fpr|classeval": base}))  # fmt: skip
            attacked = [("T1.4", t14, "L1"), *[("T2.4k", k, "L2") for k in k_points]]
            if method == "multimark":  # T2.5 gira solo per i post-hoc (P6)
                attacked.append(("T2.5", t25, "L2"))
            for attack_id, ph, level in attacked:
                ref = metrics_ref(method, "m", h, level, "python", "test", FINAL_LEVELS,
                                  (attack_id, ph))  # fmt: skip
                values = {"tpr_at_fpr": base - 0.2, "auroc": 0.8, "changed_fraction": 0.9,
                          "codebleu_distance": 0.3}  # fmt: skip
                _write(facade, ref, _rows(values))
            imp = imperceptibility_ref(method, "m", h, "L1", "python", "test")
            _write(facade, imp, _rows({"codebleu": 0.8, "adversarial_logreg_auroc_oof": 0.6}))
    return facade


def _tables(out: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted((out / "tables").iterdir())}


def _with_artifacts(cfg: ExperimentConfig, root: Path) -> ExperimentConfig:
    return cfg.model_copy(update={"paths": cfg.paths.model_copy(update={"artifacts": root})})


def test_default_report_ignores_the_posthoc_methods(cfg: ExperimentConfig, tmp_path: Path) -> None:
    """Senza --with-posthoc il report dei cinque metodi è lo stesso, byte per byte, anche con
    mcss e multimark congelati nello store (P6)."""
    annotations = config_dir() / "report" / "annotations.yaml"
    cfg_a, cfg_b = _with_artifacts(cfg, tmp_path / "a"), _with_artifacts(cfg, tmp_path / "b")
    only_five = _store_with_posthoc(cfg_a, with_posthoc=False)
    with_posthoc = _store_with_posthoc(cfg_b, with_posthoc=True)
    out_a, out_b = tmp_path / "ra", tmp_path / "rb"
    written_a = build_report(only_five.store, cfg_a, out_a, annotations)
    written_b = build_report(with_posthoc.store, cfg_b, out_b, annotations)
    assert written_a == written_b
    assert _tables(out_a) == _tables(out_b)
    readme_a = (out_a / "README.md").read_text(encoding="utf-8")
    assert readme_a == (out_b / "README.md").read_text(encoding="utf-8")
    assert "label" not in pd.read_csv(out_a / "tables" / "metrics_long.csv").columns


def test_posthoc_report_keeps_the_five_methods_identical(
    cfg: ExperimentConfig, tmp_path: Path
) -> None:
    """Con i post-hoc: colonna label (una riga per configurazione), T2.5 e attacchi su L2; le righe
    dei cinque metodi sono quelle del report di default, tolta la colonna label."""
    from bench.report import METHODS, POSTHOC_METHODS

    annotations = config_dir() / "report" / "annotations.yaml"
    facade = _store_with_posthoc(cfg, with_posthoc=True)
    out_d, out_p = tmp_path / "default", tmp_path / "posthoc"
    build_report(facade.store, cfg, out_d, annotations)
    written = build_report(facade.store, cfg, out_p, annotations, (*METHODS, *POSTHOC_METHODS))
    assert {"tables/attacks_L2", "tables/attacks_L3"} <= set(written)
    for name in ("metrics_long", "main_L1", "main_L3", "main_L3_by_dataset",
                 "imperceptibility_L1", "attacks_L1"):  # fmt: skip
        default = pd.read_csv(out_d / "tables" / f"{name}.csv", dtype=str, keep_default_na=False)
        posthoc = pd.read_csv(out_p / "tables" / f"{name}.csv", dtype=str, keep_default_na=False)
        assert "label" in posthoc.columns, name
        five = posthoc[posthoc["method"].isin(METHODS)].drop(columns="label")
        assert set(five.columns) == set(default.columns), name
        five = five[list(default.columns)].reset_index(drop=True)
        assert five.equals(default.reset_index(drop=True)), name
    main = pd.read_csv(out_p / "tables" / "main_L1.csv", keep_default_na=False)
    mm = main[main["method"] == "multimark"]
    assert sorted(mm["label"]) == ["high_capacity", "prudent"]
    assert set(mm.loc[mm["label"] == "prudent", "tpr_at_fpr"]) == {"0.500 [0.400, 0.600]"}
    assert set(mm.loc[mm["label"] == "high_capacity", "tpr_at_fpr"]) == {"0.600 [0.500, 0.700]"}
    l2 = pd.read_csv(out_p / "tables" / "attacks_L2.csv", keep_default_na=False)
    assert {"T2.4k", "T2.5"} <= set(l2["attack"])
    assert (out_p / "figures" / "k_curve_L2.png").is_file()
    assert "Metodi post-hoc (P6)" in (out_p / "README.md").read_text(encoding="utf-8")
