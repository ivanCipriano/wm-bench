"""Test del registro dei componenti (SPEC §7.1)."""

from __future__ import annotations

import pytest

from bench.domain.errors import ConfigError
from bench.registry import STAGES, Registry


class Base:
    pass


def test_register_and_get() -> None:
    reg: Registry[Base] = Registry("thing")

    @reg.register("a")
    class A(Base):
        pass

    assert reg.get("a") is A
    assert "a" in reg
    assert reg.names() == ["a"]


def test_duplicate_name_rejected() -> None:
    reg: Registry[Base] = Registry("thing")
    reg.register("a")(Base)
    with pytest.raises(ConfigError, match="already registered"):
        reg.register("a")(Base)


def test_unknown_name_lists_available() -> None:
    reg: Registry[Base] = Registry("thing")
    reg.register("b")(Base)
    reg.register("a")(Base)
    with pytest.raises(ConfigError, match=r"unknown thing 'zzz'; available: a, b"):
        reg.get("zzz")


def test_stages_registry_populated_by_explicit_import() -> None:
    import bench.pipeline.stages  # noqa: F401

    assert "selftest" in STAGES
