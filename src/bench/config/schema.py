"""Schema Pydantic della configurazione (immutabile).

Rispecchia i file di ``configs/``; la validazione incrociata (metodi, ambienti, modelli,
seme globale) avviene in ``ExperimentConfig``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bench.domain.enums import (
    KNOWN_METHODS,
    LOCKED_GLOBAL_SEED,
    Language,
    Level,
    MethodFamily,
    Split,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())


class PathsConfig(_Frozen):
    """Radici dei percorsi (``configs/paths/*.yaml``)."""

    repo: Path
    wmb: Path
    artifacts: Path
    tmp: Path
    datasets: Path
    hf_hub_cache: Path


class EnvSpec(_Frozen):
    """Ambiente Python (``configs/envs/envs.yaml``)."""

    python: Path


class ModelSpec(_Frozen):
    """Modello locale, identificato dalla snapshot della cache HF (cluster_info §7)."""

    model_id: str
    hf_repo: str
    snapshot: str = Field(pattern=r"^[0-9a-f]{40}$")
    path: Path
    tokenizer_path: Path
    dtype: str
    chat_template: str | None
    role: str

    @model_validator(mode="after")
    def _path_is_snapshot(self) -> ModelSpec:
        if self.path.name != self.snapshot:
            raise ValueError(
                f"{self.model_id}: path must end with the snapshot hash {self.snapshot}"
            )
        return self


class DecodingConfig(_Frozen):
    """Parametri di decoding fissi (SPEC §2.1)."""

    temperature: float
    top_p: float
    max_new_tokens: int = Field(gt=0)
    n: int = Field(gt=0)


class DatasetSpec(_Frozen):
    """Dataset locale: per ora solo i percorsi attesi (si estende in M2)."""

    name: str
    version: str | None = None
    files: list[Path] = Field(default_factory=list)
    dirs: list[Path] = Field(default_factory=list)


class MethodConfig(_Frozen):
    """Configurazione statica di un metodo (``configs/method/*.yaml``)."""

    name: str
    env: str
    family: MethodFamily
    submodule: str
    patched_dir: str
    worker_timeout_s: int = Field(gt=0)


class PartitionSpec(_Frozen):
    """Partizione SLURM (cluster_info §4)."""

    gpu: Literal["nvidia", "amd", "none"]
    qos: str | None
    max_time_min: int = Field(gt=0)
    usable: bool
    note: str = ""


class SlurmConfig(_Frozen):
    """Account, partizioni e margine di tempo (``configs/slurm/partitions.yaml``)."""

    account: str
    partitions: dict[str, PartitionSpec]
    timeout_target_fraction: float = Field(gt=0, le=1)


class ClusterProfile(_Frozen):
    """Profilo di esecuzione (``configs/cluster/*.yaml``)."""

    name: str
    kind: Literal["local", "slurm"]
    partition: str | None = None
    account: str | None = None
    qos: str | None = None
    gres: str | None = None
    cpus_per_task: int = Field(gt=0)
    mem_gb: int = Field(gt=0)
    timeout_min: int | None = Field(default=None, gt=0)
    provisional: bool = False

    @property
    def requests_gpu(self) -> bool:
        """``True`` se il profilo chiede GPU tramite ``gres``."""
        return self.gres is not None and "gpu" in self.gres


class ResourceRules(_Frozen):
    """Regole della ``ResourcePolicy`` (``configs/resources/default.yaml``)."""

    gpu_stages: list[str]
    gpu_methods_by_stage: dict[str, dict[str, bool]]
    gpu_attack_groups: list[str]
    profiles: dict[Literal["CPU", "GPU_NVIDIA"], str]


class DetectionConfig(_Frozen):
    """Calibrazione delle soglie (SPEC §13.2)."""

    target_fpr: float = Field(gt=0, lt=1)
    min_negatives: int = Field(gt=0)


class HpoConfig(_Frozen):
    """Selezione degli iperparametri (SPEC §14)."""

    use_selected: bool
    max_pass1_drop_pp: float = Field(ge=0)


class ExperimentConfig(_Frozen):
    """Configurazione completa e validata di un lancio."""

    global_seed: int
    stage: str
    methods: list[str]
    models: list[str]
    languages: list[Language]
    levels: list[Level]
    splits: list[Split]
    attacks: list[str]
    force: bool
    log_level: str = "INFO"
    paths: PathsConfig
    envs: dict[str, EnvSpec]
    cluster: ClusterProfile
    slurm: SlurmConfig
    resources: ResourceRules
    decoding: dict[str, DecodingConfig]
    models_catalog: dict[str, ModelSpec]
    datasets: dict[str, DatasetSpec]
    methods_catalog: dict[str, MethodConfig]
    detection: DetectionConfig
    hpo: HpoConfig

    @field_validator("global_seed")
    @classmethod
    def _locked_seed(cls, value: int) -> int:
        if value != LOCKED_GLOBAL_SEED:
            raise ValueError(
                f"global_seed is locked to {LOCKED_GLOBAL_SEED} (cluster_info §10), got {value}"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _level(cls, value: str) -> str:
        if value.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError(f"invalid log_level {value!r}")
        return value.upper()

    @model_validator(mode="after")
    def _cross_checks(self) -> ExperimentConfig:
        unknown = sorted(set(self.methods) - KNOWN_METHODS)
        if unknown:
            raise ValueError(f"unknown methods {unknown}; known: {sorted(KNOWN_METHODS)}")
        missing = sorted(set(self.methods) - set(self.methods_catalog))
        if missing:
            raise ValueError(f"methods without configs/method/*.yaml: {missing}")
        for name, method in self.methods_catalog.items():
            if method.name != name:
                raise ValueError(f"method config key '{name}' != name '{method.name}'")
            if method.env not in self.envs:
                raise ValueError(f"method '{name}': env '{method.env}' not in envs")
        if "bench-core" not in self.envs:
            raise ValueError("envs must contain 'bench-core'")
        missing_models = sorted(set(self.models) - set(self.models_catalog))
        if missing_models:
            raise ValueError(f"models without configs/model/*.yaml: {missing_models}")
        for key, model in self.models_catalog.items():
            if model.model_id != key:
                raise ValueError(f"model config key '{key}' != model_id '{model.model_id}'")
        return self
