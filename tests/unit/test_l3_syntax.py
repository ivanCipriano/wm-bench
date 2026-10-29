"""L3 senza test (ADR-016): ``dedent`` del codice Python e validità sintattica con tree-sitter."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.config.schema import ExperimentConfig
from bench.data.l3_prompts import dedent_python, tree_sitter_valid
from bench.domain.enums import ExecStatus, Language, Level, Split
from bench.domain.models import Problem
from bench.execution.executors import SampleToRun, SyntaxExecutor
from bench.execution.sandbox import LocalSandbox
from bench.generation.code_extractor import FencedCodeExtractor
from bench.lang.parsers import default_parsers

METHOD = '    def area(self):\n        """Area."""\n        return self.w * self.h\n'


def _problem(dataset: str, lang: Language, prompt: str = "", entry: str = "area") -> Problem:
    return Problem(
        problem_key=f"{dataset}/x", dataset=dataset, level=Level.L3, language=lang,
        split=Split.TEST, prompt_text=prompt, entry_point=entry,
        canonical_solution="def area(self):\n    return 1\n", test_ref=None,
        contamination_risk=True, loc_to_generate=1,
    )  # fmt: skip


def test_indented_python_method_is_valid_after_dedent() -> None:
    parsers = default_parsers()
    with pytest.raises(IndentationError, match="unexpected indent"):  # py_compile senza dedent
        compile(METHOD, "method.py", "exec")
    assert dedent_python(METHOD, Language.PYTHON).startswith("def area(self):\n")
    compile(dedent_python(METHOD, Language.PYTHON), "method.py", "exec")
    assert tree_sitter_valid(METHOD, Language.PYTHON, parsers)
    assert dedent_python("  int f() {}", Language.CPP) == "  int f() {}"  # solo Python
    # Metodi Java isolati: validi negli involucri (in una classe); codice rotto: non valido.
    java = "public int f(int x) {\n    return x;\n}"
    assert tree_sitter_valid(java, Language.JAVA, parsers)
    assert not tree_sitter_valid("public int f(int x) {\n    return x\n", Language.JAVA, parsers)
    assert not tree_sitter_valid("   \n", Language.PYTHON, parsers)


def test_extraction_dedents_codesearchnet_python_only() -> None:
    extractor = FencedCodeExtractor()
    raw = f"```python\n{METHOD}```"
    code, ok = extractor.extract(raw, _problem("codesearchnet", Language.PYTHON))
    assert ok and code == dedent_python(METHOD, Language.PYTHON)
    other, _ = extractor.extract(raw, _problem("classeval", Language.PYTHON, entry="Shape"))
    assert other == METHOD  # gli altri dataset non cambiano


def test_syntax_executor_uses_tree_sitter_as_reference(
    cfg: ExperimentConfig, tmp_path: Path
) -> None:
    executor = SyntaxExecutor(cfg, LocalSandbox(), Language.PYTHON, tmp_path)
    assert executor.precheck(SampleToRun("a", "p", 0, METHOD)) is None  # valido: si compila
    broken = executor.precheck(SampleToRun("b", "p", 0, "def f(:\n    pass\n"))
    assert broken is not None and broken.status is ExecStatus.SYNTAX_ERROR
    assert broken.stderr_tail == "tree-sitter: ERROR node"
    assert executor.source(METHOD) == dedent_python(METHOD, Language.PYTHON)
    problem = _problem("codesearchnet", Language.PYTHON, prompt="def area(self):\n")
    assert executor.canonical(problem) == problem.canonical_solution  # non prompt + soluzione
