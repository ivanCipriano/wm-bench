"""MBPP+ (EvalPlus v0.2.0): 378 problemi Python di L1 (cluster_info §8.1).

I negativi umani dei problemi MBPP sono le soluzioni originali (loader ``mbpp_original``).
"""

from __future__ import annotations

from typing import Any, ClassVar

from bench.data.loaders.base import DatasetLoader, check_rows, parse_task_number, read_jsonl
from bench.domain.enums import Language, Level
from bench.domain.ids import problem_key
from bench.domain.models import CodeSample, Problem
from bench.lang.line_counter import count_code_lines
from bench.registry import LOADERS

FIELDS = ("task_id", "prompt", "entry_point", "canonical_solution")


@LOADERS.register("mbppplus")
class MbppPlusLoader(DatasetLoader):
    """Legge ``MbppPlus-v0.2.0.jsonl`` direttamente (mai dalla cache di EvalPlus)."""

    name: ClassVar[str] = "mbppplus"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L1})
    languages: ClassVar[frozenset[Language]] = frozenset({Language.PYTHON})
    contamination_risk: ClassVar[bool] = False

    def _rows(self) -> list[dict[str, Any]]:
        rows = read_jsonl(self.spec.file("data"), FIELDS)
        check_rows(self.spec, "data", len(rows))
        return rows

    def load_problems(self, language: Language) -> list[Problem]:
        lang = self._check_language(language)
        return [
            Problem(
                problem_key=problem_key("mbpp", parse_task_number(row["task_id"], "Mbpp")),
                dataset=self.name,
                level=Level.L1,
                language=lang,
                split=None,
                prompt_text=row["prompt"],
                entry_point=row["entry_point"],
                canonical_solution=row["canonical_solution"],
                test_ref=row["task_id"],
                contamination_risk=self.contamination_risk,
                # Il prompt è solo testo: il modello genera l'intera funzione.
                loc_to_generate=count_code_lines(
                    row["canonical_solution"], lang, parsers=self.parsers
                ),
            )
            for row in self._rows()
        ]

    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        """MBPP+ non fornisce negativi propri: vengono da ``mbpp_original`` (SPEC §10.1)."""
        self._check_language(language)
        return []
