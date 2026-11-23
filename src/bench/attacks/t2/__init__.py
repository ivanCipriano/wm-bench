"""Attacchi T2: refactoring a regole (T2.1), troncamento (T2.4, T2.4k in k funzioni), con LLM
(T2.2 riscrittura, T2.3 traduzione andata e ritorno) e diluizione (T2.5); l'import popola
``bench.registry.ATTACKS`` (import espliciti, SPEC §7.1)."""

from bench.attacks.t2 import dilution, llm_rewrite, refactor, roundtrip, truncate, truncate_k

__all__ = ["dilution", "llm_rewrite", "refactor", "roundtrip", "truncate", "truncate_k"]
