"""Inventario dei siti di P0: ``export_inventory_inputs.py`` e ``inventory_sites.py`` (scripts/).

``inventory_sites.py`` si prova con la libreria del submodule ``third_party/multimark`` (stesso
contenuto della copia ``build/patched/multimark``, zero patch).
"""

from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from bench.domain.enums import Language, Level, Split
from bench.domain.models import Problem
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stages.evalplus_groundtruth import groundtruth_ref, load_groundtruth
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from tests.conftest import REPO_ROOT
from tests.unit.test_execute import facade  # noqa: F401  (fixture: L1 piccolo con baseline)
from tests.unit.test_generate_baseline import MODEL

LIBRARY = REPO_ROOT / "third_party" / "multimark"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"wmb_{name}", REPO_ROOT / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


export = _load("export_inventory_inputs")
inv = _load("inventory_sites")

needs_library = pytest.mark.skipif(
    not (LIBRARY / "multimark" / "inventory.py").is_file(),
    reason="third_party/multimark not initialised",
)

CODE_A = (
    "def f(xs):\n    total = 0\n    for x in xs:\n        total = total + 1\n    return total\n"
)
CODE_B = "def g(alpha, beta):\n    if alpha < 3:\n        return []\n    return beta\n"


def _problem(key: str, dataset: str = "humanevalplus") -> Problem:
    return Problem(
        problem_key=key,
        dataset=dataset,
        level=Level.L1,
        language=Language.PYTHON,
        split=Split.DEV,
        prompt_text="",
        entry_point="f",
        canonical_solution="x",
        test_ref=key,
        contamination_risk=False,
        loc_to_generate=None,
    )


# ---------------------------------------------------------------- export_inventory_inputs


def test_groups_cover_the_requested_datasets() -> None:
    names = [g.name for g in export.groups_for(["m1", "m2"])]
    assert names == [
        "L1_dev_canonical",
        "L1_dev_baseline_m1",
        "L1_dev_baseline_m2",
        "L2_dev_canonical",
        "L2_dev_baseline_m1",
        "L2_dev_baseline_m2",
        "L3_classeval_canonical",
    ]
    classeval = export.groups_for([])[-1]
    assert (classeval.level, classeval.split, classeval.dataset) == ("L3", None, "classeval")


def test_canonical_rows_use_the_executor_code_and_are_sorted() -> None:
    problems = [_problem("p2", "mbppplus"), _problem("p1"), _problem("p0", "mbppplus")]
    codes = {"p0": CODE_A, "p1": CODE_B, "p2": CODE_A + "\n# c\n"}
    rows = export.canonical_rows(problems, lambda p: codes[p.problem_key])
    assert [(r["dataset"], r["problem_key"]) for r in rows] == [
        ("humanevalplus", "p1"),
        ("mbppplus", "p0"),
        ("mbppplus", "p2"),
    ]
    first = rows[0]
    assert first["code"] == CODE_B
    assert first["source"] == "canonical"
    assert first["model_id"] is None and first["sample_index"] is None
    assert first["split"] == "dev" and first["language"] == "python"
    assert first["loc"] == 4
    assert tuple(first) == export.ROW_FIELDS


def test_baseline_rows_keep_sample_fields() -> None:
    records = [
        {
            "sample_id": f"s{i}",
            "code": code,
            "dataset": "codenet",
            "problem_key": "p",
            "language": "python",
            "model_id": "m",
            "sample_index": i,
            "split": "dev",
        }
        for i, code in [(1, CODE_B), (0, CODE_A)]
    ]
    rows = export.baseline_rows(records)
    assert [r["sample_index"] for r in rows] == [0, 1]
    assert rows[0]["source"] == "baseline" and rows[0]["model_id"] == "m"
    assert rows[0]["loc"] == 5


