"""Interfaccia dei loader e utilità comuni di lettura (SPEC §7.4).

I loader leggono i file locali della §8 di cluster_info, controllano schema e conteggi e
restituiscono ``Problem`` e ``CodeSample`` con ``split=None``: la divisione la assegna
``ProblemSplitter`` (I4).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

from bench.config.schema import DatasetSpec
from bench.domain.enums import Language, Level, Source
from bench.domain.errors import DataError
from bench.domain.ids import sample_id
from bench.domain.models import CodeSample, Problem
from bench.lang.parsers import ParserService, default_parsers


def normalize_newlines(code: str) -> str:
    """Fine riga Unix: alcuni dataset (es. MBPP) contengono ``\\r\\n`` (ADR-004)."""
    return code.replace("\r\n", "\n").replace("\r", "\n")


def read_jsonl(path: Path, required: Sequence[str]) -> list[dict[str, Any]]:
    """Legge un JSONL e controlla che ogni riga abbia i campi richiesti.

    Raises:
        DataError: se il file manca o una riga non ha un campo richiesto.
    """
    if not path.is_file():
        raise DataError(f"file not found: {path}")
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = [k for k in required if k not in row]
            if missing:
                raise DataError(f"{path}:{number}: missing fields {missing}; found {sorted(row)}")
            rows.append(row)
    return rows


def read_parquet(path: Path, required: Sequence[str]) -> pd.DataFrame:
    """Legge solo le colonne richieste di un Parquet, controllandone la presenza.

    Raises:
        DataError: se il file manca o una colonna richiesta non c'è.
    """
    if not path.is_file():
        raise DataError(f"file not found: {path}")
    import pyarrow.parquet as pq

    columns = pq.ParquetFile(path).schema_arrow.names
    missing = [c for c in required if c not in columns]
    if missing:
        raise DataError(f"{path}: missing columns {missing}; found {columns}")
    return pd.read_parquet(path, columns=list(required))


def check_rows(spec: DatasetSpec, role: str, actual: int) -> None:
    """Confronta le righe lette con quelle attese dalla configurazione (se dichiarate).

    Raises:
        DataError: se il numero di righe è diverso.
    """
    expected = spec.expected_rows.get(role)
    if expected is not None and actual != expected:
        raise DataError(f"{spec.name}/{role}: {actual} rows, expected {expected}")


def parse_task_number(task_id: str, prefix: str) -> int:
    """Numero da un ``task_id`` della forma ``"<prefix>/<n>"``.

    Raises:
        DataError: se il prefisso o il numero non sono quelli attesi.
    """
    head, _, tail = str(task_id).partition("/")
    if head != prefix or not tail.isdigit():
        raise DataError(f"unexpected task_id {task_id!r} (expected '{prefix}/<n>')")
    return int(tail)


def human_sample(
    *,
    problem_key: str,
    dataset: str,
    language: Language,
    level: Level,
    code: str,
    contamination_risk: bool,
) -> CodeSample:
    """Negativo umano con ``split`` ancora da assegnare."""
    return CodeSample(
        sample_id=sample_id(source=Source.HUMAN, problem_key=problem_key, language=language),
        problem_key=problem_key,
        dataset=dataset,
        language=language,
        level=level,
        split=None,
        source=Source.HUMAN,
        model_id=None,
        method=None,
        config_hash=None,
        key_id=None,
        sample_index=None,
        seed=None,
        raw_output=None,
        code=normalize_newlines(code),
        extraction_ok=True,
        embed_status=None,
        expected_message=None,
        parent_id=None,
        attack_id=None,
        attack_params_hash=None,
        attack_status=None,
        contamination_risk=contamination_risk,
    )


def unique_keys(items: Iterable[Problem | CodeSample], what: str) -> None:
    """Controlla che i ``problem_key`` siano unici.

    Raises:
        DataError: se una chiave compare più volte.
    """
    seen: set[str] = set()
    for item in items:
        if item.problem_key in seen:
            raise DataError(f"duplicate problem_key {item.problem_key} in {what}")
        seen.add(item.problem_key)


class DatasetLoader(ABC):
    """Loader di un dataset (Strategy).

    Args:
        spec: configurazione del dataset (file per ruolo, righe attese).
        parsers: servizio di parsing per il conteggio delle righe.
    """

    name: ClassVar[str]
    levels: ClassVar[frozenset[Level]]
    languages: ClassVar[frozenset[Language]]
    contamination_risk: ClassVar[bool]

    def __init__(self, spec: DatasetSpec, parsers: ParserService | None = None) -> None:
        self.spec = spec
        self.parsers = parsers or default_parsers()

    def _check_language(self, language: Language) -> Language:
        lang = Language(language)
        if lang not in self.languages:
            raise DataError(
                f"{self.name}: language {lang} not supported ({sorted(self.languages)})"
            )
        return lang

    @abstractmethod
    def load_problems(self, language: Language) -> list[Problem]:
        """Problemi del dataset in un linguaggio (``split=None``)."""

    @abstractmethod
    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        """Negativi umani nativi del dataset in un linguaggio (``split=None``)."""
