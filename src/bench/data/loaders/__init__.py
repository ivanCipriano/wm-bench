"""Loader dei dataset; l'import di questo pacchetto popola ``bench.registry.LOADERS``.

Gli import sono espliciti (niente scoperta dal filesystem, SPEC §7.1).
"""

from bench.data.loaders import (
    classeval,
    codenet,
    codesearchnet,
    humanevalpack,
    humanevalplus,
    mbpp_original,
    mbppplus,
    thestack_cpp,
)

__all__ = [
    "classeval",
    "codenet",
    "codesearchnet",
    "humanevalpack",
    "humanevalplus",
    "mbpp_original",
    "mbppplus",
    "thestack_cpp",
]
