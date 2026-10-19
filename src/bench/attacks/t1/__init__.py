"""Attacchi T1 (trasformazioni che preservano la semantica); l'import popola
``bench.registry.ATTACKS`` (import espliciti, SPEC §7.1)."""

from bench.attacks.t1 import comments, local_edits, rename, tooling

__all__ = ["comments", "local_edits", "rename", "tooling"]
