"""Proprietà degli identificatori (Hypothesis)."""

from __future__ import annotations

import random

from hypothesis import given
from hypothesis import strategies as st

from bench.domain.ids import config_hash, sample_id

json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.text(max_size=10),
    st.floats(allow_nan=False, allow_infinity=False),
)
hparams = st.dictionaries(st.text(min_size=1, max_size=8), json_scalars, max_size=6)


@given(hparams, st.text(min_size=1, max_size=40), st.integers())
def test_config_hash_invariant_to_key_order(hp: dict[str, object], commit: str, seed: int) -> None:
    items = list(hp.items())
    random.Random(seed).shuffle(items)
    assert config_hash(hp, commit) == config_hash(dict(items), commit)


@given(
    st.text(max_size=20),
    st.text(max_size=20),
    st.sampled_from(["python", "java", "cpp", "javascript"]),
    st.one_of(st.none(), st.integers(min_value=0, max_value=5)),
)
def test_sample_id_is_deterministic(
    source: str, key: str, language: str, index: int | None
) -> None:
    a = sample_id(source=source, problem_key=key, language=language, sample_index=index)
    b = sample_id(source=source, problem_key=key, language=language, sample_index=index)
    assert a == b
    assert len(a) == 32


@given(st.integers(min_value=0, max_value=1000), st.integers(min_value=0, max_value=1000))
def test_sample_index_changes_id(i: int, j: int) -> None:
    a = sample_id(source="s", problem_key="k", language="python", sample_index=i)
    b = sample_id(source="s", problem_key="k", language="python", sample_index=j)
    assert (a == b) == (i == j)
