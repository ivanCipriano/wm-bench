"""T1.1 formattazione e T1.2 lint con correzione automatica (SPEC §12; ADR-012).

- T1.1: ``black`` in processo (Python), google-java-format (Java), clang-format con stile Google
  (C++), prettier (JavaScript);
- T1.2: ``ruff check --fix`` con le regole di default (Python), OpenRewrite
  ``CommonStaticAnalysis`` (Java), ``clang-tidy --fix`` (C++), ``eslint --fix`` con le regole
  raccomandate (JavaScript).

I tool esterni lavorano a lotti (``ToolRunner``); un errore del tool su un file dà
``TOOL_ERROR`` con il codice originale.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome, tool_error
from bench.attacks.tools import ToolRunner
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.registry import ATTACKS


class ToolAttack(Attack):
    """Attacco che applica un tool per linguaggio (``tools``); Python può usare una funzione."""

    group: ClassVar[str] = "T1"
    needs_tools: ClassVar[bool] = True
    tools: ClassVar[dict[Language, str]]

    def tool(self, language: Language) -> str:
        return self.tools[Language(language)]

    def apply_python(self, code: str) -> str | None:
        """Versione in processo per Python (``None`` = usa il tool esterno)."""
        return None

    def extra_args(self, tool: str, runner: ToolRunner, ctx: AttackContext) -> tuple[str, ...]:
        """Argomenti in più del tool per un campione (default: nessuno)."""
        return ()

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        return self.apply_many([code], language, [ctx])[0]

    def apply_many(
        self, codes: Sequence[str], language: Language, ctxs: Sequence[AttackContext]
    ) -> list[AttackOutcome]:
        lang = Language(language)
        out: list[AttackOutcome | None] = [None] * len(codes)
        todo: list[int] = []
        for i, code in enumerate(codes):
            if not self.supports(lang):
                out[i] = AttackOutcome(code, AttackStatus.NOT_APPLICABLE, {})
            elif not code.strip():
                out[i] = AttackOutcome(code, AttackStatus.UNCHANGED, {"empty": True})
            else:
                todo.append(i)
        tool = self.tool(lang)
        if tool == "black":
            for i in todo:
                out[i] = self._python(codes[i])
        elif todo:
            runner = ctxs[0].tools if ctxs else None
            if runner is None:
                raise ConfigError(f"{self.attack_id}: no ToolRunner in the attack context")
            self._run_tool(runner, tool, codes, ctxs, todo, out)
        return [o for o in out if o is not None]

    def _run_tool(
        self,
        runner: ToolRunner,
        tool: str,
        codes: Sequence[str],
        ctxs: Sequence[AttackContext],
        todo: list[int],
        out: list[AttackOutcome | None],
    ) -> None:
        """Un'invocazione a lotti per ogni insieme di argomenti in più (di solito uno solo)."""
        groups: dict[tuple[str, ...], list[int]] = {}
        for i in todo:
            groups.setdefault(self.extra_args(tool, runner, ctxs[i]), []).append(i)
        for args, indices in groups.items():
            results = runner.run(tool, [codes[i] for i in indices], extra_args=args)
            for i, res in zip(indices, results, strict=True):
                detail: dict[str, Any] = {"tool": tool}
                if args:
                    detail["context"] = "prelude"
                if res.code is None:
                    out[i] = tool_error(codes[i], res.error or "tool error", detail)
                else:
                    out[i] = outcome(codes[i], res.code, detail)

    def _python(self, code: str) -> AttackOutcome:
        try:
            new = self.apply_python(code)
        except Exception as exc:  # black solleva errori diversi per codice non valido
            return tool_error(code, f"{type(exc).__name__}: {exc}", {"tool": "black"})
        return outcome(code, code if new is None else new, {"tool": "black"})


@ATTACKS.register("T1.1")
class FormatAttack(ToolAttack):
    """T1.1: formattazione con il formatter di riferimento del linguaggio."""

    attack_id: ClassVar[str] = "T1.1"
    tools: ClassVar[dict[Language, str]] = {
        Language.PYTHON: "black",
        Language.JAVA: "google-java-format",
        Language.CPP: "clang-format",
        Language.JAVASCRIPT: "prettier",
    }

    def apply_python(self, code: str) -> str | None:
        import black

        return black.format_str(code, mode=black.Mode())


@ATTACKS.register("T1.2")
class LintAttack(ToolAttack):
    """T1.2: linter con correzione automatica (solo le correzioni sicure dei tool)."""

    attack_id: ClassVar[str] = "T1.2"
    # 2: clang-tidy vede gli include che l'harness di HumanEvalPack mette davanti al codice.
    version: ClassVar[int] = 2
    tools: ClassVar[dict[Language, str]] = {
        Language.PYTHON: "ruff",
        Language.JAVA: "openrewrite",
        Language.CPP: "clang-tidy",
        Language.JAVASCRIPT: "eslint",
    }

    def extra_args(self, tool: str, runner: ToolRunner, ctx: AttackContext) -> tuple[str, ...]:
        """C++ di HumanEvalPack: ``-include`` del contesto dell'harness (``cpp_prelude``).

        Il codice generato spesso non ha include né ``using namespace std``, che l'harness mette
        davanti prima di compilare (``prepare_generation``): senza, clang-tidy non lo analizza
        (``no template named 'vector'``) e non applica correzioni. Il file resta il codice del
        campione: il contesto entra solo come opzione del compilatore.
        """
        problem = ctx.problem
        if tool != "clang-tidy" or problem is None or problem.dataset != "humanevalpack":
            return ()
        return ("-include", str(runner.prelude(cpp_prelude())))


def cpp_prelude() -> str:
    """Contesto C++ dell'harness (``IMPORT_HELPER["cpp"]``) per clang-tidy.

    Due differenze rispetto all'harness, che non cambiano il significato del codice:
    ``using namespace std;`` va dopo gli include (clang rifiuta un ``namespace std`` non ancora
    dichiarato) e ``boost/any.hpp`` resta fuori (boost c'è solo nella sandbox, non nell'ambiente
    dei tool: i campioni che lo usano restano ``TOOL_ERROR``).
    """
    from bench.execution.harness import import_helper

    lines = [line.strip() for line in import_helper()["cpp"]]
    includes = [line for line in lines if line.startswith("#include") and "boost/" not in line]
    usings = [line for line in lines if line.startswith("using ")]
    return "\n".join([*includes, *usings]) + "\n"
