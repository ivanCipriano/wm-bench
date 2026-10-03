"""Test di PromptBuilder: system prompt bloccato e template approvati (ADR-006)."""

from __future__ import annotations

import pytest

from bench.config.schema import ExperimentConfig
from bench.data.loaders.humanevalpack import HumanEvalPackLoader
from bench.data.loaders.humanevalplus import HumanEvalPlusLoader
from bench.data.loaders.mbppplus import MbppPlusLoader
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.domain.models import Problem
from bench.generation.prompt_builder import PromptBuilder
from tests.datafix import build_tiny_datasets, tiny_config

SYSTEM = (
    "You are an expert software engineer.\n"
    "Solve the programming task you are given.\n"
    "Reply with the complete solution in a single fenced code block in the requested "
    "programming language, without explanations.\n"
    "Do not include tests, example usage or a main function unless the task explicitly "
    "asks for a complete program."
)
NAMES = {
    "python": ("Python", "python"),
    "java": ("Java", "java"),
    "cpp": ("C++", "cpp"),
    "javascript": ("JavaScript", "javascript"),
}


@pytest.fixture
def problems(cfg: ExperimentConfig) -> list[Problem]:
    build_tiny_datasets(cfg.paths.datasets)
    t = tiny_config(cfg)
    out = HumanEvalPlusLoader(t.datasets["humanevalplus"]).load_problems(Language.PYTHON)[:1]
    out += MbppPlusLoader(t.datasets["mbppplus"]).load_problems(Language.PYTHON)[:1]
    hep = HumanEvalPackLoader(t.datasets["humanevalpack"])
    for lang in (Language.JAVA, Language.CPP, Language.JAVASCRIPT):
        out += hep.load_problems(lang)[:1]
    return out


def test_system_prompt_is_the_approved_text(cfg: ExperimentConfig) -> None:
    builder = PromptBuilder.from_config(cfg.prompt)
    assert builder.system_prompt == SYSTEM


def test_system_prompt_is_locked(cfg: ExperimentConfig) -> None:
    changed = cfg.prompt.model_copy(update={"system_prompt_sha256": "0" * 64})
    with pytest.raises(ConfigError, match="must not change"):
        PromptBuilder.from_config(changed)


def test_messages_and_verbatim_prompt(cfg: ExperimentConfig, problems: list[Problem]) -> None:
    builder = PromptBuilder.from_config(cfg.prompt)
    for problem in problems:
        messages = builder.build(problem)
        assert [m["role"] for m in messages] == ["system", "user"]
        user = messages[1]["content"]
        name, fence = NAMES[problem.language]
        assert user.startswith(f"Complete the following {name} code.\n")
        assert f"the tests call `{problem.entry_point}` by name." in user
        assert f"in a single ```{fence} code block." in user
        # Prompt del dataset parola per parola, dentro il blocco del linguaggio.
        assert f"```{fence}\n{problem.prompt_text}```" in user
        assert user.endswith("```")


def test_same_structure_in_all_templates(cfg: ExperimentConfig, problems: list[Problem]) -> None:
    builder = PromptBuilder.from_config(cfg.prompt)
    shapes = set()
    for problem in problems:
        user = builder.render_user(problem)
        name, fence = NAMES[problem.language]
        user = user.replace(problem.prompt_text, "<PROMPT>").replace(
            str(problem.entry_point), "<EP>"
        )
        shapes.add(user.replace(f"```{fence}", "```<TAG>").replace(f" {name} ", " <LANG> "))
    assert len(shapes) == 1


def test_hashes_cover_system_and_templates(cfg: ExperimentConfig) -> None:
    hashes = PromptBuilder.from_config(cfg.prompt).prompt_hashes()
    assert set(hashes) == {
        "system.txt",
        "humanevalplus_python.j2",
        "mbppplus_python.j2",
        "humanevalpack_java.j2",
        "humanevalpack_cpp.j2",
        "humanevalpack_javascript.j2",
    }
    assert hashes["system.txt"] == cfg.prompt.system_prompt_sha256


def test_missing_template_or_entry_point(cfg: ExperimentConfig, problems: list[Problem]) -> None:
    builder = PromptBuilder.from_config(cfg.prompt)
    with pytest.raises(ConfigError, match="no user template"):
        builder.render_user(problems[0].model_copy(update={"dataset": "unknown"}))
    with pytest.raises(ConfigError, match="entry_point"):
        builder.render_user(problems[0].model_copy(update={"entry_point": None}))
