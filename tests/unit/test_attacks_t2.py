"""Attacchi T2 con LLM (T2.2, T2.3) con un attaccante finto: prompt bloccati, estrazione D4,
stati, griglie, T2.3 spento e risorse GPU (SPEC §12, §21.3)."""

from __future__ import annotations

import random
from collections.abc import Sequence

import pytest

from bench.attacks.base import AttackContext, attack_grid, grid_attacks, make_attack
from bench.attacks.llm import Completion
from bench.attacks.t2.common import load_prompt, prompt_path_sha256, render
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.domain.models import Problem
from bench.lang.parsers import default_parsers

ORIGINAL = "def add(a, b):\n    return a + b\n"


class FakeLLM:
    """Risponde con i testi dati, in ordine; registra le conversazioni ricevute."""

    def __init__(self, replies: Sequence[str]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, str]]] = []

    def generate(self, conversations: Sequence[list[dict[str, str]]]) -> list[Completion]:
        self.seen.extend(conversations)
        out = [Completion(self.replies.pop(0), 10, False) for _ in conversations]
        return out


def _ctx(llm: FakeLLM, language: str = "python") -> AttackContext:
    problem = Problem(
        problem_key="humaneval/0",
        dataset="humanevalplus" if language == "python" else "humanevalpack",
        level="L1",
        language=language,
        split="test",
        prompt_text="def add(a, b):\n",
        entry_point="add",
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )
    return AttackContext(
        rng=random.Random(0),
        problem=problem,
        parsers=default_parsers(),
        llm=llm,  # type: ignore[arg-type]
    )


def _t22(variant: str) -> object:
    attacks = [a for a in grid_attacks("T2.2").values() if a.params["variant"] == variant]
    assert len(attacks) == 1
    return attacks[0]


def test_t2_2_grid_and_approved_prompts() -> None:
    grid = attack_grid("T2.2")
    assert sorted(p["variant"] for p in grid.points) == ["conservative", "full_rewrite"]
    assert len(grid_attacks("T2.2")) == 2
    for point in grid.points:
        assert point["system_sha256"] == prompt_path_sha256("system")
        assert point["prompt_sha256"] == prompt_path_sha256(point["variant"])
        assert point["max_new_tokens"] == 2048 and point["attacker"] == "llama31_8b_attacker"
    attack = make_attack("T2.2", grid.points[0])
    assert attack.requires_gpu and attack.needs_llm
    assert set(attack.manifest_extra()["prompts_sha256"]) == {
        "system.txt",
        f"{grid.points[0]['variant']}.txt",
    }


def test_prompt_with_another_sha_is_refused() -> None:
    with pytest.raises(ConfigError, match="sha256"):
        load_prompt("system", "0" * 64)


def test_render_changes_only_the_language() -> None:
    template = load_prompt("full_rewrite", prompt_path_sha256("full_rewrite"))
    names = {"python": "Python", "java": "Java", "cpp": "C++", "javascript": "JavaScript"}
    texts = {lang: render(template, lang, "x") for lang in Language}
    assert "C++ code" in texts[Language.CPP] and "```cpp\nx\n```" in texts[Language.CPP]
    assert all("watermark" not in t.lower() for t in texts.values())
    generic = {
        t.replace(f" {names[str(lang)]} ", " L ").replace(f"```{lang}", "```t")
        for lang, t in texts.items()
    }
    assert len(generic) == 1


@pytest.mark.parametrize(
    ("reply", "status", "code"),
    [
        ("```python\ndef add(a, b):\n    return b + a\n```", AttackStatus.APPLIED, None),
        (f"```python\n{ORIGINAL}```", AttackStatus.UNCHANGED, ORIGINAL),
        ("Sorry, I cannot help with that. ```", AttackStatus.TOOL_ERROR, ORIGINAL),
    ],
)
def test_t2_2_statuses(reply: str, status: AttackStatus, code: str | None) -> None:
    attack = _t22("full_rewrite")
    llm = FakeLLM([reply])
    out = attack.apply_many([ORIGINAL], Language.PYTHON, [_ctx(llm)])[0]  # type: ignore[attr-defined]
    assert out.status is status
    if code is not None:
        assert out.code == code
    else:
        assert "b + a" in out.code
    assert out.detail["variant"] == "full_rewrite"
    assert "extraction_ok" in out.detail
    system, user = llm.seen[0]
    assert system["role"] == "system" and "Python code" in user["content"]
    assert ORIGINAL.rstrip("\n") in user["content"]


def test_t2_2_empty_code_is_not_sent() -> None:
    llm = FakeLLM(["```python\ndef add(a, b):\n    return a - b\n```"])
    attack = _t22("conservative")
    outs = attack.apply_many(["", ORIGINAL], Language.PYTHON, [_ctx(llm), _ctx(llm)])  # type: ignore[attr-defined]
    assert [o.status for o in outs] == [AttackStatus.UNCHANGED, AttackStatus.APPLIED]
    assert len(llm.seen) == 1


def test_t2_3_is_disabled_by_default() -> None:
    with pytest.raises(ConfigError, match="disabled"):
        attack_grid("T2.3")


def test_t2_3_round_trip_with_pivot() -> None:
    from bench.attacks.t2.roundtrip import RoundTripAttack

    params = {
        "attacker": "llama31_8b_attacker",
        "max_new_tokens": 2048,
        "batch_size": 8,
        "system_sha256": prompt_path_sha256("system_translate"),
        "prompt_sha256": prompt_path_sha256("translate"),
    }
    attack = RoundTripAttack(params)
    llm = FakeLLM(
        [
            "```java\nclass S { int add(int a, int b) { return a + b; } }\n```",
            "```python\ndef add(a, b):\n    total = a + b\n    return total\n```",
        ]
    )
    out = attack.apply_many([ORIGINAL], Language.PYTHON, [_ctx(llm)])[0]
    assert out.status is AttackStatus.APPLIED and "total" in out.code
    assert out.detail["pivot"] == "java" and "class S" in out.detail["intermediate"]
    assert "into Java" in llm.seen[0][1]["content"]
    assert "into Python" in llm.seen[1][1]["content"]
    failed = attack.apply_many([ORIGINAL], Language.PYTHON, [_ctx(FakeLLM(["no code ```"]))])[0]
    assert failed.status is AttackStatus.TOOL_ERROR and failed.detail["step"] == "forward"
    assert failed.code == ORIGINAL


def test_t2_runs_on_gpu() -> None:
    from bench.config.builder import load_experiment
    from bench.config.resources import ResourceClass, ResourcePolicy

    cfg = load_experiment(["stage=attack"])
    policy = ResourcePolicy(cfg.resources, {}, cfg.slurm)
    assert policy.resource_for("attack", attack="T2.2") is ResourceClass.GPU_NVIDIA
    assert policy.resource_for("attack", attack="T1.4") is ResourceClass.CPU
