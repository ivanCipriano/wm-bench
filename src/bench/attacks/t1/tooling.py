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
            results = runner.run(tool, [codes[i] for i in todo])
            for i, res in zip(todo, results, strict=True):
                detail: dict[str, Any] = {"tool": tool}
                if res.code is None:
                    out[i] = tool_error(codes[i], res.error or "tool error", detail)
                else:
                    out[i] = outcome(codes[i], res.code, detail)
        return [o for o in out if o is not None]

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
    tools: ClassVar[dict[Language, str]] = {
        Language.PYTHON: "ruff",
        Language.JAVA: "openrewrite",
        Language.CPP: "clang-tidy",
        Language.JAVASCRIPT: "eslint",
    }
