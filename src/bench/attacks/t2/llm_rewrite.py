"""T2.2: riscrittura con LLM (SPEC §12), varianti ``conservative`` e ``full_rewrite``.

Testi approvati dall'utente il 24 ottobre 2026 (bozza v2, ``M11_t2_2_preview_draft_v2.txt``):
``configs/prompt/attacks/{system,conservative,full_rewrite}.txt``, uguali per tutti i metodi e i
linguaggi (cambia solo il nome del linguaggio), senza riferimenti al watermark.
Llama-3.1-8B-Instruct, greedy, ``max_new_tokens`` = 2048 (:mod:`bench.attacks.llm`). Il codice si
estrae con la regola D4;
se l'estrazione fallisce il campione è ``TOOL_ERROR`` con il codice originale, mai scartato.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from bench.attacks.base import Attack, AttackContext, AttackOutcome
from bench.attacks.t2.common import (
    final_outcome,
    load_prompt,
    messages,
    render,
    require_llm_params,
)
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.generation.code_extractor import FencedCodeExtractor
from bench.registry import ATTACKS

VARIANTS = ("conservative", "full_rewrite")


@ATTACKS.register("T2.2")
class LLMRewriteAttack(Attack):
    """T2.2: una cella per variante; parametri in ``configs/attack/t2_2_llm_rewrite.yaml``."""

    attack_id: ClassVar[str] = "T2.2"
    group: ClassVar[str] = "T2"
    requires_gpu: ClassVar[bool] = True
    needs_llm: ClassVar[bool] = True

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        require_llm_params(
            self.attack_id, self.params, ("variant", "system_sha256", "prompt_sha256")
        )
        if self.params["variant"] not in VARIANTS:
            raise ConfigError(
                f"T2.2: variant must be one of {VARIANTS}, got {self.params['variant']!r}"
            )
        self.extractor = FencedCodeExtractor()

    def prompts(self) -> tuple[str, str]:
        """System prompt e template della variante (con il controllo dello SHA-256)."""
        system = load_prompt("system", str(self.params["system_sha256"]))
        template = load_prompt(str(self.params["variant"]), str(self.params["prompt_sha256"]))
        return system, template

    def manifest_extra(self) -> dict[str, Any]:
        return {
            "prompts_sha256": {
                "system.txt": self.params["system_sha256"],
                f"{self.params['variant']}.txt": self.params["prompt_sha256"],
            }
        }

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        return self.apply_many([code], language, [ctx])[0]

    def apply_many(
        self, codes: Sequence[str], language: Language, ctxs: Sequence[AttackContext]
    ) -> list[AttackOutcome]:
        """Tutti i campioni non vuoti della cella in lotti di ``batch_size``."""
        if not self.supports(language):
            return [AttackOutcome(c, AttackStatus.NOT_APPLICABLE, {}) for c in codes]
        out: list[AttackOutcome | None] = [None] * len(codes)
        todo = []
        for i, code in enumerate(codes):
            if code.strip():
                todo.append(i)
            else:
                out[i] = AttackOutcome(code, AttackStatus.UNCHANGED, {"empty": True})
        if todo:
            llm = ctxs[todo[0]].llm
            if llm is None:
                raise ConfigError("T2.2 needs the attacker LLM in the attack context")
            system, template = self.prompts()
            convs = [messages(system, render(template, language, codes[i])) for i in todo]
            detail = {"variant": self.params["variant"]}
            for i, completion in zip(todo, llm.generate(convs), strict=True):
                out[i] = final_outcome(
                    codes[i], completion, ctxs[i], language, self.extractor, detail
                )
        return [o for o in out if o is not None]
