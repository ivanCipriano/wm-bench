"""Enumerazioni del dominio (SPEC §5.2)."""

from __future__ import annotations

from enum import StrEnum


class Language(StrEnum):
    """Linguaggi di programmazione valutati."""

    PYTHON = "python"
    JAVA = "java"
    CPP = "cpp"
    JAVASCRIPT = "javascript"


class Level(StrEnum):
    """Livelli di difficoltà del protocollo."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class Split(StrEnum):
    """Parte di sviluppo o di test (I4)."""

    DEV = "dev"
    TEST = "test"


class Source(StrEnum):
    """Origine di un campione di codice."""

    HUMAN = "human"
    LLM_BASELINE = "llm_baseline"
    LLM_WATERMARKED = "llm_watermarked"
    ATTACKED = "attacked"


class MethodFamily(StrEnum):
    """Famiglia del metodo di watermarking."""

    LOGIT = "logit"
    PROMPT = "prompt"
    POST_HOC = "post_hoc"


class ExecStatus(StrEnum):
    """Esito dell'esecuzione dei test su un campione."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    NO_TESTS = "NO_TESTS"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    SANDBOX_ERROR = "SANDBOX_ERROR"


class AttackStatus(StrEnum):
    """Esito dell'applicazione di un attacco."""

    APPLIED = "APPLIED"
    UNCHANGED = "UNCHANGED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    TOOL_ERROR = "TOOL_ERROR"


# I cinque metodi del benchmark (SPEC §1, D10).
KNOWN_METHODS: frozenset[str] = frozenset({"sweet", "acw", "stone", "promptmark", "mcgmark"})

# Seme globale fissato dall'utente il 1° ottobre 2026 (cluster_info §10): non modificabile.
LOCKED_GLOBAL_SEED = 20261001
