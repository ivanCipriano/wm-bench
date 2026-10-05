"""Test della ResourcePolicy e della coerenza dei profili SLURM (cluster_info §4)."""

from __future__ import annotations

import pytest

from bench.config.resources import ResourceClass, ResourcePolicy, validate_profile
from bench.config.schema import ClusterProfile, ExperimentConfig
from bench.doctor import load_cluster_profiles
from bench.domain.errors import ConfigError
from tests.conftest import REPO_ROOT

PROFILES = load_cluster_profiles(REPO_ROOT / "configs" / "cluster")


def _policy(
    cfg: ExperimentConfig, profiles: dict[str, ClusterProfile] | None = None
) -> ResourcePolicy:
    return ResourcePolicy(cfg.resources, profiles or PROFILES, cfg.slurm)


@pytest.mark.parametrize(
    ("stage", "method", "attack", "expected"),
    [
        ("generate_baseline", None, None, ResourceClass.GPU_NVIDIA),
        ("imperceptibility", None, None, ResourceClass.GPU_NVIDIA),
        ("watermark", "stone", None, ResourceClass.GPU_NVIDIA),
        ("watermark", "acw", None, ResourceClass.CPU),
        ("detect", "sweet", None, ResourceClass.GPU_NVIDIA),
        # STONE: la green list dipende dal generatore casuale CUDA (audit di STONE §2).
        ("detect", "stone", None, ResourceClass.GPU_NVIDIA),
        ("attack", None, "T1.4", ResourceClass.CPU),
        ("attack", None, "T2.2", ResourceClass.GPU_NVIDIA),
        ("attack", None, "T3.1", ResourceClass.GPU_NVIDIA),
        ("attack", None, "T4.1", ResourceClass.CPU),
        ("execute", None, None, ResourceClass.CPU),
        ("metrics", None, None, ResourceClass.CPU),
    ],
)
def test_resource_for(
    cfg: ExperimentConfig,
    stage: str,
    method: str | None,
    attack: str | None,
    expected: ResourceClass,
) -> None:
    assert _policy(cfg).resource_for(stage, method, attack) is expected


def test_method_required_for_method_dependent_stage(cfg: ExperimentConfig) -> None:
    with pytest.raises(ConfigError, match="needs a method"):
        _policy(cfg).resource_for("detect")
    with pytest.raises(ConfigError, match="no resource rule"):
        _policy(cfg).resource_for("detect", "unknown")


def test_real_profiles_have_no_errors(cfg: ExperimentConfig) -> None:
    for profile in PROFILES.values():
        assert [i for i in validate_profile(profile, cfg.slurm) if i.severity == "error"] == []


def test_policy_picks_nvidia_gpu_and_cpu_defq(cfg: ExperimentConfig) -> None:
    policy = _policy(cfg)
    gpu = policy.profile_for(ResourceClass.GPU_NVIDIA)
    cpu = policy.profile_for(ResourceClass.CPU)
    assert gpu.partition == "gpuq" and gpu.requests_gpu
    assert cpu.partition == "defq" and not cpu.requests_gpu


def _variant(name: str, **changes: object) -> ClusterProfile:
    return PROFILES[name].model_copy(update=changes)


def _errors(cfg: ExperimentConfig, profile: ClusterProfile) -> list[str]:
    return [i.message for i in validate_profile(profile, cfg.slurm) if i.severity == "error"]


def test_gpu_on_defq_rejected(cfg: ExperimentConfig) -> None:
    bad = _variant("slurm_cpu", gres="gpu:1")
    assert any("torch is CUDA-only" in m for m in _errors(cfg, bad))
    policy = _policy(cfg, {**PROFILES, "slurm_gpu": bad.model_copy(update={"name": "slurm_gpu"})})
    with pytest.raises(ConfigError, match="rejected"):
        policy.profile_for(ResourceClass.GPU_NVIDIA)


def test_wrong_account_and_qos_rejected(cfg: ExperimentConfig) -> None:
    msgs = _errors(cfg, _variant("slurm_gpu", account="usershpc", qos="normal"))
    assert any("account 'usershpc'" in m for m in msgs)
    assert any("qos 'normal'" in m for m in msgs)


def test_unusable_partition_rejected(cfg: ExperimentConfig) -> None:
    msgs = _errors(cfg, _variant("slurm_cpu", partition="fatq", qos=None))
    assert any("not usable" in m for m in msgs)
    msgs = _errors(cfg, _variant("slurm_cpu", partition="thinq"))
    assert any("unknown partition" in m for m in msgs)


def test_timeout_limits(cfg: ExperimentConfig) -> None:
    assert any("partition limit" in m for m in _errors(cfg, _variant("slurm_gpu", timeout_min=420)))
    warnings = [
        i
        for i in validate_profile(_variant("slurm_gpu", timeout_min=400), cfg.slurm)
        if i.severity == "warning"
    ]
    assert any("80%" in i.message for i in warnings)


def test_gpu_profile_without_gpu_rejected_by_policy(cfg: ExperimentConfig) -> None:
    profiles = {**PROFILES, "slurm_gpu": _variant("slurm_gpu", gres=None)}
    with pytest.raises(ConfigError, match="requests no GPU"):
        _policy(cfg, profiles).profile_for(ResourceClass.GPU_NVIDIA)
