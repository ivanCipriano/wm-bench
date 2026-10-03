"""Test di bench submit: piano dei job, parametri SLURM, corpo del job (ADR-006)."""

from __future__ import annotations

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.doctor import load_cluster_profiles
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
    params = executor_parameters(cfg, PROFILES["slurm_gpu"], "generate_baseline", 16)
    assert params["slurm_partition"] == "gpuq"
    assert params["slurm_account"] == "did_tesi_nlp_330"
    assert params["slurm_qos"] == "did_tesi_nlp_330_gpuq_qos"
    assert params["slurm_gres"] == "gpu:1"
    assert params["timeout_min"] == 330
    assert params["slurm_array_parallelism"] == 16
    assert "export HF_HUB_OFFLINE=1" in params["slurm_setup"]
    assert "export TRANSFORMERS_OFFLINE=1" in params["slurm_setup"]
    cpu = executor_parameters(cfg, PROFILES["slurm_cpu"], "prepare_data", 1)
    assert cpu["slurm_partition"] == "defq" and "slurm_gres" not in cpu


def test_job_environment(cfg: ExperimentConfig) -> None:
    env = job_environment(cfg)
    assert env["HF_HUB_CACHE"] == str(cfg.paths.hf_hub_cache)
    assert env["HF_DATASETS_OFFLINE"] == "1" and env["PYTHONHASHSEED"] == "0"


def test_dry_run_submits_nothing(cfg: ExperimentConfig) -> None:
    c = rebuild(cfg, levels=["L1"], splits=["dev"], languages=["python"])
    jobs, params = submit(c, "generate_baseline", PROFILES, dry_run=True)
    assert [j.job_id for j in jobs] == ["dry-run", "dry-run"]
    assert params[0]["cells"] == [
        "model_id=qwen25_coder_7b__language=python__level=L1__split=dev",
        "model_id=deepseek_coder_6p7b__language=python__level=L1__split=dev",
    ]
    assert not (c.paths.artifacts / "_slurm").exists()


def test_job_body_runs_one_cell(cfg: ExperimentConfig) -> None:
    summary = run_cell(cfg.model_dump(mode="json"), "selftest", {})
    assert "RAN      selftest [all]" in summary
