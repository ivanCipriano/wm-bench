"""Loader dei dataset; l'import di questo pacchetto popola ``bench.registry.LOADERS``.

Gli import sono espliciti (niente scoperta dal filesystem, SPEC §7.1).
"""

from bench.data.loaders import humanevalpack, humanevalplus, mbpp_original, mbppplus

__all__ = ["humanevalpack", "humanevalplus", "mbpp_original", "mbppplus"]
