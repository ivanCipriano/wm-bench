"""Test della configurazione di decoding neutra (decisione dell'utente, ADR-006)."""

from __future__ import annotations

import pytest

from bench.config.schema import DecodingConfig
from bench.generation.decoding import (
    effective_config,
    neutral_generation_config,
    neutral_settings,
    special_token_ids,
)

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

L1 = DecodingConfig(temperature=0.2, top_p=0.95, max_new_tokens=512, n=6)


def test_neutral_settings() -> None:
    s = neutral_settings(L1)
    assert s["do_sample"] is True
    assert (s["temperature"], s["top_p"], s["max_new_tokens"], s["num_return_sequences"]) == (
        0.2,
        0.95,
        512,
        6,
    )
    assert s["top_k"] == 0
    assert s["repetition_penalty"] == 1.0
    assert s["no_repeat_ngram_size"] == 0
    assert s["num_beams"] == 1


def test_special_tokens() -> None:
    assert special_token_ids(2, None, [2, 7]) == ([2, 7], 2)
    assert special_token_ids(None, 0, 5) == ([5], 0)
    with pytest.raises(ValueError):
        special_token_ids(None, None, None)


def _tiny_model() -> object:
    config = transformers.LlamaConfig(
        vocab_size=64,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=128,
        bos_token_id=1,
        eos_token_id=2,
        pad_token_id=0,
    )
    torch.manual_seed(0)
    model = transformers.LlamaForCausalLM(config)
    # generation_config "alla Qwen": valori che la configurazione neutra deve ignorare.
    model.generation_config = transformers.GenerationConfig(
        do_sample=True,
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        repetition_penalty=1.1,
        eos_token_id=2,
        pad_token_id=0,
    )
    model.eval()
    return model


def test_model_generation_config_is_ignored() -> None:
    model = _tiny_model()
    gc = neutral_generation_config(L1, [2], 0)
    model.generation_config = gc  # come in HFTextGenerator
    prepared, _ = model._prepare_generation_config(gc)  # type: ignore[attr-defined]
    assert prepared.top_k == 0
    assert prepared.repetition_penalty == 1.0
    assert prepared.temperature == 0.2
    assert prepared.top_p == 0.95
    eff = effective_config(gc)
    assert eff["top_k"] == 0 and eff["repetition_penalty"] == 1.0 and eff["do_sample"] is True
    assert "transformers_version" not in eff


def test_seeded_sampling_is_reproducible() -> None:
    from bench.generation.hf_generator import sample_sequences

    model = _tiny_model()
    gc = neutral_generation_config(
        DecodingConfig(temperature=0.2, top_p=0.95, max_new_tokens=8, n=3), [2], 0
    )
    model.generation_config = gc
    ids = torch.tensor([[1, 5, 6, 7]])
    a = sample_sequences(model, ids, gc, 3, seed=11)
    b = sample_sequences(model, ids, gc, 3, seed=11)
    assert a.shape[0] == 3 and a.shape[1] <= 8
    assert torch.equal(a, b)
