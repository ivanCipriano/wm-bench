"""Test di derive_seed (SPEC §18, §21.3)."""

from __future__ import annotations

from typing import Any

import pytest
from bench_contracts import derive_seed

from tests.conftest import SEED_VECTORS


@pytest.mark.parametrize(("parts", "expected"), SEED_VECTORS)
def test_derive_seed_fixed_values(parts: list[Any], expected: int) -> None:
    assert derive_seed(*parts) == expected


def test_derive_seed_range_and_determinism() -> None:
    values = [derive_seed(20261001, "split", f"mbpp/{i}") for i in range(500)]
    assert values == [derive_seed(20261001, "split", f"mbpp/{i}") for i in range(500)]
    assert all(0 <= v < 2**31 - 1 for v in values)
    assert len(set(values)) == len(values)


def test_derive_seed_depends_on_order() -> None:
    assert derive_seed("a", "b") != derive_seed("b", "a")
