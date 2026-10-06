"""Adapter concreti; l'import di questo pacchetto popola ``bench.registry.METHODS``.

Import espliciti (SPEC §7.1). STONE (Milestone 5); SWEET, MCGMark, PromptMark, ACW (Milestone 6).
"""

from bench.methods.adapters import stone, sweet

__all__ = ["stone", "sweet"]
