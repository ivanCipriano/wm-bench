"""HumanEval+ (EvalPlus v0.1.10): 164 problemi Python di L1 (cluster_info §8.1)."""

from __future__ import annotations

from typing import Any, ClassVar

from bench.data.loaders.base import (
    DatasetLoader,
    check_rows,
    human_sample,
    parse_task_number,
    read_jsonl,
)
from bench.domain.enums import Language, Level
from bench.domain.ids import problem_key
from bench.domain.models import CodeSample, Problem
from bench.lang.line_counter import count_code_lines, prompt_line_count
from bench.registry import LOADERS

FIELDS = ("task_id", "prompt", "entry_point", "canonical_solution")


@LOADERS.register("humanevalplus")
class HumanEvalPlusLoader(DatasetLoader):
    """Legge ``HumanEvalPlus-v0.1.10.jsonl`` direttamente (mai dalla cache di EvalPlus)."""

    name: ClassVar[str] = "humanevalplus"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L1})
    languages: ClassVar[frozenset[Language]] = frozenset({Language.PYTHON})
    contamination_risk: ClassVar[bool] = False

    def _rows(self) -> list[dict[str, Any]]:
        rows = read_jsonl(self.spec.file("data"), FIELDS)
        check_rows(self.spec, "data", len(rows))
        return rows

    def load_problems(self, language: Language) -> list[Problem]:
        lang = self._check_language(language)
        problems = []
        for row in self._rows():
            number = parse_task_number(row["task_id"], "HumanEval")
            prompt, solution = row["prompt"], row["canonical_solution"]
            problems.append(
                Problem(
                    problem_key=problem_key("humaneval", number),
                    dataset=self.name,
                    level=Level.L1,
                    language=lang,
                    split=None,
                    prompt_text=prompt,
                    entry_point=row["entry_point"],
                    canonical_solution=solution,
                    test_ref=row["task_id"],
                    contamination_risk=self.contamination_risk,
                    # Solo la parte da generare: la soluzione analizzata dopo il prompt.
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
                problem_key=problem_key(
                    "humaneval", parse_task_number(row["task_id"], "HumanEval")
                ),
                dataset=self.name,
                language=lang,
                level=Level.L1,
                code=row["prompt"] + row["canonical_solution"],
                contamination_risk=self.contamination_risk,
            )
            for row in self._rows()
        ]
