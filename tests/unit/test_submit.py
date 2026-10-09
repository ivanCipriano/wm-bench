"""Test di bench submit: piano dei job, parametri SLURM, corpo del job (ADR-006)."""

from __future__ import annotations

import pytest

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.doctor import load_cluster_profiles
from bench.domain.errors import ConfigError
from bench.pipeline.submit import (
    executor_parameters,
    job_environment,
    plan_submission,
    run_cell,
    submit,
)
from tests.conftest import REPO_ROOT, rebuild

PROFILES = load_cluster_profiles(REPO_ROOT / "configs" / "cluster")


def test_generate_baseline_goes_to_gpuq(cfg: ExperimentConfig) -> None:
    import bench.pipeline.stages  # noqa: F401

    c = rebuild(cfg, levels=["L1"], splits=["dev", "test"])
    groups = plan_submission(c, "generate_baseline", PROFILES)
    assert len(groups) == 1
    group = groups[0]
    assert group.resource is ResourceClass.GPU_NVIDIA
    assert group.profile.partition == "gpuq"
    assert len(group.cells) == 2 * 4 * 2  # modelli x linguaggi x parti


def test_executor_parameters(cfg: ExperimentConfig) -> None:
    params = executor_parameters(cfg, PROFILES["slurm_gpu"], "generate_baseline")
    assert params["slurm_partition"] == "gpuq"
    assert params["slurm_account"] == "did_tesi_nlp_330"
    assert params["slurm_qos"] == "did_tesi_nlp_330_gpuq_qos"
    assert params["slurm_gres"] == "gpu:1"
    assert params["timeout_min"] == 330
    assert "slurm_array_parallelism" not in params  # nessun job array
    assert "slurm_dependency" not in params
    assert "export HF_HUB_OFFLINE=1" in params["slurm_setup"]
    assert "export TRANSFORMERS_OFFLINE=1" in params["slurm_setup"]
    cpu = executor_parameters(cfg, PROFILES["slurm_cpu"], "prepare_data", after="123")
    assert cpu["slurm_partition"] == "defq" and "slurm_gres" not in cpu
    assert cpu["slurm_dependency"] == "afterany:123"


def test_job_environment(cfg: ExperimentConfig) -> None:
    env = job_environment(cfg)
    assert env["HF_HUB_CACHE"] == str(cfg.paths.hf_hub_cache)
    assert env["HF_DATASETS_OFFLINE"] == "1" and env["PYTHONHASHSEED"] == "0"


def test_dry_run_submits_nothing(cfg: ExperimentConfig) -> None:
    c = rebuild(cfg, levels=["L1"], splits=["dev"], languages=["python"])
    jobs = submit(c, "generate_baseline", PROFILES, dry_run=True)
    assert len(jobs) == 1  # un solo job SLURM: celle in sequenza
    assert jobs[0].job_id == "dry-run"
    assert jobs[0].cells == [
        "model_id=qwen25_coder_7b__language=python__level=L1__split=dev",
        "model_id=deepseek_coder_6p7b__language=python__level=L1__split=dev",
    ]
    assert not (c.paths.artifacts / "_slurm").exists()


def test_job_body_runs_one_cell(cfg: ExperimentConfig) -> None:
    summary = run_cell(cfg.model_dump(mode="json"), "selftest", {})
    assert "RAN      selftest [all]" in summary


def test_sequential_job_runs_cells_in_order(cfg: ExperimentConfig) -> None:
    from bench.pipeline.submit import SequentialJob

    out = SequentialJob()(cfg.model_dump(mode="json"), "selftest", [{}, {}])
    assert out.count("selftest [all]") == 2
    assert out.splitlines()[0].startswith("RAN") and "SKIPPED  selftest" in out


