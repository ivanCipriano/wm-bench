"""PromptMark senza GPU: adapter, esempi del prompt usati nel ciclo, limiti del processo figlio."""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path
from typing import Any

import pytest

from bench.config.schema import ExperimentConfig
from bench.data.promptmark_freq import LetterFrequencies
from bench.domain.errors import ConfigError
from bench.methods.adapters.promptmark import PromptMarkAdapter
from bench.methods.base import EMBED_COLUMNS, MethodAdapter
from tests.conftest import REPO_ROOT
from tests.unit.test_adapters import _problem, echo_client

sys.path.insert(0, str(REPO_ROOT / "shims"))
from bench_shims.promptmark.examples import prompt_examples

FREQS = LetterFrequencies(
    letter_freqs={c: i + 1 for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    total_identifiers=351,
    n_programs=10,
    n_skipped=0,
    source="test",
)


@pytest.fixture(autouse=True)
def fake_commit_and_freqs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)
    monkeypatch.setattr(PromptMarkAdapter, "frequencies", lambda self: FREQS)


def test_promptmark_hparams(cfg: ExperimentConfig) -> None:
    a = PromptMarkAdapter(cfg.methods_catalog["promptmark"], cfg, echo_client())
    hp = a.default_hparams()
    assert hp == {"iter_cap": 5, "z_threshold": 2.12, "green_size": None}
    native = a.to_native_hparams(hp)
    assert native["g_min"] == 8 and native["g_max"] == 18 and native["iter_cap"] == 5
    assert native["letter_freqs"] == FREQS.letter_freqs and native["total_identifiers"] == 351
    fixed = a.to_native_hparams({**hp, "green_size": 12})
    assert fixed["g_min"] == fixed["g_max"] == 12
    assert a.config_hash(hp) != a.config_hash({**hp, "green_size": 12})
    with pytest.raises(ConfigError, match="green_size"):
        a.to_native_hparams({**hp, "green_size": 19})
    with pytest.raises(ConfigError, match="iter_cap"):
        a.to_native_hparams({**hp, "iter_cap": 0})
    with pytest.raises(ConfigError, match="missing"):
        a.to_native_hparams({"iter_cap": 5})
    assert a.supports("python") and not a.supports("java")
    assert not a.gpu_for_detect and a.seed_scheme() == "per_sample"
    assert a.source().pythonpath == a.source().root / "src"


def test_promptmark_embed_columns(cfg: ExperimentConfig, tmp_path: Path) -> None:
    from bench.domain.enums import Language
    from bench.generation.prompt_builder import PromptBuilder

    client = echo_client()
    a = PromptMarkAdapter(cfg.methods_catalog["promptmark"], cfg, client)
    run = a.embed_from_prompts(
        [_problem("humaneval/0", Language.PYTHON)],
        a.default_hparams(),
        cfg.models_catalog["qwen25_coder_7b"],
        "k1",
        {"num_return_sequences": 2},
        2,
        PromptBuilder.from_config(cfg.prompt),
        tmp_path,
        "cpu",
    )
    assert [it.n for it in client.last_items] == [1, 1]
    assert len(run.metrics) == 2 and set(run.metrics[0]) == set(EMBED_COLUMNS)


HE_PROMPT = '''from typing import List


def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if two numbers are closer than threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    >>> print('not an expression');
    >>> x = 1
    >>> has_close_elements([1.0], 0.5)
    not python ->
    """
'''

MBPP_PROMPT = '''"""
Write a function to find the shared elements from the given two lists.
assert set(similar_elements((3, 4, 5, 6),(5, 7, 4, 10))) == set((4, 5))
assert broken(
"""
'''


def test_prompt_examples_inside_the_framework_fence() -> None:
    """Il prompt del framework racchiude il codice in un blocco ``` subito dopo la docstring."""
    rendered = "Complete the following Python code.\n\n```python\n" + HE_PROMPT + "```"
    assert prompt_examples(rendered) == prompt_examples(HE_PROMPT)
    assert len(prompt_examples(rendered)) == 2


def test_prompt_examples_with_trailing_comments() -> None:
    """Un commento in coda all'esempio non deve rendere l'asserzione non valida (HumanEval/32)."""
    text = (
        "    >>> round(find_zero([1, 2]), 2) # f(x) = 1 + 2x\n    -0.5\n    >>> f('#')\n    '#'\n"
    )
    assert prompt_examples(text) == [
        "assert (round(find_zero([1, 2]), 2)) == (-0.5)",
        "assert (f('#')) == ('#')",
    ]


def test_prompt_examples() -> None:
    assert prompt_examples(HE_PROMPT) == [
        "assert (has_close_elements([1.0, 2.0, 3.0], 0.5)) == (False)",
        "assert (has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)) == (True)",
    ]
    assert prompt_examples(MBPP_PROMPT) == [
        "assert set(similar_elements((3, 4, 5, 6),(5, 7, 4, 10))) == set((4, 5))"
    ]
    assert prompt_examples("no examples here") == []


def _child(return_dict: Any) -> None:
    from bench_shims.promptmark.__main__ import limited

    def original(code: str, imports: Any, tests: Any, out: Any) -> Any:
        import os
        import resource

        out["cwd"] = os.getcwd()
        out["cuda"] = os.environ.get("CUDA_VISIBLE_DEVICES")
        out["secret"] = os.environ.get("HF_TOKEN")
        out["nproc"] = resource.getrlimit(resource.RLIMIT_NPROC)[0]
        try:
            os.fork()
            out["fork"] = "allowed"
        except OSError:
            out["fork"] = "blocked"
        return out

    limited(original, return_dict["tmp"])("", [], [], return_dict)


@pytest.mark.skipif(sys.platform != "linux", reason="setrlimit and /proc are Linux-only")
def test_limits_in_the_child_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "secret")
    with multiprocessing.Manager() as manager:
        out = manager.dict({"tmp": str(tmp_path)})
        p = multiprocessing.Process(target=_child, args=(out,))
        p.start()
        p.join(30)
        result = dict(out)
    assert result["cwd"].startswith(str(tmp_path)) and result["cuda"] == ""
    assert result["secret"] is None and result["nproc"] == 0 and result["fork"] == "blocked"
    assert list(tmp_path.iterdir()) == []  # cartella dell'esecuzione rimossa
