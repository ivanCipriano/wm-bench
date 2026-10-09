"""Adapter della Milestone 6 e rilevazione generica (``Detector.detect_codes``) senza GPU."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.domain.models import Problem
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.adapters.mcgmark import FIXED_NATIVE, McgmarkAdapter, message_bits
from bench.methods.adapters.stone import StoneAdapter
from bench.methods.adapters.sweet import SweetAdapter
from bench.methods.base import DetectInput, MethodAdapter
from bench.methods.worker_client import WorkerClient, WorkerRun
from tests.conftest import REPO_ROOT


class EchoClient(WorkerClient):
    """Client reale che lancia lo shim ``echo`` con l'interprete dei test."""

    def run(
        self,
        env_name: str,
        method: str,
        request: Any,
        items: Any,
        source: Any = None,
        secrets: Any = (),
    ) -> WorkerRun:  # type: ignore[override]
        self.last_items = list(items)
        return super().run("test", "echo", request, items, None, secrets)


def echo_client() -> EchoClient:
    return EchoClient(
        envs={"test": Path(sys.executable)},
        shims_root=REPO_ROOT / "shims",
        timeout_s=60,
        extra_pythonpath=[REPO_ROOT / "packages" / "bench-contracts"],
    )


@pytest.fixture(autouse=True)
def fake_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)


def test_sweet_hparams(cfg: ExperimentConfig) -> None:
    a = SweetAdapter(cfg.methods_catalog["sweet"], cfg, echo_client())
    hp = a.default_hparams()
    assert hp == {"gamma": 0.5, "delta": 0.5, "entropy_threshold": 0.5}
    assert a.to_native_hparams(hp) == {
        "gamma": 0.5,
        "delta": 0.5,
        "entropy_threshold": 0.5,
        "seeding_scheme": "simple_1",
        "select_green_tokens": True,
        "z_threshold": 4.0,
    }
    with pytest.raises(ConfigError, match="missing"):
        a.to_native_hparams({"gamma": 0.5, "delta": 0.5})
    with pytest.raises(ConfigError, match="unknown"):
        a.to_native_hparams({**hp, "hash_key": 1})
    with pytest.raises(ConfigError, match="entropy_threshold"):
        a.to_native_hparams({**hp, "entropy_threshold": -1})
    assert all(a.supports(lang) for lang in ("python", "java", "cpp", "javascript"))
    assert a.gpu_for_detect and a.source().pythonpath == a.source().root
    assert a.key("k1") != StoneAdapter(cfg.methods_catalog["stone"], cfg, echo_client()).key("k1")


def test_detect_codes_keeps_order_and_skips_unsupported_languages(
    cfg: ExperimentConfig, tmp_path: Path
) -> None:
    client = echo_client()
    a = StoneAdapter(cfg.methods_catalog["stone"], cfg, client)
    model = cfg.models_catalog["qwen25_coder_7b"]
    inputs = [
        DetectInput(
            "s/1",
            "python",
            "def f():\n    return 1\n",
            prompt_messages=[{"role": "user", "content": "x"}],
        ),
        DetectInput("s/2", "javascript", "const f = () => 1"),
        DetectInput("s/3", "cpp", "int f(){return 1;}", context_prompt="ctx"),
    ]
    results = a.detect_codes(inputs, a.default_hparams(), model, "k1", {}, tmp_path, "cpu")
    assert [r.item_id for r in results] == ["s/1", "s/2", "s/3"]
    assert [r.status for r in results] == ["OK", "NOT_APPLICABLE", "OK"]
    assert results[0].score == float(len(inputs[0].code)) and results[1].score is None
    # Solo i linguaggi supportati arrivano al worker, con il loro contesto.
    assert [it.item_id for it in client.last_items] == ["s/1", "s/3"]
    assert client.last_items[0].prompt_messages == [{"role": "user", "content": "x"}]
    assert client.last_items[1].context_prompt == "ctx"


def _problem(key: str, language: Language) -> Problem:
    return Problem(
        problem_key=key,
        dataset="humanevalplus",
        level="L1",  # type: ignore[arg-type]
        language=language,
        split="dev",  # type: ignore[arg-type]
        prompt_text="def f():\n",
        entry_point="f",
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )


def test_mcgmark_hparams_and_message(cfg: ExperimentConfig) -> None:
    a = McgmarkAdapter(cfg.methods_catalog["mcgmark"], cfg, echo_client())
    assert a.default_hparams() == {}
    assert a.to_native_hparams({}) == FIXED_NATIVE and FIXED_NATIVE["gamma"] == 0.5
    with pytest.raises(ConfigError, match="no tunable"):
        a.to_native_hparams({"delta": 2.0})
    assert a.supports("python") and not any(a.supports(x) for x in ("java", "cpp", "javascript"))
    assert a.gpu_for_detect and a.seed_scheme() == "per_sample"
    assert a.source().pythonpath == a.source().root / "Watermark"
    assert SweetAdapter(cfg.methods_catalog["sweet"], cfg, echo_client()).seed_scheme() == (
        "per_problem"
    )
    model = cfg.models_catalog["qwen25_coder_7b"]
    problem = _problem("humaneval/0", Language.PYTHON)
    messages = [a.expected_message(model, problem, i) for i in range(6)]
    assert all(m is not None and len(m) == 12 and set(m) <= {"0", "1"} for m in messages)
    assert len(set(messages)) > 1  # un messaggio per campione
    assert messages[0] == message_bits(cfg.global_seed, "humaneval/0", "python", model.model_id, 0)
    seeds = {a.sample_seed(model, problem, i) for i in range(6)}
    assert len(seeds) == 6


