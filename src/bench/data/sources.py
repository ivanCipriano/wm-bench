"""Sorgenti di funzioni umane per l'integrazione dei negativi (SPEC §10.4, ADR-004).

- CodeSearchNet (Python, Java, JS): una riga = una funzione (``whole_func_string``).
- The Stack (C++): una riga = un file; le funzioni si estraggono con tree-sitter. I
  repository sono assegnati a usi disgiunti con una partizione fissa (cluster_info §8.5).

Si tengono solo funzioni che si analizzano senza errori; tutte portano
``contamination_risk=True`` (I6).
"""

from __future__ import annotations

import bisect
import itertools
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pyarrow.parquet as pq
import xxhash

from bench.config.schema import DatasetSpec
from bench.data.loaders.base import check_rows, normalize_newlines, read_parquet
from bench.domain.enums import Language
from bench.domain.errors import DataError
from bench.domain.ids import derive_seed
from bench.lang.functions import extract_functions, standalone_function_loc
from bench.lang.parsers import ParserService, default_parsers

logger = logging.getLogger(__name__)

_SEED_RANGE = 2**31 - 1


@dataclass(frozen=True)
class Candidate:
    """Funzione candidata per l'integrazione dei negativi."""

    key: str
    code: str
    loc: int
    repo: str


@dataclass
class SourceStats:
    """Diagnostica della lettura di una sorgente (finisce nel manifest)."""

    rows: int = 0
    candidates: int = 0
    dropped_parse_error: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Statistiche come dizionario JSON."""
        return {
            "rows": self.rows,
            "candidates": self.candidates,
            "dropped_parse_error": self.dropped_parse_error,
            **self.extra,
        }


CSN_COLUMNS = (
    "repository_name",
    "func_path_in_repository",
    "whole_func_string",
    "func_code_string",
    "func_code_url",
)


class CodeSearchNetSource:
    """Funzioni di CodeSearchNet (``$DATA/codesearchnet/<lang>/<split>-...parquet``).

    Args:
        spec: configurazione del dataset ``codesearchnet``.
        parsers: servizio di parsing.
    """

    name = "codesearchnet"

    def __init__(self, spec: DatasetSpec, parsers: ParserService | None = None) -> None:
        self.spec = spec
        self.parsers = parsers or default_parsers()

    def repos(self, language: Language, split: str) -> set[str]:
        """Repository presenti in uno split (per la disgiunzione tra usi)."""
        role = f"{Language(language)}/{split}"
        frame = read_parquet(self.spec.file(role), ("repository_name",))
        check_rows(self.spec, role, len(frame))
        return {str(repo) for repo in frame["repository_name"].tolist()}

    def candidates(self, language: Language, split: str) -> tuple[list[Candidate], SourceStats]:
        """Funzioni candidate di uno split.

        Si usa ``whole_func_string`` (la funzione come scritta nel repository); il manifest
        registra la frazione di righe in cui coincide con ``func_code_string``.
        """
        lang = Language(language)
        role = f"{lang}/{split}"
        frame = read_parquet(self.spec.file(role), CSN_COLUMNS)
        check_rows(self.spec, role, len(frame))
        stats = SourceStats(rows=len(frame))
        same = (
            (frame["whole_func_string"] == frame["func_code_string"]).mean() if len(frame) else 1.0
        )
        stats.extra["whole_equals_code_fraction"] = round(float(same), 6)
        out: list[Candidate] = []
        for row in frame.itertuples(index=False):
            code = normalize_newlines(str(row.whole_func_string))
            loc = standalone_function_loc(code, lang, self.parsers)
            if loc is None:
                stats.dropped_parse_error += 1
                continue
            digest = xxhash.xxh3_64_hexdigest(str(row.func_code_url).encode("utf-8"))
            out.append(Candidate(f"csn/{lang}/{digest}", code, loc, str(row.repository_name)))
        stats.candidates = len(out)
        return out, stats


STACK_COLUMNS = ("content", "max_stars_repo_name", "max_stars_repo_path", "hexsha")


class TheStackCppSource:
    """Funzioni C++ estratte dai file di The Stack (``$DATA/thestack/data/cpp/``).

    Args:
        spec: configurazione del dataset ``thestack_cpp``.
        partition: frazioni per uso (es. ``{"l3_test": 0.4, "l1_test": 0.2, ...}``).
        seed: seme globale (la partizione deriva da ``derive_seed``).
        parsers: servizio di parsing.
    """

    name = "thestack_cpp"

    def __init__(
        self,
        spec: DatasetSpec,
        partition: dict[str, float],
        seed: int,
        parsers: ParserService | None = None,
    ) -> None:
        self.spec = spec
        self.seed = seed
        self.parsers = parsers or default_parsers()
        self._buckets = list(partition)
        self._cuts = list(itertools.accumulate(partition.values()))

    def bucket(self, repo: str) -> str:
        """Uso a cui è assegnato un repository (partizione fissa e deterministica)."""
        u = derive_seed(self.seed, "sample", self.name, "repo", repo) / _SEED_RANGE
        index = min(bisect.bisect_right(self._cuts, u), len(self._buckets) - 1)
        return self._buckets[index]

    def candidates(self, buckets: Iterable[str]) -> tuple[dict[str, list[Candidate]], SourceStats]:
        """Funzioni candidate per gli usi richiesti, in una sola passata sui file.

        I file senza repository usano ``hexsha`` come repository.
        """
        wanted = set(buckets)
        unknown = wanted - set(self._buckets)
        if unknown:
            raise DataError(f"thestack_cpp: unknown partition buckets {sorted(unknown)}")
        out: dict[str, list[Candidate]] = {b: [] for b in sorted(wanted)}
        stats = SourceStats()
        files_per_bucket: dict[str, int] = {b: 0 for b in sorted(wanted)}
        for role in sorted(self.spec.files):
            path = self.spec.file(role)
            if not path.is_file():
                raise DataError(f"file not found: {path}")
            table = pq.read_table(path, columns=list(STACK_COLUMNS))
            check_rows(self.spec, role, table.num_rows)
            stats.rows += table.num_rows
            for row in table.to_pylist():
                repo = str(row["max_stars_repo_name"] or row["hexsha"])
                bucket = self.bucket(repo)
                if bucket not in wanted:
                    continue
                files_per_bucket[bucket] += 1
                source = normalize_newlines(str(row["content"] or ""))
                for fn in extract_functions(source, Language.CPP, self.parsers):
                    key = f"thestack/{row['hexsha']}:{fn.start_line}"
                    out[bucket].append(Candidate(key, fn.code, fn.loc, repo))
        stats.candidates = sum(len(v) for v in out.values())
        stats.extra["files_per_bucket"] = files_per_bucket
        stats.extra["candidates_per_bucket"] = {b: len(v) for b, v in out.items()}
        return out, stats
