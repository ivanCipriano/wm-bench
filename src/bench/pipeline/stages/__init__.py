"""Fasi concrete; l'import di questo pacchetto popola ``bench.registry.STAGES``.

Gli import sono espliciti (niente scoperta dal filesystem, SPEC §7.1).
"""

from bench.pipeline.stages import selftest

__all__ = ["selftest"]