def test_sequential_job_continues_after_a_failed_cell(
    cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.pipeline.submit as sub

    done: list[str] = []

    def fake_run_cell(cfg_data: dict, stage: str, cell: dict) -> str:  # type: ignore[type-arg]
        if cell.get("language") == "python":
            raise sub.ConfigError("missing ground truth")
        done.append(cell["language"])
        return f"RAN {cell['language']}"

    monkeypatch.setattr(sub, "run_cell", fake_run_cell)
    cells = [{"language": "python"}, {"language": "java"}, {"language": "cpp"}]
    with pytest.raises(RuntimeError, match="1 of 3 cell") as info:
        sub.SequentialJob()({}, "execute", cells)
    assert done == ["java", "cpp"]  # le celle dopo quella fallita vengono eseguite
    assert "language=python: ConfigError: missing ground truth" in str(info.value)


def test_lanes_are_disjoint_and_balanced() -> None:
    from bench.pipeline.stage import Cell
    from bench.pipeline.submit import split_into_lanes

    cells = [Cell(model_id=m, language=lang) for m in ("a", "b") for lang in ("py", "j", "c", "js")]
    weights = [542 if c.language == "py" else 164 for c in cells]
    lanes = split_into_lanes(cells, weights, 3)
    assert len(lanes) == 3
    flat = [c for lane in lanes for c in lane]
    assert sorted(c.key() for c in flat) == sorted(c.key() for c in cells)  # ogni cella una volta
    loads = [sum(weights[cells.index(c)] for c in lane) for lane in lanes]
    assert max(loads) - min(loads) <= 542
    assert split_into_lanes(cells[:2], weights[:2], 3) == [[cells[0]], [cells[1]]]


def test_dry_run_with_three_jobs(cfg: ExperimentConfig) -> None:
    c = rebuild(cfg, levels=["L1"], splits=["dev", "test"])
    jobs = submit(c, "generate_baseline", PROFILES, dry_run=True, n_jobs=3)
    assert len(jobs) == 3
    keys = [k for j in jobs for k in j.cells]
    assert len(keys) == 16 and len(set(keys)) == 16
    assert all("slurm_dependency" not in j.params for j in jobs)


def test_refuses_when_jobs_are_already_queued(
    cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.pipeline.submit as sub

    rows = [("4242", "i.cipriano1", "RUNNING", "wmb-generate_baseline")]
    monkeypatch.setattr(sub, "queued_jobs", lambda account: rows)
    c = rebuild(cfg, levels=["L1"], splits=["dev"], languages=["python"])
    with pytest.raises(ConfigError, match="already queued or running"):
        sub.submit(c, "generate_baseline", PROFILES)
    # Il vecchio job con tutte le celle blocca anche le quote.
    with pytest.raises(ConfigError, match="4242"):
        sub.submit(c, "generate_baseline", PROFILES, share=(1, 2))


def test_two_people_split_the_cells(cfg: ExperimentConfig) -> None:
    c = rebuild(cfg, levels=["L1"], splits=["dev", "test"])
    first = submit(c, "generate_baseline", PROFILES, dry_run=True, n_jobs=3, share=(1, 2))
    second = submit(c, "generate_baseline", PROFILES, dry_run=True, n_jobs=3, share=(2, 2))
    a = {k for j in first for k in j.cells}
    b = {k for j in second for k in j.cells}
    assert not a & b  # nessuna cella in entrambe le quote
    assert len(a | b) == 16
    assert len(first) == 3 and len(second) == 3
    assert {j.params["name"] for j in first} == {"wmb-generate_baseline-s1of2"}
    assert {j.params["name"] for j in second} == {"wmb-generate_baseline-s2of2"}
    # Deterministico: lo stesso comando dà le stesse celle.
    again = submit(c, "generate_baseline", PROFILES, dry_run=True, n_jobs=3, share=(1, 2))
    assert [j.cells for j in again] == [j.cells for j in first]


def test_conflicts_between_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    import bench.pipeline.submit as sub

    rows = [
        ("1", "s.faraulo", "RUNNING", "wmb-generate_baseline-s2of2"),
        ("2", "x", "PENDING", "wmb-prepare_data"),
        ("3", "x", "PENDING", "other-job"),
    ]
    monkeypatch.setattr(sub, "queued_jobs", lambda account: rows)
    assert sub.conflicting_jobs("generate_baseline", (1, 2), "acc") == []  # quota disgiunta
    assert len(sub.conflicting_jobs("generate_baseline", (2, 2), "acc")) == 1  # stessa quota
    assert len(sub.conflicting_jobs("generate_baseline", (1, 3), "acc")) == 1  # altra divisione
    assert len(sub.conflicting_jobs("generate_baseline", (1, 1), "acc")) == 1  # tutte le celle


def test_jobs_start_with_group_writable_umask(cfg: ExperimentConfig) -> None:
    params = executor_parameters(cfg, PROFILES["slurm_gpu"], "generate_baseline")
    assert params["slurm_setup"][0] == "umask 002"


def test_parse_share() -> None:
    from bench.pipeline.submit import parse_share

    assert parse_share("1/2") == (1, 2)
    for bad in ("0/2", "3/2", "1-2", "a/b"):
        with pytest.raises(ConfigError):
            parse_share(bad)


def test_cpu_jobs_load_apptainer(cfg: ExperimentConfig) -> None:
    params = executor_parameters(cfg, PROFILES["slurm_cpu"], "execute")
    assert params["slurm_setup"][:2] == ["umask 002", "module load apptainer/apptainer.module"]
    assert "slurm_gres" not in params
    gpu = executor_parameters(cfg, PROFILES["slurm_gpu"], "generate_baseline")
    assert not any(line.startswith("module load") for line in gpu["slurm_setup"])


def test_cell_weight_counts_samples(cfg: ExperimentConfig) -> None:
    import pandas as pd

    from bench.pipeline.stage import Cell
    from bench.pipeline.submit import cell_weight

    path = cfg.paths.artifacts / "data" / "problems" / "L1_java.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"split": ["dev"] * 3 + ["test"] * 7}).to_parquet(path)
    n = cfg.decoding["level1"].n
    assert cell_weight(cfg, Cell(model_id="m", level="L1", language="java", split="test")) == 7 * n
    assert cell_weight(cfg, Cell(source="canonical", level="L1", language="java")) == 10
    assert cell_weight(cfg, Cell(source="canonical", level="L1", language="cpp")) == 1  # ignoto


def test_jobs_of_different_methods_do_not_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """``wmb-detect.sweet`` e ``wmb-detect.stone`` lavorano su celle disgiunte."""
    import bench.pipeline.submit as sub

    assert sub.job_name("detect", (1, 1), ["stone", "sweet"]) == "wmb-detect.stone+sweet"
    assert sub.job_name("detect", (2, 2), ["sweet"]) == "wmb-detect.sweet-s2of2"
    rows = [("1", "x", "RUNNING", "wmb-detect.sweet+stone"), ("2", "x", "PENDING", "wmb-execute")]
    monkeypatch.setattr(sub, "queued_jobs", lambda account: rows)
    assert sub.conflicting_jobs("detect", (1, 1), "acc", ["mcgmark"]) == []
    assert len(sub.conflicting_jobs("detect", (1, 1), "acc", ["stone"])) == 1
    assert len(sub.conflicting_jobs("detect", (1, 1), "acc", None)) == 1  # tutti i metodi
    # Un job senza metodi nel nome (vecchio formato) conta come se li avesse tutti.
    assert len(sub.conflicting_jobs("execute", (1, 1), "acc", ["mcgmark"])) == 1


def test_job_name_carries_the_methods_only_for_method_stages(cfg: ExperimentConfig) -> None:
    c = rebuild(cfg, methods=["sweet"])
    detect = submit(c, "detect", PROFILES, dry_run=True)
    assert {j.params["name"] for j in detect} == {"wmb-detect.sweet"}
    baseline = submit(c, "generate_baseline", PROFILES, dry_run=True)
    assert {j.params["name"] for j in baseline} == {"wmb-generate_baseline"}