def test_mcgmark_embeds_one_sample_per_item(cfg: ExperimentConfig, tmp_path: Path) -> None:
    client = echo_client()
    a = McgmarkAdapter(cfg.methods_catalog["mcgmark"], cfg, client)
    model = cfg.models_catalog["qwen25_coder_7b"]
    problems = [_problem("humaneval/0", Language.PYTHON), _problem("humaneval/1", Language.JAVA)]
    run = a.embed_from_prompts(
        problems,
        {},
        model,
        "k1",
        {"num_return_sequences": 3},
        3,
        PromptBuilder.from_config(cfg.prompt),
        tmp_path,
        "cpu",
    )
    # Solo Python arriva al worker: un item per campione, n = 1, seme e messaggio propri.
    assert [it.item_id for it in client.last_items] == [f"humaneval/0#{i}" for i in range(3)]
    assert all(it.n == 1 for it in client.last_items)
    assert run.request is not None and run.request["decoding"]["num_return_sequences"] == 1
    python = [s for s in run.samples if s.problem_key == "humaneval/0"]
    java = [s for s in run.samples if s.problem_key == "humaneval/1"]
    assert [s.sample_index for s in python] == [0, 1, 2]
    assert [s.seed for s in python] == [it.seed for it in client.last_items]
    assert [s.expected_message for s in python] == [it.expected_message for it in client.last_items]
    assert len({s.seed for s in python}) == 3
    assert [str(s.embed_status) for s in java] == ["NOT_APPLICABLE"] * 3


def test_mcgmark_twin_baseline(cfg: ExperimentConfig, tmp_path: Path) -> None:
    client = echo_client()
    a = McgmarkAdapter(cfg.methods_catalog["mcgmark"], cfg, client)
    model = cfg.models_catalog["qwen25_coder_7b"]
    problems = [_problem("humaneval/0", Language.PYTHON)]
    builder = PromptBuilder.from_config(cfg.prompt)
    args = (problems, {}, model, "k1", {"num_return_sequences": 2}, 2, builder)
    marked = a.embed_from_prompts(*args, tmp_path / "wm", "cpu")
    twin = a.embed_from_prompts(*args, tmp_path / "twin", "cpu", watermark=False)
    assert marked.request is not None and twin.request is not None
    assert marked.request["hparams"]["assistant_prefill"] == "```python\n"
    assert "watermark" not in marked.request["hparams"]
    assert twin.request["hparams"]["watermark"] is False
    assert [s.seed for s in twin.samples] == [s.seed for s in marked.samples]
    for s in twin.samples:
        assert s.source == "llm_baseline" and s.method == "mcgmark" and s.key_id is None
        assert s.embed_status is None
    assert {s.sample_id for s in twin.samples}.isdisjoint(s.sample_id for s in marked.samples)
    sweet = SweetAdapter(cfg.methods_catalog["sweet"], cfg, echo_client())
    with pytest.raises(ConfigError, match="twin"):
        sweet.embed_from_prompts(*args, tmp_path / "s", "cpu", watermark=False)


def test_acw_hparams_and_post_hoc_embedding(cfg: ExperimentConfig, tmp_path: Path) -> None:
    from bench.domain.models import CodeSample
    from bench.methods.adapters.acw import AcwAdapter

    client = echo_client()
    a = AcwAdapter(cfg.methods_catalog["acw"], cfg, client)
    hp = a.default_hparams()
    assert hp == {"num_transforms": 43}
    assert a.to_native_hparams(hp) == {"num_transforms": 43, "random_rules": True}
    with pytest.raises(ConfigError, match="num_transforms"):
        a.to_native_hparams({"num_transforms": 44})
    assert a.secrets == ("SOURCERY_TOKEN",) and not a.gpu_for_embed and not a.gpu_for_detect
    assert a.supports("python") and not a.supports("java")
    assert a.source().pythonpath == a.source().root / "source"

    def parent(key: str, lang: Language, index: int) -> CodeSample:
        base: dict[str, Any] = dict.fromkeys(CodeSample.model_fields)
        base.update(
            sample_id=f"{key}-{lang}-{index}",
            problem_key=key,
            dataset="humanevalplus",
            language=lang,
            level="L1",
            split="dev",
            source="llm_baseline",
            model_id="qwen25_coder_7b",
            sample_index=index,
            seed=7,
            code="def f():\n    return 1\n",
            extraction_ok=True,
            contamination_risk=False,
        )
        return CodeSample.model_validate(base)

    parents = [parent("humaneval/0", Language.PYTHON, i) for i in range(2)]
    parents.append(parent("humaneval/0", Language.JAVA, 0))
    model = cfg.models_catalog["qwen25_coder_7b"]
    run = a.embed_from_code(parents, hp, model, "k1", tmp_path)
    assert [it.item_id for it in client.last_items] == [
        "humaneval/0-python-0",
        "humaneval/0-python-1",
    ]
    assert [s.embed_status for s in run.samples] == ["OK", "OK", "NOT_APPLICABLE"]
    assert [s.parent_id for s in run.samples] == [p.sample_id for p in parents]
    assert all(s.source == "llm_watermarked" and s.method == "acw" for s in run.samples)
    assert len({s.sample_id for s in run.samples}) == 3 and len(run.metrics) == 3
