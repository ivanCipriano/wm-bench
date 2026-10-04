"""Sandbox reale sul cluster (marker ``apptainer``; ADR-001, cluster_info §5).

Da lanciare su un nodo di calcolo ``defq`` (``scripts/check_sandbox.sh``) dopo
``scripts/build_sandbox.sh`` e ``module load apptainer/apptainer.module``. Verifica:
rete assente, isolamento del filesystem, compilatori dell'immagine e soluzioni canoniche di
alcuni problemi HumanEvalPack eseguite con l'executor vero.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.models import Problem
from bench.execution.executors import HumanEvalPackExecutor, SampleToRun
from bench.execution.sandbox import ApptainerSandbox
from bench.pipeline.stages.generate_baseline import problems_ref
from bench.store.artifact_store import ArtifactStore
from tests.integration.conftest import require

pytestmark = pytest.mark.apptainer


@pytest.fixture(scope="module")
def box(data_cfg: ExperimentConfig) -> ApptainerSandbox:
    if shutil.which("apptainer") is None:
        if os.environ.get("WMB_REQUIRE_DATA") == "1":
            pytest.fail("apptainer not in PATH: module load apptainer/apptainer.module")
        pytest.skip("apptainer not in PATH")
    require(data_cfg.execution.image_sif, "sandbox image")
    sandbox = ApptainerSandbox(data_cfg.execution)
    sandbox.check()
    return sandbox


@pytest.fixture
def workdir(box: ApptainerSandbox, data_cfg: ExperimentConfig) -> Iterator[Path]:
    path = box.workdir_root(data_cfg.paths.tmp) / "test_sandbox" / uuid.uuid4().hex[:8]
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _python(box: ApptainerSandbox, workdir: Path, code: str) -> str:
    result = box.run(["python3", "-c", code], workdir, 60)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_network_is_disabled(box: ApptainerSandbox, workdir: Path) -> None:
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('pypi.org', 443), 5); print('network')\n"
        "except OSError as e:\n"
        "    print('no-network')\n"
    )
    assert _python(box, workdir, code) == "no-network"


def test_filesystem_is_isolated(
    box: ApptainerSandbox, workdir: Path, data_cfg: ExperimentConfig
) -> None:
    code = (
        "import os\n"
        f"print(os.path.exists({str(data_cfg.paths.artifacts)!r}), os.path.exists('/work'),"
        " os.environ.get('HOME'), os.environ.get('SLURM_JOB_ID'))\n"
    )
    out = _python(box, workdir, code)
    assert out.startswith("False True")  # artefatti invisibili, solo /work montata
    assert out.endswith("None")  # ambiente ripulito


def test_image_versions(box: ApptainerSandbox, workdir: Path) -> None:
    out = _python(box, workdir, "print(open('/opt/wmb/versions.json').read())")
    versions = json.loads(out)
    assert versions["evalplus"] == "0.3.1"
    assert versions["python"].startswith("3.11")
    for tool in ("gcc", "javac", "node", "js-md5"):
        assert versions[tool]


def test_timeout_kills_the_invocation(box: ApptainerSandbox, workdir: Path) -> None:
    result = box.run(["python3", "-c", "import time; time.sleep(60)"], workdir, 2)
    assert result.timed_out
    assert result.duration_s < 30  # ucciso dal timeout interno, non dal tetto esterno


def test_invocations_never_hang(box: ApptainerSandbox, workdir: Path) -> None:
    # Su beegfs alcune invocazioni si bloccavano (diagnosi del 4 ottobre 2026, ADR-001).
    durations = []
    for _ in range(20):
        result = box.run(["python3", "-c", "print('ok')"], workdir, 30)
        assert result.returncode == 0 and result.stdout.strip() == "ok", result
        durations.append(result.duration_s)
    print(f"exec durations: max {max(durations):.2f}s, mean {sum(durations) / 20:.2f}s")
    assert max(durations) < 10


@pytest.mark.parametrize("language", [Language.CPP, Language.JAVA, Language.JAVASCRIPT])
def test_humanevalpack_canonicals(
    box: ApptainerSandbox, data_cfg: ExperimentConfig, language: Language
) -> None:
    store = ArtifactStore(data_cfg.paths.artifacts)
    ref = problems_ref("L1", language)
    require(store.manifest_of(ref), "prepare_data artifacts")
    table = store.read_table(ref)
    # 0 e 1: casi semplici; 22 (Boost in C++) e 162 (OpenSSL in C++, js-md5 in JavaScript).
    keys = ["humaneval/0", "humaneval/1", "humaneval/22", "humaneval/162"]
    problems = [
        Problem.model_validate(r)
        for r in table[table["problem_key"].isin(keys)].to_dict(orient="records")
    ]
    assert len(problems) == len(keys)
    root = box.workdir_root(data_cfg.paths.tmp) / "test_sandbox"
    root.mkdir(parents=True, exist_ok=True)
    executor = HumanEvalPackExecutor(data_cfg, box, language, root)
    for problem in problems:
        canonical = SampleToRun("canon", problem.problem_key, None, executor.canonical(problem))
        wrong = SampleToRun(
            "wrong", problem.problem_key, 0, problem.prompt_text
        )  # firma senza corpo
        records = executor.run_problem(problem, [canonical, wrong])
        assert records[0].status == "PASSED", (problem.problem_key, records[0].stderr_tail)
        assert records[1].status != "PASSED", problem.problem_key
        assert records[0].sandbox_image_hash == box.image_hash


def test_evalplus_known_cases(box: ApptainerSandbox, data_cfg: ExperimentConfig) -> None:
    """Casi di ADR-007: find_zero (bug di EvalPlus 0.3.1), MBPP/255 (memoria), timeout per test."""
    from bench.execution.executors import EvalPlusExecutor
    from bench.pipeline.stages.evalplus_groundtruth import groundtruth_ref, load_groundtruth

    store = ArtifactStore(data_cfg.paths.artifacts)
    require(
        store.manifest_of(groundtruth_ref()), "EvalPlus ground truth (run evalplus_groundtruth)"
    )
    table = store.read_table(problems_ref("L1", Language.PYTHON)).set_index("problem_key")
    gt = load_groundtruth(store.read_table(groundtruth_ref()))
    root = box.workdir_root(data_cfg.paths.tmp) / "test_sandbox"
    root.mkdir(parents=True, exist_ok=True)
    executor = EvalPlusExecutor(data_cfg, box, Language.PYTHON, root, gt)

    def problem(key: str) -> Problem:
        return Problem.model_validate({"problem_key": key, **table.loc[key].to_dict()})

    for key in ("humaneval/32", "humaneval/139", "mbpp/255"):
        p = problem(key)
        record = executor.run_problem(p, [SampleToRun("c", key, 0, executor.canonical(p))])[0]
        assert record.status == "PASSED", (key, record.stderr_tail)
    p32 = problem("humaneval/32")
    wrong = p32.prompt_text + "    return 12345.0\n"
    record = executor.run_problem(p32, [SampleToRun("w", p32.problem_key, 0, wrong)])[0]
    assert record.status == "FAILED", record.stderr_tail
    p0 = problem("humaneval/0")
    slow = p0.prompt_text + "    import time\n    time.sleep(3)\n    return False\n"
    record = executor.run_problem(p0, [SampleToRun("s", p0.problem_key, 0, slow)])[0]
    assert record.status == "TIMEOUT", record.stderr_tail
    assert "time limit" in (record.stderr_tail or "")
