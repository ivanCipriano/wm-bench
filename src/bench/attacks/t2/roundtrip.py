"""T2.3: traduzione andata e ritorno con LLM (SPEC §12), condizionale.

Coppie dello SPEC: py→java→py, java→py→java, cpp→java→cpp, js→py→js. Stesso LLM e decoding di
T2.2 (:mod:`bench.attacks.llm`, ``clean_up_tokenization_spaces=False``). Prompt in
``configs/prompt/attacks/{system_translate,translate}.txt``: **bozza non approvata**, per questo
l'attacco è spento (``enabled: false`` in ``configs/attack/t2_3_roundtrip.yaml``, SPEC
``attack.t2_3.enabled``). Andata: codice estratto con il passo 1 di D4 nel linguaggio intermedio;
ritorno: regola D4 completa nel linguaggio del campione. Un'estrazione fallita in uno dei due passi
dà ``TOOL_ERROR`` con il codice originale (``detail.step``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from bench.attacks.base import Attack, AttackContext, AttackOutcome, tool_error
from bench.attacks.t2.common import (
    completion_detail,
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

PIVOT: dict[Language, Language] = {
    Language.PYTHON: Language.JAVA,
    Language.JAVA: Language.PYTHON,
    Language.CPP: Language.JAVA,
    Language.JAVASCRIPT: Language.PYTHON,
}


@ATTACKS.register("T2.3")
class RoundTripAttack(Attack):
    """T2.3: una cella; parametri in ``configs/attack/t2_3_roundtrip.yaml``."""

    attack_id: ClassVar[str] = "T2.3"
    group: ClassVar[str] = "T2"
    requires_gpu: ClassVar[bool] = True
    needs_llm: ClassVar[bool] = True

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        require_llm_params(self.attack_id, self.params, ("system_sha256", "prompt_sha256"))
        self.extractor = FencedCodeExtractor()

    def prompts(self) -> tuple[str, str]:
        system = load_prompt("system_translate", str(self.params["system_sha256"]))
        return system, load_prompt("translate", str(self.params["prompt_sha256"]))

    def manifest_extra(self) -> dict[str, Any]:
        return {
            "prompts_sha256": {
                "system_translate.txt": self.params["system_sha256"],
                "translate.txt": self.params["prompt_sha256"],
            }
        }

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        return self.apply_many([code], language, [ctx])[0]

    def apply_many(
        self, codes: Sequence[str], language: Language, ctxs: Sequence[AttackContext]
    ) -> list[AttackOutcome]:
        if not self.supports(language):
            return [AttackOutcome(c, AttackStatus.NOT_APPLICABLE, {}) for c in codes]
        out: list[AttackOutcome | None] = [None] * len(codes)
        todo = []
        for i, code in enumerate(codes):
            if code.strip():
                todo.append(i)
            else:
                out[i] = AttackOutcome(code, AttackStatus.UNCHANGED, {"empty": True})
        if not todo:
            return [o for o in out if o is not None]
        llm = ctxs[todo[0]].llm
        if llm is None:
            raise ConfigError("T2.3 needs the attacker LLM in the attack context")
        system, template = self.prompts()
        pivot = PIVOT[language]
        forward = llm.generate(
            [messages(system, render(template, language, codes[i], pivot)) for i in todo]
        )
        back_todo: list[tuple[int, str, dict[str, Any]]] = []
        for i, completion in zip(todo, forward, strict=True):
            detail = {"pivot": str(pivot), **completion_detail("forward_", completion)}
            intermediate = self.extractor.select(completion.text, pivot)
            if intermediate is None:
                out[i] = tool_error(
                    codes[i], "forward extraction failed", {**detail, "step": "forward"}
                )
            else:
                back_todo.append((i, intermediate, {**detail, "intermediate": intermediate}))
        backward = llm.generate(
            [messages(system, render(template, pivot, mid, language)) for _, mid, _ in back_todo]
        )
        for (i, _, detail), completion in zip(back_todo, backward, strict=True):
            result = final_outcome(codes[i], completion, ctxs[i], language, self.extractor, detail)
            if result.status is AttackStatus.TOOL_ERROR:
                result.detail["step"] = "backward"
            out[i] = result
        return [o for o in out if o is not None]
