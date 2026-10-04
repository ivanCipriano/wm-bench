"""Fasi ``evalplus_groundtruth`` ed ``execute`` con una sandbox finta (nessun container).

La sandbox finta legge ``job.json`` come farebbe il runner e scrive ``result.json``:
i campioni che contengono ``FAIL`` falliscono, gli altri passano. Così si verificano
pianificazione delle celle, I1, ripresa, manifest e costruzione dei job senza eseguire codice.
"""

from __future__ import annotations

import json
import pickle
import shutil
import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest

import bench.pipeline.stages.generate_baseline as gb
from bench.config.schema import ExperimentConfig
from bench.domain.enums import ExecStatus
from bench.execution import harness
from bench.execution import sandbox as sb
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner
from bench.pipeline.stages.evalplus_groundtruth import groundtruth_ref
from bench.pipeline.stages.execute import ExecuteStage, execution_ref
from tests.conftest import rebuild
from tests.datafix import HE_TOTAL, MBPP_TOTAL, _jsonl, build_tiny_datasets, tiny_config
from tests.unit.test_generate_baseline import MODEL, FakeBackend

LOCK = threading.Lock()


def _fake_status(program: str, alone: bool) -> str:
    """FAIL: risposta sbagliata; SLOW: TIMEOUT in compagnia, e da solo PASSED solo con RECOVER."""
    if "FAIL" in program:
        return "FAILED"
    if "SLOW" in program:
        return "PASSED" if alone and "RECOVER" in program else "TIMEOUT"
    return "PASSED"


class FakeSandbox(sb.Sandbox):
    """Sandbox finta: registra i job e risponde come il runner."""

    jobs: ClassVar[list[dict[str, Any]]] = []
    files: ClassVar[list[dict[str, bytes]]] = []
    fail_after: ClassVar[int | None] = None
    broken: ClassVar[bool] = False

    @property
    def image_hash(self) -> str:
        return "f" * 64

    def runner_command(self, workdir: Path) -> list[str]:
        return ["runner", str(workdir / "job.json")]

    def work_path(self, workdir: Path, name: str) -> str:
        return str(workdir / name)

    def run(
        self, cmd: list[str], workdir: Path, timeout_s: float, mem_mb: int | None = None
    ) -> sb.SandboxResult:
        job = json.loads((workdir / "job.json").read_text(encoding="utf-8"))
        if job["mode"] == "samples":
            files = {p.name: p.read_bytes() for p in workdir.iterdir() if p.name != "job.json"}
            with LOCK:  # la fase esegue i problemi in parallelo su più thread
                if (
                    FakeSandbox.fail_after is not None
                    and len(FakeSandbox.jobs) >= FakeSandbox.fail_after
                ):
                    raise RuntimeError("simulated SLURM timeout")
                FakeSandbox.jobs.append(job)
                FakeSandbox.files.append(files)
            if FakeSandbox.broken:
                return sb.SandboxResult(255, "", "FATAL: container failed", 0.1, False)
            results = [
                {
                    "id": s["id"],
                    "status": _fake_status(s["program"], alone=len(job["samples"]) == 1),
                    "n_tests": None,
                    "n_passed": None,
                    "duration_s": 0.01,
                    "stderr_tail": None,
                }
                for s in job["samples"]
            ]
            out: dict[str, Any] = {"versions": {"image": "fake"}, "results": results}
        else:  # groundtruth: un pickle per problema dei file JSONL della fixture
            entries = []
            (workdir / job["out_dir"]).mkdir()
            for name, path in job["datasets"].items():
                assert Path(path).parent == workdir  # copiati nella cartella di lavoro
                for row in (
                    json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()
                ):
                    fname = f"{job['out_dir']}/{row['task_id'].replace('/', '_')}.pkl"
                    (workdir / fname).write_bytes(pickle.dumps({"problem": row, "oracle": {}}))
                    entries.append(
                        {
                            "dataset": name,
                            "task_id": row["task_id"],
                            "file": fname,
                            "canonical_program": row["prompt"] + row["canonical_solution"],
                            "n_base": 1,
                            "n_plus": 2,
                        }
                    )
            out = {
                "versions": {"image": "fake"},
                "problems": entries,
                "dataset_hash": {"humaneval": "h"},
            }
        (workdir / "result.json").write_text(json.dumps(out), encoding="utf-8")
        return sb.SandboxResult(0, "", "", 0.1, False)


