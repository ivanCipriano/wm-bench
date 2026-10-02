"""Scelta delle risorse e coerenza dei profili SLURM (SPEC §16.3, cluster_info §4).

``ResourcePolicy`` decide se una ``(fase, metodo, attacco)`` va su CPU o GPU NVIDIA e quale
profilo usare; ``validate_profile`` rifiuta i profili incoerenti (GPU su partizioni AMD,
account o QoS sbagliati, timeout oltre il limite della partizione).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from bench.config.schema import ClusterProfile, ResourceRules, SlurmConfig
from bench.domain.errors import ConfigError


class ResourceClass(StrEnum):
    """Classe di risorse richiesta da un lavoro."""

    CPU = "CPU"
    GPU_NVIDIA = "GPU_NVIDIA"


@dataclass(frozen=True)
class ProfileIssue:
    """Problema di un profilo: ``error`` blocca, ``warning`` segnala."""

    severity: Literal["error", "warning"]
    message: str


class ResourcePolicy:
    """Politica delle risorse.

    Args:
        rules: regole da configurazione.
        profiles: profili disponibili per nome.
        slurm: partizioni e account.
    """

    def __init__(
        self, rules: ResourceRules, profiles: dict[str, ClusterProfile], slurm: SlurmConfig
    ) -> None:
        self.rules = rules
        self.profiles = profiles
        self.slurm = slurm

    def resource_for(
        self, stage: str, method: str | None = None, attack: str | None = None
    ) -> ResourceClass:
        """Classe di risorse per una fase (eventualmente per metodo o attacco)."""
        if stage in self.rules.gpu_stages:
            return ResourceClass.GPU_NVIDIA
        by_method = self.rules.gpu_methods_by_stage.get(stage)
        if by_method is not None:
            if method is None:
                raise ConfigError(f"stage '{stage}' needs a method to choose resources")
            if method not in by_method:
                raise ConfigError(f"no resource rule for stage '{stage}' and method '{method}'")
            return ResourceClass.GPU_NVIDIA if by_method[method] else ResourceClass.CPU
        if stage == "attack" and attack is not None:
            group = attack.split(".", 1)[0]
            if group in self.rules.gpu_attack_groups:
                return ResourceClass.GPU_NVIDIA
        return ResourceClass.CPU

    def profile_for(self, resource: ResourceClass) -> ClusterProfile:
        """Profilo SLURM associato a una classe di risorse, dopo averne verificato la coerenza.

        Raises:
            ConfigError: se il profilo manca o ha errori bloccanti.
        """
        name = self.rules.profiles[resource.value]
        if name not in self.profiles:
            raise ConfigError(f"profile '{name}' for {resource} not found")
        profile = self.profiles[name]
        errors = [i.message for i in validate_profile(profile, self.slurm) if i.severity == "error"]
        if resource is ResourceClass.GPU_NVIDIA and not profile.requests_gpu:
            errors.append(f"profile '{name}' is used for GPU jobs but requests no GPU")
        if errors:
            raise ConfigError(f"profile '{name}' rejected: " + "; ".join(errors))
        return profile


def validate_profile(profile: ClusterProfile, slurm: SlurmConfig) -> list[ProfileIssue]:
    """Controlla un profilo rispetto alle partizioni e all'account del progetto."""
    if profile.kind == "local":
        return []
    issues: list[ProfileIssue] = []

    def error(msg: str) -> None:
        issues.append(ProfileIssue("error", f"{profile.name}: {msg}"))

    def warning(msg: str) -> None:
        issues.append(ProfileIssue("warning", f"{profile.name}: {msg}"))

    if profile.account != slurm.account:
        error(f"account '{profile.account}' != '{slurm.account}'")
    if profile.partition is None or profile.partition not in slurm.partitions:
        error(f"unknown partition '{profile.partition}'")
        return issues
    part = slurm.partitions[profile.partition]
    if not part.usable:
        error(f"partition '{profile.partition}' is not usable ({part.note or 'no QoS'})")
    if part.qos is None or profile.qos != part.qos:
        error(f"qos '{profile.qos}' != '{part.qos}' for partition '{profile.partition}'")
    if profile.requests_gpu and part.gpu != "nvidia":
        error(f"GPU requested on partition '{profile.partition}' ({part.gpu}): torch is CUDA-only")
    if not profile.requests_gpu and profile.gres is not None:
        warning(f"non-GPU gres '{profile.gres}'")
    if part.gpu == "nvidia" and not profile.requests_gpu:
        warning(f"partition '{profile.partition}' has NVIDIA GPUs but the profile requests none")
    if profile.timeout_min is None:
        error("timeout_min is required for SLURM profiles")
    elif profile.timeout_min >= part.max_time_min:
        error(f"timeout_min {profile.timeout_min} >= partition limit {part.max_time_min}")
    elif profile.timeout_min > slurm.timeout_target_fraction * part.max_time_min:
        warning(
            f"timeout_min {profile.timeout_min} above {slurm.timeout_target_fraction:.0%} "
            f"of the limit {part.max_time_min}"
        )
    return issues
