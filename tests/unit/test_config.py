"""Test della configurazione reale (configs/) e dell'ExperimentBuilder."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from bench.config.builder import ExperimentBuilder, compose_config, load_experiment
from bench.config.schema import ExperimentConfig
from bench.doctor import load_cluster_profiles
from bench.domain.enums import KNOWN_METHODS
from bench.domain.errors import ConfigError
from tests.conftest import REPO_ROOT, rebuild


def test_real_config_is_valid(cfg: ExperimentConfig, local_root: Path) -> None:
    assert cfg.global_seed == 20261001
    assert set(cfg.methods) == KNOWN_METHODS
    assert set(cfg.methods_catalog) == KNOWN_METHODS
    assert set(cfg.envs) == {"bench-core", "sweet", "acw", "stone", "promptmark", "mcgmark"}
    assert len(cfg.models_catalog) == 5
    assert cfg.decoding["level1"].max_new_tokens == 512
    assert cfg.decoding["level234"].max_new_tokens == 1024
    assert cfg.decoding["level1"].temperature == 0.2
    assert cfg.decoding["level1"].top_p == 0.95
    assert cfg.decoding["level1"].n == 6
    assert cfg.paths.artifacts == local_root / "artifacts"
    assert cfg.cluster.name == "local"


def test_cluster_paths_follow_cluster_info() -> None:
    cfg = load_experiment(["stage=selftest"])
    wmb = PurePosixPath("/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench")
    assert PurePosixPath(cfg.paths.artifacts.as_posix()) == wmb / "artifacts"
    assert PurePosixPath(cfg.paths.tmp.as_posix()) == wmb / "tmp"
    qwen = cfg.models_catalog["qwen25_coder_7b"]
    assert qwen.path.as_posix() == (
        "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/hf_cache/hub/"
        "models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242"
    )
    assert qwen.tokenizer_path == qwen.path
    for method in cfg.methods_catalog.values():
        assert method.env == method.name
        assert method.worker_timeout_s < 7 * 3600


def test_every_method_points_to_an_existing_submodule(cfg: ExperimentConfig) -> None:
    gitmodules = (REPO_ROOT / ".gitmodules").read_text(encoding="utf-8")
    for method in cfg.methods_catalog.values():
        assert f"path = {method.submodule}" in gitmodules
        assert method.patched_dir == f"build/patched/{method.name}"


def test_global_seed_is_locked(local_root: Path) -> None:
    with pytest.raises(ConfigError, match="locked"):
        load_experiment(["paths=local", "stage=selftest", "global_seed=1"])


def test_unknown_method_rejected(local_root: Path) -> None:
    with pytest.raises(ConfigError, match="unknown methods"):
        load_experiment(["paths=local", "stage=selftest", "methods=[sweet,foo]"])


def test_unknown_model_rejected(cfg: ExperimentConfig) -> None:
    with pytest.raises(ConfigError, match="models without"):
        rebuild(cfg, models=["gpt5"])


def test_missing_stage_rejected(local_root: Path) -> None:
    with pytest.raises(ConfigError, match="cannot resolve"):
        ExperimentBuilder.from_hydra(compose_config(["paths=local"]))


def test_model_path_must_be_the_snapshot(cfg: ExperimentConfig) -> None:
    data = cfg.model_dump(mode="json")
    data["models_catalog"]["qwen25_coder_7b"]["path"] = "/models/qwen"
    with pytest.raises(ConfigError, match="snapshot"):
        ExperimentBuilder.from_dict(data).build()


def test_fluent_builder_and_immutability(cfg: ExperimentConfig, tmp_path: Path) -> None:
    built = (
        ExperimentBuilder.from_dict(cfg.model_dump(mode="json"))
        .with_stage("selftest")
        .with_methods(["sweet"])
        .with_models(["qwen25_coder_7b"])
        .with_paths(artifacts=tmp_path / "a")
        .build()
    )
    assert built.methods == ["sweet"]
    assert built.paths.artifacts == tmp_path / "a"
    with pytest.raises(ValidationError):
        built.stage = "other"  # type: ignore[misc]


def test_error_message_lists_all_problems(cfg: ExperimentConfig) -> None:
    data = cfg.model_dump(mode="json")
    data["detection"]["target_fpr"] = 2
    data["log_level"] = "LOUD"
    with pytest.raises(ConfigError) as info:
        ExperimentBuilder.from_dict(data).build()
    assert "detection.target_fpr" in str(info.value)
    assert "log_level" in str(info.value)


def test_cluster_profiles_load() -> None:
    profiles = load_cluster_profiles(REPO_ROOT / "configs" / "cluster")
    assert set(profiles) == {"local", "slurm_gpu", "slurm_gpu_h100", "slurm_cpu"}
    assert profiles["slurm_cpu"].gres is None
    assert profiles["slurm_cpu"].partition == "defq"
    assert profiles["slurm_gpu"].partition == "gpuq"
