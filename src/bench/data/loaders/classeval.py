"""ClassEval: le classi Python del Livello 3 (cluster_info §8.7, SPEC §10.1; ADR-016).

Legge ``third_party/ClassEval/data/ClassEval_data.json`` (100 classi) e toglie le 6 classi di
``configs/dataset/classeval_excluded.yaml`` (oracle di MCGMark, D21; TODO 30): L3 ha 94 classi,
tutte di test. Per ogni classe:

- il prompt è lo **scheletro ufficiale** (``skeleton``: import, descrizione della classe,
  costruttore, firme e docstring dei metodi), come nella generazione olistica di ClassEval
  (decisione dell'utente del 29 ottobre 2026);
- ``entry_point`` è il nome della classe, ``test_ref`` il ``task_id`` (test in ``test``);
- la soluzione canonica è ``solution_code``; ``loc_to_generate`` sono le righe di codice della
  canonica meno quelle dello scheletro (SPEC §10.3: solo la parte da generare);
- il negativo umano è la classe canonica (SPEC §10.1).
"""

from __future__ import annotations

import ast
import json
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

import yaml

from bench.data.codenet_exclusions import check_not_excluded, load_excluded
from bench.data.loaders.base import (
    DatasetLoader,
    check_rows,
    human_sample,
    normalize_newlines,
    unique_keys,
)
from bench.domain.enums import Language, Level
from bench.domain.errors import ConfigError, DataError
from bench.domain.models import CodeSample, Problem
from bench.lang.line_counter import count_code_lines
from bench.registry import LOADERS

FIELDS = ("task_id", "skeleton", "test", "solution_code", "import_statement", "class_name",
          "test_classes")  # fmt: skip
KEY_PREFIX = "classeval/ClassEval_"


def classeval_key(task_id: str) -> str:
    """``problem_key`` di una classe (``ClassEval_7`` → ``classeval/ClassEval_7``)."""
    return f"classeval/{task_id}"


def load_pass1_excluded(path: Path) -> dict[str, str]:
    """Classi escluse da Pass@1 e ΔPass@1 (D41), ``problem_key`` → motivo.

    Raises:
        ConfigError: se il file è malformato, una chiave non è di ClassEval o è ripetuta.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("excluded")
    if not isinstance(entries, list):
        raise ConfigError(f"{path}: 'excluded' must be a list")
    result: dict[str, str] = {}
    for entry in entries:
        key, reason = entry.get("problem_key"), entry.get("reason")
        if not isinstance(key, str) or not key.startswith(KEY_PREFIX) or key in result:
            raise ConfigError(f"{path}: invalid or duplicate problem_key {key!r}")
        if not reason:
            raise ConfigError(f"{path}: missing reason for {key}")
        result[key] = str(reason)
    return result


def test_methods(test_code: str) -> dict[str, int]:
    """Classi del codice di test → numero di metodi ``test*`` (per le classi di soli appoggi)."""
    tree = ast.parse(test_code)
    return {
        node.name: sum(
            isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef) and f.name.startswith("test")
            for f in node.body
        )
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


@LOADERS.register("classeval")
class ClassEvalLoader(DatasetLoader):
    """Classi di ClassEval meno quelle escluse."""

    name: ClassVar[str] = "classeval"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L3})
    languages: ClassVar[frozenset[Language]] = frozenset({Language.PYTHON})
    contamination_risk: ClassVar[bool] = False

    @cached_property
    def all_rows(self) -> list[dict[str, Any]]:
        """Tutte le classi del file, controllate (campi e numero di righe)."""
        path = self.spec.file("data")
        if not path.is_file():
            raise DataError(f"file not found: {path} (initialise the ClassEval submodule)")
        rows = json.loads(path.read_text(encoding="utf-8"))
        for row in rows:
            missing = [k for k in FIELDS if k not in row]
            if missing:
                raise DataError(f"{path}: {row.get('task_id')}: missing fields {missing}")
        check_rows(self.spec, "data", len(rows))
        return list(rows)

    @cached_property
    def excluded(self) -> dict[str, str]:
        """Classi escluse (``problem_key`` → uso)."""
        return load_excluded(self.spec.file("excluded"), prefix=KEY_PREFIX)

    @cached_property
    def rows(self) -> list[dict[str, Any]]:
        """Classi del Livello 3: tutte meno le escluse, controllate con ``check_not_excluded``."""
        keys = {classeval_key(r["task_id"]) for r in self.all_rows}
        unknown = sorted(set(self.excluded) - keys)
        if unknown:
            raise DataError(f"classeval_excluded.yaml: unknown classes {unknown}")
        kept = [r for r in self.all_rows if classeval_key(r["task_id"]) not in self.excluded]
        check_not_excluded((classeval_key(r["task_id"]) for r in kept), self.excluded, "ClassEval")
        check_rows(self.spec, "classes", len(kept))
        return kept

    def tests(self) -> dict[str, dict[str, Any]]:
        """``task_id`` → test, classi di test e import (per l'executor ``classeval``).

        Due correzioni del dataset, uguali per tutti i metodi (ADR-016): i nomi delle classi di
        test sono senza spazi agli estremi (ClassEval_97 ha ``" Words2NumbersTestMain"``, che con
        ``getattr`` non si trova) e si tolgono le classi elencate che non contengono test
        (ClassEval_69: ``TestPDFHandler`` ha solo ``setUpClass`` e ``tearDownClass``, e con 0 test
        l'harness la conta come errore, quindi la classe non si potrebbe mai risolvere).
        """
        out: dict[str, dict[str, Any]] = {}
        for r in self.all_rows:
            test = normalize_newlines(str(r["test"]))
            counts = test_methods(test)
            names = [str(c).strip() for c in r["test_classes"]]
            out[str(r["task_id"])] = {
                "test": test,
                "test_classes": [c for c in names if counts.get(c, 1) > 0],
                "import_statement": [str(s) for s in r["import_statement"]],
            }
        return out

    def load_problems(self, language: Language) -> list[Problem]:
        lang = self._check_language(language)
        problems = []
        for row in self.rows:
            skeleton = normalize_newlines(str(row["skeleton"]))
            solution = normalize_newlines(str(row["solution_code"]))
            to_generate = count_code_lines(solution, lang, parsers=self.parsers) - count_code_lines(
                skeleton, lang, parsers=self.parsers
            )
            problems.append(
                Problem(
                    problem_key=classeval_key(row["task_id"]),
                    dataset=self.name,
                    level=Level.L3,
                    language=lang,
                    split=None,
                    prompt_text=skeleton,
                    entry_point=str(row["class_name"]),
                    canonical_solution=solution,
                    test_ref=str(row["task_id"]),
                    contamination_risk=self.contamination_risk,
                    loc_to_generate=max(0, to_generate),
                )
            )
        unique_keys(problems, "classeval")
        return problems

    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        lang = self._check_language(language)
        return [
            human_sample(
                problem_key=classeval_key(row["task_id"]),
                dataset=self.name,
                language=lang,
                level=Level.L3,
                code=str(row["solution_code"]),
                contamination_risk=self.contamination_risk,
            )
            for row in self.rows
        ]
