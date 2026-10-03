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

    monkeypatch.setattr(sub, "active_jobs", lambda name: ["4242 RUNNING"])
    c = rebuild(cfg, levels=["L1"], splits=["dev"], languages=["python"])
    with pytest.raises(ConfigError, match="already queued or running"):
        sub.submit(c, "generate_baseline", PROFILES)
