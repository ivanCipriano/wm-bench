"""Project CodeNet: i 250 problemi del Livello 2 nei 4 linguaggi (cluster_info §8.6, ADR-011).

I problemi e le sottomissioni vengono da ``configs/dataset/codenet_selected.yaml``, generato da
``scripts/codenet_select.py`` (criteri in D28). Per ogni problema e linguaggio:

- il prompt è la descrizione convertita da HTML a testo (``bench.data.codenet.describe``);
- la soluzione canonica è la prima sottomissione scelta; ``loc_to_generate`` sono le sue righe
  di codice (SPEC §10.3, tutto il programma: su CodeNet si genera il programma intero);
- i negativi umani sono le altre 5 sottomissioni (utenti distinti), con ``sample_index`` 0-4.

``problem_key`` = ``codenet/<pid>``: lo stesso problema ha la stessa parte nei 4 linguaggi (I4).
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

import yaml

from bench.data.codenet import LANG_NAMES, describe
from bench.data.loaders.base import DatasetLoader, human_sample, normalize_newlines, unique_keys
from bench.domain.enums import Language, Level
from bench.domain.errors import DataError
from bench.domain.models import CodeSample, Problem
from bench.lang.line_counter import count_code_lines
from bench.registry import LOADERS

N_SELECTED = 250  # default di ``expected_rows.problems`` (SPEC §10.2)


@LOADERS.register("codenet")
class CodeNetLoader(DatasetLoader):
    """Legge la selezione versionata, le descrizioni e le sottomissioni estratte."""

    name: ClassVar[str] = "codenet"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L2})
    languages: ClassVar[frozenset[Language]] = frozenset(Language)
    contamination_risk: ClassVar[bool] = False

    @cached_property
    def selection(self) -> dict[str, Any]:
        """Contenuto di ``codenet_selected.yaml``.

        Raises:
            DataError: se il file manca o non contiene ``expected_rows.problems`` (250) problemi
                distinti.
        """
        path = self.spec.file("selected")
        if not path.is_file():
            raise DataError(f"{path} not found: run scripts/codenet_select.py (Milestone 9)")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        keys = [p["problem_key"] for p in data.get("problems", [])]
        expected = self.spec.expected_rows.get("problems", N_SELECTED)
        if len(keys) != expected or len(set(keys)) != expected:
            raise DataError(f"{path}: expected {expected} distinct problems, found {len(keys)}")
        return dict(data)

    def funnel(self) -> list[dict[str, Any]]:
        """Problemi rimasti dopo ogni criterio della selezione (per il manifest)."""
        return list(self.selection.get("funnel", []))

    @staticmethod
    def pid(problem_key: str) -> str:
        return problem_key.split("/", 1)[1]

    def _code(self, pid: str, lang: Language, sub: dict[str, Any]) -> str:
        path = Path(self.spec.dirs["data"]) / pid / LANG_NAMES[lang]
        path = path / f"{sub['submission_id']}.{sub['ext']}"
        if not path.is_file():
            raise DataError(f"CodeNet submission not extracted: {path}")
        return normalize_newlines(path.read_text(encoding="utf-8", errors="replace"))

    def _description(self, pid: str) -> str:
        path = Path(self.spec.dirs["problem_descriptions"]) / f"{pid}.html"
        if not path.is_file():
            raise DataError(f"CodeNet description not found: {path}")
        return describe(path.read_text(encoding="utf-8", errors="replace")).text

    def load_problems(self, language: Language) -> list[Problem]:
        lang = self._check_language(language)
        problems = []
        for entry in self.selection["problems"]:
            key = str(entry["problem_key"])
            pid = self.pid(key)
            canonical = self._code(pid, lang, entry["submissions"][str(lang)]["canonical"])
            problems.append(
                Problem(
                    problem_key=key,
                    dataset=self.name,
                    level=Level.L2,
                    language=lang,
                    split=None,
                    prompt_text=self._description(pid),
                    entry_point=None,
                    canonical_solution=canonical,
                    test_ref=pid,
                    contamination_risk=self.contamination_risk,
                    loc_to_generate=count_code_lines(canonical, lang, parsers=self.parsers),
                )
            )
        unique_keys(problems, f"codenet/{lang}")
        return problems

    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        lang = self._check_language(language)
        samples = []
        for entry in self.selection["problems"]:
            key = str(entry["problem_key"])
            pid = self.pid(key)
            for index, sub in enumerate(entry["submissions"][str(lang)]["negatives"]):
                samples.append(
                    human_sample(
                        problem_key=key,
                        dataset=self.name,
                        language=lang,
                        level=Level.L2,
                        code=self._code(pid, lang, sub),
                        contamination_risk=self.contamination_risk,
                        sample_index=index,
                    )
                )
        return samples
