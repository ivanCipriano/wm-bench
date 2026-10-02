"""Test degli identificatori canonici (SPEC §5.3)."""

from __future__ import annotations

import hashlib
import json

import pytest
import xxhash

from bench.domain.ids import (
    SAMPLE_ID_FIELDS,
    canonical_json,
    config_hash,
    derive_seed,
    problem_key,
    sample_id,
)
from tests.conftest import SEED_VECTORS


def test_sample_id_matches_spec_concatenation() -> None:
    expected_payload = "llm_watermarked|humaneval/3|python|qwen25_coder_7b|stone|abc123|k1|2|||"
    sid = sample_id(
        source="llm_watermarked",
        problem_key="humaneval/3",
        language="python",
        model_id="qwen25_coder_7b",
        method="stone",
        config_hash="abc123",
        key_id="k1",
        sample_index=2,
    )
    assert sid == xxhash.xxh3_128_hexdigest(expected_payload.encode())
    assert len(sid) == 32


def test_sample_id_field_order_is_the_spec_one() -> None:
    assert SAMPLE_ID_FIELDS == (
        "source",
        "problem_key",
        "language",
        "model_id",
        "method",
        "config_hash",
        "key_id",
        "sample_index",
        "parent_id",
        "attack_id",
        "attack_params_hash",
    )


def test_sample_id_missing_fields_are_empty_strings() -> None:
    a = sample_id(source="human", problem_key="mbpp/1", language="python")
    b = sample_id(source="human", problem_key="mbpp/1", language="python", model_id="")
    assert a == b


def test_sample_id_distinguishes_fields() -> None:
    base = {"source": "attacked", "problem_key": "mbpp/1", "language": "python"}
    assert sample_id(**base, attack_id="T1.1") != sample_id(**base, parent_id="T1.1")
    assert sample_id(**base, sample_index=0) != sample_id(**base, sample_index=1)


def test_config_hash_is_12_hex_and_order_independent() -> None:
    h1 = config_hash({"gamma": 0.5, "delta": 2.0}, "abc")
    h2 = config_hash({"delta": 2.0, "gamma": 0.5}, "abc")
    assert h1 == h2
    assert len(h1) == 12
    int(h1, 16)


def test_config_hash_depends_on_commit_and_contract() -> None:
    hp = {"gamma": 0.5}
    assert config_hash(hp, "abc") != config_hash(hp, "abd")
    assert config_hash(hp, "abc", contract_version="1.0") != config_hash(hp, "abc", "2.0")


def test_config_hash_definition() -> None:
    payload = json.dumps(
        {"contract_version": "1.0", "hparams": {"g": 1}, "submodule_commit": "c"},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert config_hash({"g": 1}, "c", "1.0") == hashlib.sha256(payload.encode()).hexdigest()[:12]


def test_canonical_json_rejects_nan() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_problem_key() -> None:
    assert problem_key("humaneval", 42) == "humaneval/42"
    with pytest.raises(ValueError):
        problem_key("a/b", 1)


@pytest.mark.parametrize(("parts", "expected"), SEED_VECTORS)
def test_derive_seed_is_the_contracts_one(parts: list[object], expected: int) -> None:
    assert derive_seed(*parts) == expected
