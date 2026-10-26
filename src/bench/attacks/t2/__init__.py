"""Attacchi T2: refactoring a regole (T2.1), troncamento (T2.4) e con LLM (T2.2 riscrittura,
T2.3 traduzione andata e ritorno); l'import popola ``bench.registry.ATTACKS`` (import
espliciti, SPEC §7.1)."""

from bench.attacks.t2 import llm_rewrite, refactor, roundtrip, truncate

__all__ = ["llm_rewrite", "refactor", "roundtrip", "truncate"]
