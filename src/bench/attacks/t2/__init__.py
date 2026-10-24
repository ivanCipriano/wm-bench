"""Attacchi T2 con LLM (T2.2 riscrittura, T2.3 traduzione andata e ritorno); l'import popola
``bench.registry.ATTACKS`` (import espliciti, SPEC §7.1)."""

from bench.attacks.t2 import llm_rewrite, roundtrip

__all__ = ["llm_rewrite", "roundtrip"]
