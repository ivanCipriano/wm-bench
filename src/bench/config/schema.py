"""Schema Pydantic della configurazione (immutabile).

Rispecchia i file di ``configs/``; la validazione incrociata (metodi, ambienti, modelli,
seme globale) avviene in ``ExperimentConfig``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

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
    """Dataset locale (cluster_info §8): file e cartelle per ruolo, righe attese per ruolo."""

    name: str
    version: str | None = None
    files: dict[str, Path] = Field(default_factory=dict)
    dirs: dict[str, Path] = Field(default_factory=dict)
    expected_rows: dict[str, int] = Field(default_factory=dict)

    def file(self, role: str) -> Path:
        """File con un dato ruolo (es. ``"java"``, ``"test"``, ``"java/validation"``).

        Raises:
            KeyError: se il ruolo non è configurato.
        """
        if role not in self.files:
            raise KeyError(f"dataset {self.name}: no file for role '{role}' ({sorted(self.files)})")
        return self.files[role]


class SplitFamily(_Frozen):
    """Famiglia di problemi con prompt: quanti in sviluppo su quanti totali (SPEC §10.4)."""

    dev: int = Field(ge=0)
    total: int = Field(gt=0)

    @model_validator(mode="after")
    def _dev_le_total(self) -> SplitFamily:
        if self.dev > self.total:
            raise ValueError(f"dev {self.dev} > total {self.total}")
        return self


class SplitConfig(_Frozen):
    """Divisione per problema (``configs/split/default.yaml``)."""

    families: dict[str, SplitFamily]
    mbpp_extra_assignment: Literal["proportional", "dev", "test"]
    mbpp_extra_reference: str = "mbpp"


_SUM_TOLERANCE = 1e-9


class NegativesConfig(_Frozen):
    """Negativi umani e integrazione (``configs/negatives/default.yaml``)."""

    min_dev_negatives: int = Field(gt=0)
    l1_test_total: int = Field(gt=0)
    n_bins: int = Field(default=10, gt=0)
    sources: dict[Language, dict[Literal["dev", "test"], str]]
    thestack_partition: dict[str, float]

    @model_validator(mode="after")
    def _partition(self) -> NegativesConfig:
        total = sum(self.thestack_partition.values())
        if abs(total - 1.0) > _SUM_TOLERANCE or any(
            v <= 0 for v in self.thestack_partition.values()
        ):
            raise ValueError(f"thestack_partition must be positive and sum to 1, got {total}")
        missing = {"l1_test", "dev"} - set(self.thestack_partition)
        if missing:
            raise ValueError(f"thestack_partition misses {sorted(missing)}")
        return self


class MethodConfig(_Frozen):
    """Configurazione statica di un metodo (``configs/method/*.yaml``)."""

    name: str
    env: str
    family: MethodFamily
    submodule: str
    patched_dir: str
    worker_timeout_s: int = Field(gt=0)
    # Dall'audit (SPEC §9.0): sottocartella della copia patchata da mettere nel PYTHONPATH,
    # linguaggi supportati (gli altri sono NOT_APPLICABLE) e configurazione di default
    # (nomi del protocollo) usata fino all'HPO.
    patched_subdir: str = ""
    supported_languages: list[Language] | None = None
    default_hparams: dict[str, Any] = Field(default_factory=dict)


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
    # Comandi eseguiti all'avvio di ogni job SLURM del profilo (es. ``module load``).
    setup: list[str] = Field(default_factory=list)

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


class PromptConfig(_Frozen):
    """Prompt della generazione (``configs/prompt/default.yaml``, ADR-006)."""

    system_prompt_file: Path
    system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_dir: Path


class EvalPlusConfig(_Frozen):
    """Parametri di EvalPlus (valori di default di EvalPlus 0.3.1)."""

    min_time_limit: float = Field(gt=0)
    gt_time_limit_factor: float = Field(gt=0)


class ExecutionConfig(_Frozen):
    """Esecuzione dei test nella sandbox (``configs/execution/default.yaml``, SPEC §11, ADR-001).

    ``timeouts_s`` limita l'esecuzione di un campione, ``compile_timeout_s`` la sua
    compilazione; ``mem_mb`` è il limite di memoria virtuale per campione (``None`` = nessun
    limite per processo: JVM e V8 riservano spazio di indirizzi ben oltre l'uso reale).
    """

    sandbox: Literal["apptainer", "local"]
    image_dir: Path
    image_sif: Path
    network_none: bool = True
    node_local: bool = True  # immagine e cartelle di lavoro sul disco locale del nodo
    apptainer_module: str
    sources: list[Literal["canonical", "llm_baseline", "llm_watermarked", "baseline_twin"]]
    timeouts_s: dict[str, float]
    compile_timeout_s: float = Field(gt=0)
    mem_mb: dict[str, int | None]
    groundtruth_timeout_s: float = Field(gt=0)
    max_workers: int | None = Field(default=None, gt=0)
    canonical_repeats: int = Field(default=1, gt=0)
    stderr_tail_chars: int = Field(gt=0)
    evalplus: EvalPlusConfig

    @model_validator(mode="after")
    def _languages(self) -> ExecutionConfig:
        expected = {str(x) for x in Language}
        for name, table in (("timeouts_s", self.timeouts_s), ("mem_mb", self.mem_mb)):
            missing = sorted(expected - set(table))
            if missing:
                raise ValueError(f"execution.{name} is missing languages {missing}")
        return self


class DetectionConfig(_Frozen):
    """Calibrazione delle soglie (SPEC §13.2)."""

    target_fpr: float = Field(gt=0, lt=1)
    min_negatives: int = Field(gt=0)
    n_bootstrap: int = Field(default=1000, gt=0)  # ricampionamenti (SPEC §13.6)
    # Metriche sul test con soglie provvisorie: vietate, salvo lo smoke test (decisione
    # dell'utente del 9 ottobre 2026).
    allow_provisional_test: bool = False


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
    split: SplitConfig
    negatives: NegativesConfig
    prompt: PromptConfig
    execution: ExecutionConfig

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
