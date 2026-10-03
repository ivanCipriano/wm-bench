"""HumanEvalPack: 164 problemi per Java, C++ e JavaScript di L1 (cluster_info §8.2).

Gli identificativi ``Java/N``, ``CPP/N``, ``JavaScript/N`` diventano ``humaneval/N``: lo
stesso problema ha la stessa divisione in tutti i linguaggi (I4). La versione Python serve
solo al controllo incrociato degli ID con HumanEval+.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from bench.data.loaders.base import (
    DatasetLoader,
    check_rows,
    human_sample,
    parse_task_number,
    read_parquet,
)
from bench.domain.enums import Language, Level
from bench.domain.ids import problem_key
from bench.domain.models import CodeSample, Problem
from bench.lang.line_counter import count_code_lines, prompt_line_count
from bench.registry import LOADERS

# Cartella del file e prefisso dei task_id per linguaggio (JavaScript -> "js").
DIRS: dict[Language, str] = {
    Language.PYTHON: "python",
    Language.JAVA: "java",
    Language.CPP: "cpp",
    Language.JAVASCRIPT: "js",
}
PREFIXES: dict[Language, str] = {
    Language.PYTHON: "Python",
    Language.JAVA: "Java",
    Language.CPP: "CPP",
    Language.JAVASCRIPT: "JavaScript",
}
COLUMNS = ("task_id", "prompt", "canonical_solution", "entry_point")


@LOADERS.register("humanevalpack")
class HumanEvalPackLoader(DatasetLoader):
    """Legge ``humanevalpack/<dir>/test-00000-of-00001.parquet``."""

    name: ClassVar[str] = "humanevalpack"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L1})
    languages: ClassVar[frozenset[Language]] = frozenset(
        {Language.JAVA, Language.CPP, Language.JAVASCRIPT}
    )
    contamination_risk: ClassVar[bool] = False

    def _frame(self, lang: Language) -> pd.DataFrame:
        role = DIRS[lang]
        frame = read_parquet(self.spec.file(role), COLUMNS)
        check_rows(self.spec, role, len(frame))
        return frame

    def _key(self, lang: Language, task_id: str) -> str:
        return problem_key("humaneval", parse_task_number(task_id, PREFIXES[lang]))

    def python_keys(self) -> set[str]:
        """Chiavi della versione Python, per il controllo incrociato con HumanEval+."""
        frame = self._frame(Language.PYTHON)
        return {self._key(Language.PYTHON, t) for t in frame["task_id"]}

    def load_problems(self, language: Language) -> list[Problem]:
        lang = self._check_language(language)
        problems = []
        for row in self._frame(lang).itertuples(index=False):
            prompt, solution = str(row.prompt), str(row.canonical_solution)
            problems.append(
                Problem(
                    problem_key=self._key(lang, str(row.task_id)),
                    dataset=self.name,
                    level=Level.L1,
                    language=lang,
                    split=None,
                    prompt_text=prompt,
                    entry_point=str(row.entry_point),
                    canonical_solution=solution,
                    test_ref=str(row.task_id),
                    contamination_risk=self.contamination_risk,
                    loc_to_generate=count_code_lines(
                        prompt + solution,
                        lang,
                        start_line=prompt_line_count(prompt),
                        parsers=self.parsers,
                    ),
                )
            )
        return problems

    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        lang = self._check_language(language)
        return [
            human_sample(
                problem_key=self._key(lang, str(row.task_id)),
                dataset=self.name,
                language=lang,
                level=Level.L1,
                code=str(row.prompt) + str(row.canonical_solution),
                contamination_risk=self.contamination_risk,
            )
            for row in self._frame(lang).itertuples(index=False)
        ]
