"""Generazione reale su GPU (marker ``gpu``): configurazione neutra e riproducibilità del seme.

Da lanciare su un nodo gpuq. Salta senza CUDA, a meno che ``WMB_REQUIRE_GPU=1``.
"""

from __future__ import annotations

import os

import pytest

from bench.config.schema import DecodingConfig, ExperimentConfig
from bench.data.loaders.humanevalplus import HumanEvalPlusLoader
from bench.domain.enums import Language
from bench.generation.code_extractor import FencedCodeExtractor
from bench.generation.hf_generator import HFTextGenerator, generation_seed
from bench.generation.prompt_builder import PromptBuilder
from tests.integration.conftest import require

pytestmark = pytest.mark.gpu


def test_qwen_neutral_config_and_seed(data_cfg: ExperimentConfig) -> None:
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        if os.environ.get("WMB_REQUIRE_GPU") == "1":
            pytest.fail("CUDA not available")
        pytest.skip("CUDA not available")
    spec = data_cfg.models_catalog["qwen25_coder_7b"]
    require(spec.path, "Qwen snapshot")
    problems = HumanEvalPlusLoader(data_cfg.datasets["humanevalplus"]).load_problems(
        Language.PYTHON
    )[:2]
    prompts = PromptBuilder.from_config(data_cfg.prompt)
    decoding = DecodingConfig(temperature=0.2, top_p=0.95, max_new_tokens=64, n=2)
    gen = HFTextGenerator(spec, decoding)

    assert gen.model.generation_config is gen.generation_config
    eff = gen.info()["generation_config"]
    assert eff["top_k"] == 0 and eff["repetition_penalty"] == 1.0 and eff["do_sample"] is True
    assert (eff["temperature"], eff["top_p"], eff["max_new_tokens"]) == (0.2, 0.95, 64)

    for problem in problems:
        seed = generation_seed(data_cfg.global_seed, spec.model_id, problem.problem_key, "python")
        messages = prompts.build(problem)
        first = gen.generate(messages, 2, seed)
        second = gen.generate(messages, 2, seed)
        assert len(first) == 2 and all(first)
        # Stesso seme sulla stessa GPU: stesso output (SPEC §18; non garantito tra GPU diverse).
        assert first == second
        assert any(FencedCodeExtractor().extract(text, problem)[1] for text in first)
