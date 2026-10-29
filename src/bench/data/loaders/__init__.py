"""Loader dei dataset; l'import di questo pacchetto popola ``bench.registry.LOADERS``.

Gli import sono espliciti (niente scoperta dal filesystem, SPEC §7.1).
"""

from bench.data.loaders import (
    classeval,
    codenet,
    humanevalpack,
    humanevalplus,
    mbpp_original,
    mbppplus,
)

__all__ = ["classeval", "codenet", "humanevalpack", "humanevalplus", "mbpp_original", "mbppplus"]
