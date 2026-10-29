"""The Stack C++ nel Livello 3: funzioni del bucket ``l3_test`` (cluster_info §8.5; ADR-016 §2).

Una sola passata sui file dei repository assegnati al bucket ``l3_test`` della partizione fissa
(``configs/negatives/default.yaml``, disgiunta per repository dagli altri usi):

- **prompt candidati**: le funzioni con un commento di documentazione subito sopra, che passa i
  filtri (``bench.data.l3_prompts.cpp_prompts``); prompt = commento + firma;
- **negativi**: tutte le funzioni più esterne senza errori di sintassi (come l'integrazione di L1,
  ``TheStackCppSource``).

``problem_key`` = ``thestack/<hexsha>:<riga>`` (la chiave dei negativi): la funzione di un prompt è
anche un negativo con la stessa chiave. La scelta dei 500 prompt e dei 10.000 negativi la fa
``prepare_data``. Tutto con ``contamination_risk=True`` (I6).
"""

from __future__ import annotations

from collections import Counter
from functools import cached_property
from typing import Any, ClassVar

import pyarrow.parquet as pq

from bench.data.l3_prompts import cpp_prompts
from bench.data.loaders.base import DatasetLoader, check_rows, normalize_newlines
from bench.data.loaders.codesearchnet import human_negative
from bench.data.sources import STACK_COLUMNS, Candidate, TheStackCppSource
from bench.domain.enums import Language, Level
from bench.domain.errors import ConfigError, DataError
from bench.domain.models import CodeSample, Problem
from bench.lang.functions import extract_functions
from bench.registry import LOADERS

BUCKET = "l3_test"


@LOADERS.register("thestack_cpp")
class TheStackCppLoader(DatasetLoader):
    """Prompt e negativi di L3 dai repository del bucket ``l3_test`` di The Stack.

    Args:
        partition: frazioni della partizione per repository (``negatives.thestack_partition``).
        seed: seme globale (la partizione deriva da ``derive_seed``).
    """

    name: ClassVar[str] = "thestack_cpp"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L3})
    languages: ClassVar[frozenset[Language]] = frozenset({Language.CPP})
    contamination_risk: ClassVar[bool] = True

    def __init__(self, spec: Any, partition: dict[str, float] | None = None, seed: int = 0,
                 parsers: Any = None) -> None:  # fmt: skip
        super().__init__(spec, parsers)
        if not partition or BUCKET not in partition:
            raise ConfigError(f"thestack_cpp: the partition has no '{BUCKET}' bucket")
        self.source = TheStackCppSource(spec, partition, seed, self.parsers)

    @cached_property
    def pools(self) -> tuple[list[Problem], list[Candidate], dict[str, Any]]:
        """Prompt candidati, negativi candidati e conteggi, in una sola passata sui file."""
        prompts: list[Problem] = []
        negatives: list[Candidate] = []
        counts: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        files = 0
        for role in sorted(self.spec.files):
            path = self.spec.file(role)
            if not path.is_file():
                raise DataError(f"file not found: {path}")
            table = pq.read_table(path, columns=list(STACK_COLUMNS))
            check_rows(self.spec, role, table.num_rows)
            for row in table.to_pylist():
                repo = str(row["max_stars_repo_name"] or row["hexsha"])
                if self.source.bucket(repo) != BUCKET:
                    continue
                files += 1
                source = normalize_newlines(str(row["content"] or ""))
                for fn in extract_functions(source, Language.CPP, self.parsers):
                    key = f"thestack/{row['hexsha']}:{fn.start_line}"
                    negatives.append(Candidate(key, fn.code, fn.loc, repo))
                found, file_counts = cpp_prompts(source, self.parsers)
                counts.update(file_counts)
                for pf in found:
                    kinds[pf.doc_kind] += 1
                    key = f"thestack/{row['hexsha']}:{pf.start_line}"
                    prompts.append(
                        Problem(
                            problem_key=key,
                            dataset=self.name,
                            level=Level.L3,
                            language=Language.CPP,
                            split=None,
                            prompt_text=pf.prompt,
                            entry_point=pf.entry_point,
                            canonical_solution=pf.code,
                            test_ref=None,
                            contamination_risk=True,
                            loc_to_generate=pf.loc_to_generate,
                        )
                    )
        info = {
            "bucket": BUCKET,
            "files_in_bucket": files,
            "functions": int(counts.pop("functions", 0)),
            "prompt_filters": dict(sorted(counts.items())),
            "doc_kind": dict(sorted(kinds.items())),
            "prompt_candidates": len(prompts),
            "negative_candidates": len(negatives),
        }
        return prompts, negatives, info

    def load_problems(self, language: Language) -> list[Problem]:
        self._check_language(language)
        return self.pools[0]

    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        lang = self._check_language(language)
        return [human_negative(c, lang, self.name) for c in self.pools[1]]
