"""MBPP originale: 974 soluzioni Python, usate solo come negativi umani (cluster_info §8.3, D7)."""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from bench.data.loaders.base import DatasetLoader, check_rows, human_sample, read_parquet
from bench.domain.enums import Language, Level
from bench.domain.errors import DataError
from bench.domain.ids import problem_key
from bench.domain.models import CodeSample, Problem
from bench.registry import LOADERS

ROLES = ("train", "test", "validation", "prompt")
COLUMNS = ("task_id", "code")  # "code" è la soluzione di riferimento (verificato in M2)


@LOADERS.register("mbpp_original")
class MbppOriginalLoader(DatasetLoader):
    """Unione dei quattro file Parquet di ``mbpp/full`` (insieme = 974 problemi)."""

    name: ClassVar[str] = "mbpp_original"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L1})
    languages: ClassVar[frozenset[Language]] = frozenset({Language.PYTHON})
    contamination_risk: ClassVar[bool] = False

    def _frame(self) -> pd.DataFrame:
        frames = []
        for role in ROLES:
            frame = read_parquet(self.spec.file(role), COLUMNS)
            check_rows(self.spec, role, len(frame))
            frames.append(frame)
        data = pd.concat(frames, ignore_index=True)
        check_rows(self.spec, "all", len(data))
        if data["task_id"].duplicated().any():
            dup = sorted(data.loc[data["task_id"].duplicated(), "task_id"].tolist())[:5]
            raise DataError(f"mbpp_original: duplicate task_id {dup}")
        return data.sort_values("task_id", kind="stable").reset_index(drop=True)

    def load_problems(self, language: Language) -> list[Problem]:
        """Nessun problema: le soluzioni MBPP originali non sono mai usate come prompt (D7)."""
        self._check_language(language)
        return []

    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        lang = self._check_language(language)
        frame = self._frame()
        return [
            human_sample(
                problem_key=problem_key("mbpp", int(task_id)),
                dataset=self.name,
                language=lang,
                level=Level.L1,
                code=str(code),
                contamination_risk=self.contamination_risk,
            )
            for task_id, code in zip(frame["task_id"], frame["code"], strict=True)
        ]