@pytest.fixture
def facade(cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch) -> BenchmarkFacade:
    build_tiny_datasets(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    BenchmarkFacade(tcfg).run_stage("prepare_data")
    tcfg = rebuild(tcfg, models=[MODEL], languages=["python", "java"], splits=["dev", "test"])
    FakeBackend.calls = []
    FakeBackend.fail_after = None
    monkeypatch.setattr(gb, "BACKEND_FACTORY", FakeBackend)
    BenchmarkFacade(tcfg).run_stage("generate_baseline")
    FakeSandbox.jobs, FakeSandbox.files = [], []
    FakeSandbox.fail_after, FakeSandbox.broken = None, False
    monkeypatch.setattr(sb, "make_sandbox", lambda config: FakeSandbox())
    return BenchmarkFacade(tcfg)


def test_cells_of_execute(cfg: ExperimentConfig) -> None:
    cells = CellPlanner(rebuild(cfg, levels=["L1"], splits=["dev", "test"])).cells_for(ExecuteStage)
    canonical = [c for c in cells if c.source == "canonical"]
    baseline = [c for c in cells if c.source == "llm_baseline"]
    assert len(canonical) == 4 and all(c.model_id is None and c.split is None for c in canonical)
    assert len(baseline) == 2 * 4 * 2
    assert execution_ref(canonical[0]).path.as_posix() == "execution/canonical/L1_python.parquet"
    assert execution_ref(baseline[0]).path.as_posix() == (
        "execution/llm_baseline/qwen25_coder_7b/L1_python_dev.parquet"
    )


def test_python_needs_the_groundtruth_first(facade: BenchmarkFacade) -> None:
    from bench.domain.errors import MissingInputError

    with pytest.raises(MissingInputError, match="evalplus_groundtruth"):
        facade.run_stage("execute", cells=[Cell(source="canonical", level="L1", language="python")])


def test_groundtruth_and_canonical_python(facade: BenchmarkFacade) -> None:
    facade.run_stage("evalplus_groundtruth")
    gt = facade.store.read_table(groundtruth_ref())
    assert len(gt) == HE_TOTAL + MBPP_TOTAL
    manifest = facade.store.read_manifest(groundtruth_ref())
    assert manifest is not None and manifest.sandbox_image_hash == "f" * 64
    assert manifest.extra["problems"] == {"humaneval": HE_TOTAL, "mbpp": MBPP_TOTAL}

    facade.run_stage("execute", cells=[Cell(source="canonical", level="L1", language="python")])
    ref = execution_ref(Cell(source="canonical", level="L1", language="python"))
    table = facade.store.read_table(ref)
    repeats = facade.cfg.execution.canonical_repeats
    assert repeats == 3
    assert len(table) == (HE_TOTAL + MBPP_TOTAL) * repeats  # la canonica 3 volte per problema
    assert set(table["status"]) == {"PASSED"}
    assert sorted(set(table["sample_index"])) == list(range(repeats))
    assert table["sample_id"].is_unique
    assert set(table["first_attempt_status"]) == {"PASSED"} and table["retry_status"].isna().all()
    assert set(table["executor"]) == {"evalplus"}
    # Il job di EvalPlus porta il pickle della ground truth e il nome del dataset di EvalPlus.
    datasets = {job["evalplus"]["dataset"] for job in FakeSandbox.jobs}
    assert datasets == {"humaneval", "mbpp"}
    payload = pickle.loads(FakeSandbox.files[0]["payload.pkl"])
    assert payload["problem"]["task_id"] in set(gt["task_id"])
    first = FakeSandbox.jobs[0]
    assert (
        first["samples"][0]["program"]
        == gt.set_index("task_id").loc[payload["problem"]["task_id"], "canonical_program"]
    )
    assert first["timeout_s"] == facade.cfg.execution.timeouts_s["python"]


def test_baseline_java_rows_statuses_and_manifest(facade: BenchmarkFacade) -> None:
    cell = Cell(source="llm_baseline", model_id=MODEL, level="L1", language="java", split="dev")
    facade.run_stage("execute", cells=[cell])
    samples = facade.store.read_table(gb.baseline_ref(MODEL, "L1", "java", "dev"))
    table = facade.store.read_table(execution_ref(cell))
    assert len(table) == len(samples)  # I1
    assert list(table["sample_id"]) == list(
        samples.sort_values(["problem_key", "sample_index"])["sample_id"]
    )
    failed_extraction = set(samples.loc[~samples["extraction_ok"], "sample_id"])
    by_id = table.set_index("sample_id")["status"]
    assert all(by_id[s] == ExecStatus.EXTRACTION_FAILED.value for s in failed_extraction)
    # Solo i campioni estratti vanno nella sandbox, un job per problema.
    assert len(FakeSandbox.jobs) == samples["problem_key"].nunique()
    assert all(len(j["samples"]) == facade.cfg.decoding["level1"].n - 1 for j in FakeSandbox.jobs)
    # Il programma è quello dell'harness: codice preparato + "\n" + test del dataset.
    tests = {r["task_id"]: r["test"] for r in _jsonl("humanevalpack.jsonl") if r["dir"] == "java"}
    job = FakeSandbox.jobs[0]
    assert job["rule"] == "java" and job["compile_cmd"] == ["javac", "Main.java"]
    assert any(job["samples"][0]["program"].endswith("\n" + t) for t in tests.values())
    manifest = facade.store.read_manifest(execution_ref(cell))
    assert manifest is not None
    assert manifest.n_rows_expected == manifest.n_rows_out == len(samples)
    assert manifest.sandbox_image_hash == "f" * 64
    assert manifest.extra["status_counts"]["EXTRACTION_FAILED"] == len(failed_extraction)


def test_resume_after_interruption(facade: BenchmarkFacade) -> None:
    cell = Cell(source="llm_baseline", model_id=MODEL, level="L1", language="java", split="test")
    FakeSandbox.fail_after = 2
    with pytest.raises(RuntimeError, match="simulated"):
        facade.run_stage("execute", cells=[cell])
    first = len(FakeSandbox.jobs)
    assert first == 2
    FakeSandbox.fail_after = None
    facade.run_stage("execute", cells=[cell])
    n_problems = facade.store.read_table(execution_ref(cell))["problem_key"].nunique()
    assert len(FakeSandbox.jobs) == n_problems  # i problemi già fatti non si rieseguono
    ref = execution_ref(cell)
    assert not (
        facade.store.root / ref.path.parent / "_partial" / f"{ref.path.stem}.jsonl"
    ).exists()


def test_sandbox_failure_is_a_status_not_an_exception(facade: BenchmarkFacade) -> None:
    FakeSandbox.broken = True
    cell = Cell(source="canonical", level="L1", language="java")
    facade.run_stage("execute", cells=[cell])
    table = facade.store.read_table(execution_ref(cell))
    assert set(table["status"]) == {ExecStatus.SANDBOX_ERROR.value}
    assert table["stderr_tail"].str.contains("container failed").all()


@pytest.mark.skipif(shutil.which("javac") is None, reason="javac not available")
def test_canonical_java_really_passes(
    facade: BenchmarkFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sb, "make_sandbox", lambda config: sb.LocalSandbox())
    cell = Cell(source="canonical", level="L1", language="java")
    facade.run_stage("execute", cells=[cell])
    table = facade.store.read_table(execution_ref(cell))
    assert len(table) == HE_TOTAL * facade.cfg.execution.canonical_repeats
    assert set(table["status"]) == {"PASSED"}, table[["problem_key", "status", "stderr_tail"]]
    assert set(table["sandbox_image_hash"]) == {"local"}


def test_check_program_used_for_canonical(facade: BenchmarkFacade) -> None:
    cell = Cell(source="canonical", level="L1", language="java")
    facade.run_stage("execute", cells=[cell])
    rows = {r["task_id"]: r for r in _jsonl("humanevalpack.jsonl") if r["dir"] == "java"}
    programs = {j["samples"][0]["program"] for j in FakeSandbox.jobs}
    from bench.domain.enums import Language

    expected = {
        harness.check_program(r["prompt"] + r["canonical_solution"], r["test"], Language.JAVA)
        for r in rows.values()
    }
    assert programs == expected


def test_timeouts_are_retried_alone_once(
    facade: BenchmarkFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench.execution.executors import HumanEvalPackExecutor

    markers = {"humaneval/0": "SLOW RECOVER", "humaneval/1": "SLOW", "humaneval/2": "FAIL"}
    original = HumanEvalPackExecutor.canonical
    monkeypatch.setattr(
        HumanEvalPackExecutor,
        "canonical",
        lambda self, p: original(self, p) + "// " + markers.get(p.problem_key, ""),
    )
    cell = Cell(source="canonical", level="L1", language="java")
    facade.run_stage("execute", cells=[cell])
    table = facade.store.read_table(execution_ref(cell)).set_index(["problem_key", "sample_index"])
    repeats = facade.cfg.execution.canonical_repeats
    for rep in range(repeats):
        recovered = table.loc[("humaneval/0", rep)]
        assert recovered["first_attempt_status"] == "TIMEOUT"
        assert recovered["retry_status"] == recovered["status"] == "PASSED"
        still = table.loc[("humaneval/1", rep)]
        assert (
            still["first_attempt_status"] == still["retry_status"] == still["status"] == "TIMEOUT"
        )
        wrong = table.loc[("humaneval/2", rep)]  # risposte sbagliate: niente ripetizione
        assert wrong["status"] == "FAILED" and wrong["retry_status"] is None
    # Le ripetizioni girano una per invocazione (da sole), solo per i TIMEOUT.
    alone = [j for j in FakeSandbox.jobs if len(j["samples"]) == 1]
    assert len(alone) == 2 * repeats
    manifest = facade.store.read_manifest(execution_ref(cell))
    assert manifest is not None
    assert manifest.extra["retried"] == 2 * repeats
    assert manifest.extra["recovered_timeout_to_passed"] == repeats
    assert manifest.extra["first_attempt_status_counts"]["TIMEOUT"] == 2 * repeats


def test_workers_follow_the_job_cpus(
    cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench.pipeline.stages.execute import job_cpus, max_workers

    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "16")
    assert job_cpus(cfg) == 16
    assert max_workers(cfg, "java") == 16
    assert max_workers(cfg, "python") == 8  # metà per Python (ADR-007)
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "1")
    assert max_workers(cfg, "python") == 1
    monkeypatch.delenv("SLURM_CPUS_PER_TASK")
    import os

    if hasattr(os, "sched_getaffinity"):
        assert job_cpus(cfg) == len(os.sched_getaffinity(0))