def test_jsonl_is_deterministic_and_write_skips_identical(tmp_path: Path) -> None:
    rows = export.canonical_rows([_problem("p1")], lambda p: CODE_A)
    data = export.jsonl_bytes(rows)
    assert data == export.jsonl_bytes(list(rows))
    assert data.endswith(b"\n") and b"\r" not in data
    path = tmp_path / "out" / "g.jsonl"
    assert export.write_if_new(path, data) == "written"
    assert export.write_if_new(path, data) == "unchanged"
    with pytest.raises(export.ExportError):
        export.write_if_new(path, data + b"{}\n")
    assert path.read_bytes() == data  # il file esistente non si tocca


def test_export_end_to_end_on_tiny_l1(
    facade: BenchmarkFacade,  # noqa: F811
    tmp_path: Path,
) -> None:
    facade.run_stage("evalplus_groundtruth")
    store = facade.store
    groups = ["L1_dev_canonical", f"L1_dev_baseline_{MODEL}"]
    out = tmp_path / "inputs"
    manifests = export.export(facade.cfg, store, out, [MODEL], groups)
    assert sorted(manifests) == sorted(groups)

    problems = store.read_table(problems_ref("L1", "python"))
    dev = problems[problems["split"] == "dev"]
    gt = load_groundtruth(store.read_table(groundtruth_ref()))
    lines = (out / "L1_dev_canonical.jsonl").read_text(encoding="utf-8").splitlines()
    canonical = [json.loads(x) for x in lines]
    assert len(canonical) == len(dev) > 0
    for row in canonical:  # il codice è quello dell'executor (ground truth di EvalPlus)
        test_ref = dev.loc[dev["problem_key"] == row["problem_key"], "test_ref"].item()
        assert row["code"] == gt[test_ref]["canonical_program"]
        assert row["split"] == "dev" and row["source"] == "canonical"

    baseline = store.read_table(baseline_ref(MODEL, "L1", "python", "dev"))
    lines = (out / f"L1_dev_baseline_{MODEL}.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(x) for x in lines]
    assert sorted(r["sample_id"] for r in rows) == sorted(baseline["sample_id"])
    assert {r["model_id"] for r in rows} == {MODEL}
    manifest = json.loads(
        (out / f"L1_dev_baseline_{MODEL}.jsonl.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["input"]["path"] == f"baseline/{MODEL}/L1_python_dev.parquet"
    assert manifest["input"]["sha256"] is not None and manifest["rows"] == len(rows)

    # Seconda esportazione: stessi file (nessuna riscrittura); gruppo sconosciuto: errore.
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert export.export(facade.cfg, store, out, [MODEL], groups) == manifests
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    with pytest.raises(export.ExportError, match="unknown groups"):
        export.export(facade.cfg, store, out, [MODEL], ["L9_nothing"])


def test_main_builds_the_configuration(
    local_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main`` compone la configurazione come sul cluster (``stage`` obbligatorio, job 818687)."""
    calls: list[tuple[Any, ...]] = []

    def fake_export(cfg: Any, store: Any, out_dir: Path, models: list[str], only: Any) -> dict:  # type: ignore[type-arg]
        calls.append((cfg.stage, cfg.paths.artifacts, out_dir, models, only))
        return {}

    monkeypatch.setattr(export, "export", fake_export)
    out = tmp_path / "inputs"
    assert export.main(["--out-dir", str(out), "paths=local"]) == 0
    stage, artifacts, out_dir, models, only = calls[0]
    assert stage == "execute" and artifacts == local_root / "artifacts"
    assert out_dir == out and models == ["qwen25_coder_7b", "deepseek_coder_6p7b"]
    assert only is None


# ---------------------------------------------------------------- inventory_sites


def _input(path: Path, codes: list[tuple[str, str]]) -> None:
    lines = []
    for i, (dataset, code) in enumerate(codes):
        rec: dict[str, Any] = {
            "sample_id": f"s{i}",
            "code": code,
            "dataset": dataset,
            "problem_key": f"p{i}",
            "language": "python",
            "source": "canonical",
            "model_id": None,
            "sample_index": None,
            "split": "dev",
            "loc": 4,
        }
        lines.append(json.dumps(rec))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_variant_names() -> None:
    assert inv.variant_name("kind", True, False) == "canonical-kind_guards-on_params-off"
    assert inv.variant_name("paper", False, True) == "canonical-paper_guards-off_params-on"
    assert (
        inv.variant_name("paper", True, True, module_level=True)
        == "canonical-paper_guards-on_params-on_modlevel-on"
    )


def test_bands_are_the_decided_ones_closed_and_contiguous() -> None:
    assert inv.LOC_BANDS == ((1, 5), (6, 10), (11, 20), (21, 40), (41, None))
    assert inv.SITE_BANDS == ((1, 7), (8, 12), (13, 19), (20, 31), (32, 55), (56, None))
    for bands in (inv.LOC_BANDS, inv.SITE_BANDS):
        for (_, high), (low, _) in itertools.pairwise(bands):
            assert low == high + 1  # nessun buco e nessuna sovrapposizione
        for value in range(1, 200):  # ogni valore positivo sta in esattamente una fascia
            assert sum(inv.in_band(value, b) for b in bands) == 1
        assert not any(inv.in_band(0, b) for b in bands)
    assert inv.band_label("loc", (41, None)) == "loc 41+"
    assert inv.band_label("n_sites_total", (13, 19)) == "n_sites_total 13-19"


def test_strata_and_rows_outside_bands() -> None:
    def row(dataset: str, loc: int, sites: int | None) -> dict[str, Any]:
        parsed = sites is not None
        return {"dataset": dataset, "loc_bench": loc, "parse_ok": parsed, "n_sites_total": sites}

    rows = [row("a", 5, 7), row("a", 6, 8), row("b", 0, 0), row("b", 41, None)]
    got = dict(inv.strata(rows))
    assert [r["loc_bench"] for r in got["dataset=a"]] == [5, 6]
    assert [r["loc_bench"] for r in got["loc 1-5"]] == [5]
    assert [r["loc_bench"] for r in got["loc 6-10"]] == [6]
    assert [r["loc_bench"] for r in got["loc 41+"]] == [41]
    assert [r["n_sites_total"] for r in got["n_sites_total 1-7"]] == [7]
    assert [r["n_sites_total"] for r in got["n_sites_total 8-12"]] == [8]
    assert got["n_sites_total 56+"] == []
    # loc 0 e 0 siti: fuori da ogni fascia, contati; il campione non analizzato non è un sito 0.
    assert inv.outside_bands(rows) == (1, 1)


@needs_library
def test_inventory_calls_the_library_and_refuses_to_overwrite(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    _input(inputs / "L1_dev_canonical.jsonl", [("humanevalplus", CODE_A), ("mbppplus", CODE_B)])
    _input(inputs / "L1_dev_bad.jsonl", [("humanevalplus", "def (:\n")])
    out = tmp_path / "tables"
    summary = tmp_path / "summary.txt"
    argv = [
        "--input", str(inputs), "--out-dir", str(out), "--library", str(LIBRARY),
        "--summary", str(summary), "--summary-json", str(tmp_path / "s.json"),
        "--channels", "extended",
    ]  # fmt: skip
    assert inv.main(argv) == 0

    from multimark.inventory import inventory_code  # importato da LIBRARY

    table = out / "L1_dev_canonical.canonical-kind_guards-on_params-off.csv"
    with table.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["dataset"] for r in rows] == ["humanevalplus", "mbppplus"]
    assert "code" not in rows[0] and rows[0]["loc_bench"] == "4"
    expected = inventory_code(CODE_A, channels="extended")
    assert int(rows[0]["n_sites_total"]) == expected["n_sites_total"]
    assert int(rows[0]["loc"]) == expected["loc"]

    text = summary.read_text(encoding="utf-8")
    assert "Inventory L1_dev_canonical\n" in text
    assert "Inventory L1_dev_canonical [dataset=mbppplus]" in text
    assert "loc bands: loc 1-5, loc 6-10, loc 11-20, loc 21-40, loc 41+\n" in text
    assert "Inventory L1_dev_canonical [loc 1-5]" in text
    assert "Inventory L1_dev_canonical [n_sites_total 1-7]" in text
    assert "rows outside loc bands (loc_bench = 0): 0" in text
    groups = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["groups"]
    assert groups["L1_dev_canonical"]["strata"]["loc 1-5"]["n_samples"] == 2
    assert groups["L1_dev_bad"]["outside_site_bands"] == 0  # non analizzato: non conta
    assert "parse_errors=1" in text  # il campione non analizzabile è contato a parte
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["options"] == {
        "canonical": "kind",
        "guards": True,
        "include_params": False,
        "module_level": False,
        "channels": "extended",
    }
    assert "options: canonical-kind_guards-on_params-off, channels=extended\n" in text
    assert data["groups"]["L1_dev_canonical"]["summary"]["n_parsed"] == 2

    # Stesso ingresso: nessuna riscrittura; ingresso diverso: rifiuto, file intatti.
    assert inv.main(argv) == 0
    before = table.read_bytes()
    _input(inputs / "L1_dev_canonical.jsonl", [("humanevalplus", CODE_B)])
    assert inv.main(argv) == 1
    assert table.read_bytes() == before


@needs_library
def test_include_params_changes_only_the_names_channel(tmp_path: Path) -> None:
    inputs = tmp_path / "in.jsonl"
    _input(inputs, [("humanevalplus", CODE_B)])
    base = ["--input", str(inputs), "--out-dir", str(tmp_path), "--library", str(LIBRARY)]
    base += ["--channels", "extended"]
    assert inv.main(base) == 0
    assert inv.main([*base, "--include-params"]) == 0

    def read(variant: str) -> dict[str, str]:
        with (tmp_path / f"in.{variant}.csv").open(encoding="utf-8", newline="") as fh:
            return next(csv.DictReader(fh))

    off = read("canonical-kind_guards-on_params-off")
    on = read("canonical-kind_guards-on_params-on")
    assert int(on["n_vars"]) > int(off["n_vars"])
    assert on["n_sites_structural"] == off["n_sites_structural"]


MODULE_CODE = "total = 0\nfor x in range(3):\n    total = total + x\nprint(total)\n"


@needs_library
def test_module_level_and_channels_are_passed_to_the_library(tmp_path: Path) -> None:
    inputs = tmp_path / "in.jsonl"
    _input(inputs, [("humanevalplus", MODULE_CODE), ("humanevalplus", CODE_B)])
    base = ["--input", str(inputs), "--out-dir", str(tmp_path), "--library", str(LIBRARY)]
    assert inv.main([*base, "--channels", "extended"]) == 0
    assert inv.main([*base, "--channels", "extended", "--module-level", "on"]) == 0

    from multimark.inventory import inventory_code  # importato da LIBRARY

    def read(variant: str) -> list[dict[str, str]]:
        with (tmp_path / f"in.{variant}.csv").open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    off = read("canonical-kind_guards-on_params-off")
    on = read("canonical-kind_guards-on_params-off_modlevel-on")
    assert off[0]["n_vars_module"] == "0"
    expected = inventory_code(MODULE_CODE, module_level=True, channels="extended")
    assert int(on[0]["n_vars_module"]) == expected["n_vars_module"] > 0
    assert on[0]["n_sites_structural"] == off[0]["n_sites_structural"]

    # mcss8 legge solo i canali 1-8: stessa tabella della libreria con channels="mcss8".
    out = tmp_path / "m"
    assert inv.main([*base[:2], "--out-dir", str(out), *base[4:], "--channels", "mcss8"]) == 0
    with (out / "in.canonical-kind_guards-on_params-off.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row, code in zip(rows, (MODULE_CODE, CODE_B), strict=True):
        assert (
            int(row["n_sites_structural"])
            == inventory_code(code, channels="mcss8")["n_sites_structural"]
        )


def test_channels_must_be_explicit(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        inv.build_parser().parse_args(["--input", "x", "--out-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        inv.build_parser().parse_args(
            ["--input", "x", "--out-dir", str(tmp_path), "--channels", "all"]
        )


def test_missing_library_is_reported(tmp_path: Path) -> None:
    with pytest.raises(inv.InventoryError, match="apply_patches.sh multimark"):
        inv.import_library(tmp_path)
