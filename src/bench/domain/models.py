"""Modelli di dominio dell'orchestratore (Pydantic v2, immutabili; SPEC §5.2).

I modelli ``Worker*Model`` rispecchiano le dataclass di ``bench_contracts.schema``
aggiungendo validazione; un test di contratto verifica che i campi coincidano (SPEC §21.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bench_contracts import SCHEMA_VERSION, DetectStatus, EmbedStatus, WorkerOp
from bench_contracts.schema import MAX_ERROR_CHARS
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bench.domain.enums import AttackStatus, ExecStatus, Language, Level, Source, Split


class FrozenModel(BaseModel):
    """Base comune: immutabile e senza campi extra."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())


class Problem(FrozenModel):
    """Problema di un dataset, in un linguaggio."""

    problem_key: str
    dataset: str
    level: Level
    language: Language
    split: Split | None  # None solo prima dell'assegnazione (ProblemSplitter); mai negli artefatti
    prompt_text: str
    entry_point: str | None
    canonical_solution: str | None
    test_ref: str | None
    contamination_risk: bool
    loc_to_generate: int | None


class CodeSample(FrozenModel):
    """Campione di codice (umano, baseline, marcato o attaccato)."""

    sample_id: str
    problem_key: str
    dataset: str
    language: Language
    level: Level
    split: Split | None  # None solo prima dell'assegnazione (ProblemSplitter); mai negli artefatti
    source: Source
    model_id: str | None
    method: str | None
    config_hash: str | None
    key_id: str | None
    sample_index: int | None
    seed: int | None
    raw_output: str | None
    code: str
    extraction_ok: bool
    embed_status: str | None
    expected_message: str | None
    parent_id: str | None
    attack_id: str | None
    attack_params_hash: str | None
    attack_status: AttackStatus | None
    contamination_risk: bool

    @field_validator("embed_status")
    @classmethod
    def _check_embed_status(cls, value: str | None) -> str | None:
        if value is not None and value not in EmbedStatus.ALL:
            raise ValueError(f"invalid embed_status {value!r}")
        return value


class ExecutionRecord(FrozenModel):
    """Esito dell'esecuzione dei test su un campione."""

    sample_id: str
    status: ExecStatus
    n_tests: int | None
    n_passed: int | None
    duration_s: float
    stderr_tail: str | None
    executor: str
    sandbox_image_hash: str
    # Ripetizione dei TIMEOUT (ADR-007): esito del primo tentativo e della ripetizione;
    # ``status`` è quello finale. ``retry_status`` è None se il campione non è stato ripetuto.
    first_attempt_status: ExecStatus | None = None
    retry_status: ExecStatus | None = None


class DetectionRecord(FrozenModel):
    """Esito della rilevazione su un campione."""

    sample_id: str
    method: str
    model_id: str
    config_hash: str
    key_id: str
    status: str
    score: float | None
    native_decision: bool | None
    decoded_message: str | None
    bits_correct: int | None
    extra: dict[str, Any]

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in DetectStatus.ALL:
            raise ValueError(f"invalid detect status {value!r}")
        return value


class ThresholdSet(FrozenModel):
    """Soglia di rilevazione congelata (I3)."""

    method: str
    model_id: str
    language: Language
    config_hash: str
    target_fpr: float
    threshold: float
    achieved_fpr_dev: float
    n_negatives: int
    underpowered: bool
    created_at: datetime
    negatives_ref: str


class MetricValue(FrozenModel):
    """Valore di una metrica con intervallo di confidenza (I7)."""

    name: str
    value: float
    ci_low: float | None
    ci_high: float | None
    ci_method: str
    n: int
    cell: dict[str, str]


# --------------------------------------------------------------------------- contratti worker


class WorkerRequestModel(FrozenModel):
    """Versione validata di ``bench_contracts.WorkerRequest``."""

    schema_version: str
    op: str
    method: str
    model_id: str
    model_path: str
    tokenizer_path: str
    hparams: dict[str, Any]
    key: int
    key_id: str
    decoding: dict[str, Any]
    system_prompt: str
    items_path: str
    output_path: str
    device: str
    log_path: str

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(f"schema_version {value!r} != {SCHEMA_VERSION!r}")
        return value

    @field_validator("op")
    @classmethod
    def _check_op(cls, value: str) -> str:
        if value not in WorkerOp.ALL:
            raise ValueError(f"invalid op {value!r}")
        return value


class WorkerItemModel(FrozenModel):
    """Versione validata di ``bench_contracts.WorkerItem``."""

    item_id: str
    language: Language
    seed: int
    prompt_messages: list[dict[str, str]] | None
    code: str | None
    context_prompt: str | None
    expected_message: str | None
    n: int = Field(ge=1)

    @field_validator("expected_message")
    @classmethod
    def _check_bits(cls, value: str | None) -> str | None:
        if value is not None and set(value) - {"0", "1"}:
            raise ValueError("expected_message must contain only '0' and '1'")
        return value


class WorkerResultModel(FrozenModel):
    """Versione validata di ``bench_contracts.WorkerResult``."""

    item_id: str
    sample_index: int | None
    status: str
    raw_output: str | None
    code: str | None
    score: float | None
    native_decision: bool | None
    decoded_message: str | None
    extra: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=MAX_ERROR_CHARS)
    elapsed_s: float = 0.0

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in EmbedStatus.ALL | DetectStatus.ALL:
            raise ValueError(f"invalid status {value!r}")
        return value
