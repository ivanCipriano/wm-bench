"""Gerarchia delle eccezioni dell'orchestratore (SPEC §17)."""

from __future__ import annotations


class BenchError(Exception):
    """Radice di tutte le eccezioni del framework."""


class ConfigError(BenchError):
    """Configurazione non valida o incoerente."""


class MissingInputError(BenchError):
    """Un input richiesto da una fase non esiste (o non ha un manifest completo).

    Args:
        missing: descrizione dell'artefatto mancante (di norma il percorso).
        producer: nome della fase da lanciare per produrlo, se noto.
    """

    def __init__(self, missing: str, producer: str | None = None) -> None:
        self.missing = missing
        self.producer = producer
        hint = f"; run stage '{producer}' first" if producer else ""
        super().__init__(f"missing input: {missing}{hint}")


class WorkerError(BenchError):
    """Il processo worker di un metodo è fallito in modo non recuperabile."""


class SandboxError(BenchError):
    """Errore della sandbox Apptainer (non del codice eseguito)."""


class ToolError(BenchError):
    """Errore di uno strumento esterno usato dagli attacchi."""


class InvariantViolation(BenchError):
    """Violazione di un invariante del protocollo (SPEC §3.2): errore fatale."""


class ContractVersionError(BenchError):
    """Versione dello schema dei contratti diversa da quella attesa."""
