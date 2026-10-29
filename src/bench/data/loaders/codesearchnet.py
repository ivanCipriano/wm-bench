"""CodeSearchNet nel Livello 3: funzioni dello split di test (cluster_info §8.4; ADR-016 §2).

Per linguaggio (Python, Java, JavaScript), dallo split di **test**:

- **prompt candidati**: le funzioni con documentazione (``func_documentation_string`` non vuota)
  da cui si costruisce il prompt firma + docstring (``bench.data.l3_prompts``); i motivi di scarto
  si contano. La scelta dei 500 prompt, stratificata per righe del corpo, e l'esclusione delle
  funzioni già usate in L1 (D39) le fa ``prepare_data``;
- **negativi**: tutte le funzioni che si analizzano (come l'integrazione dei negativi di L1,
  ``CodeSearchNetSource``), con ``contamination_risk=True`` (I6).

``problem_key`` = chiave del candidato (``csn/<lang>/<xxh3 di func_code_url>``), la stessa
dell'integrazione di L1: così l'esclusione D39 è un confronto di chiavi.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Any, ClassVar

import xxhash

from bench.data.l3_prompts import PromptedFunction, brace_prompt, dedent_python, python_prompt
from bench.data.loaders.base import (
    DatasetLoader,
    check_rows,
    human_sample,
    normalize_newlines,
    read_parquet,
)
from bench.data.sources import Candidate, CodeSearchNetSource
from bench.domain.enums import Language, Level
from bench.domain.models import CodeSample, Problem
from bench.registry import LOADERS

SPLIT = "test"
COLUMNS = ("repository_name", "func_name", "whole_func_string", "func_documentation_string",
           "func_code_url")  # fmt: skip


def csn_key(lang: Language, url: str) -> str:
    """Chiave di una funzione (uguale a quella di ``CodeSearchNetSource``)."""
    return f"csn/{Language(lang)}/{xxhash.xxh3_64_hexdigest(url.encode('utf-8'))}"


def problem_of(key: str, lang: Language, fn: PromptedFunction) -> Problem:
    """Problema di L3 da una funzione con prompt (``split`` da assegnare)."""
    return Problem(
        problem_key=key,
        dataset="codesearchnet",
        level=Level.L3,
        language=lang,
        split=None,
        prompt_text=fn.prompt,
        entry_point=fn.entry_point,
        canonical_solution=fn.code,
        test_ref=None,
        contamination_risk=True,
        loc_to_generate=fn.loc_to_generate,
    )


@LOADERS.register("codesearchnet")
class CodeSearchNetLoader(DatasetLoader):
    """Prompt e negativi di L3 dallo split di test di CodeSearchNet."""

    name: ClassVar[str] = "codesearchnet"
    levels: ClassVar[frozenset[Level]] = frozenset({Level.L3})
    languages: ClassVar[frozenset[Language]] = frozenset(
        {Language.PYTHON, Language.JAVA, Language.JAVASCRIPT}
    )
    contamination_risk: ClassVar[bool] = True

    def prompt_pool(self, language: Language) -> tuple[list[Problem], dict[str, Any]]:
        """Tutte le funzioni da cui si costruisce un prompt e i conteggi degli scarti."""
        lang = self._check_language(language)
        role = f"{lang}/{SPLIT}"
        frame = read_parquet(self.spec.file(role), COLUMNS)
        check_rows(self.spec, role, len(frame))
        drops: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        out: list[Problem] = []
        dedented = 0
        for row in frame.itertuples(index=False):
            doc = str(row.func_documentation_string or "")
            if not doc.strip():
                drops["no_documentation"] += 1
                continue
            raw = normalize_newlines(str(row.whole_func_string))
            code = dedent_python(raw, lang)
            dedented += code != raw
            if lang is Language.PYTHON:
                fn = python_prompt(code, self.parsers)
            else:
                fn = brace_prompt(code, lang, normalize_newlines(doc), self.parsers)
            if isinstance(fn, str):
                drops[fn] += 1
                continue
            kinds[fn.doc_kind] += 1
            out.append(problem_of(csn_key(lang, str(row.func_code_url)), lang, fn))
        funnel = {
            "rows": len(frame),
            "dropped": dict(sorted(drops.items())),
            "doc_kind": dict(sorted(kinds.items())),
            "candidates": len(out),
            "python_dedented": dedented,
        }
        return out, funnel

    def negative_pool(self, language: Language) -> tuple[list[Candidate], dict[str, Any]]:
        """Tutte le funzioni dello split di test che si analizzano (negativi di L3)."""
        lang = self._check_language(language)
        cands, stats = CodeSearchNetSource(self.spec, self.parsers).candidates(lang, SPLIT)
        out = [dataclasses.replace(c, code=dedent_python(c.code, lang)) for c in cands]
        changed = sum(a.code != b.code for a, b in zip(cands, out, strict=True))
        return out, stats.as_dict() | {"python_dedented": changed}

    def load_problems(self, language: Language) -> list[Problem]:
        return self.prompt_pool(language)[0]

    def load_human_negatives(self, language: Language) -> list[CodeSample]:
        lang = self._check_language(language)
        return [human_negative(c, lang, self.name) for c in self.negative_pool(lang)[0]]


def human_negative(cand: Candidate, lang: Language, dataset: str) -> CodeSample:
    """Negativo umano di L3 (``problem_key`` = chiave del candidato)."""
    return human_sample(
        problem_key=cand.key,
        dataset=dataset,
        language=lang,
        level=Level.L3,
        code=cand.code,
        contamination_risk=True,
    )
