"""Adapter concreti; l'import di questo pacchetto popola ``bench.registry.METHODS``.

Import espliciti (SPEC §7.1). STONE (Milestone 5); SWEET, MCGMark, PromptMark, ACW (Milestone 6);
``mcss`` e ``multimark`` (PIPELINE_metodo.md di multimark, P2).
"""

from bench.methods.adapters import acw, mcgmark, mcss, multimark, promptmark, stone, sweet

__all__ = ["acw", "mcgmark", "mcss", "multimark", "promptmark", "stone", "sweet"]
