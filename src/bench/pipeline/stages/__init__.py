"""Fasi concrete; l'import di questo pacchetto popola ``bench.registry.STAGES``.

Gli import sono espliciti (niente scoperta dal filesystem, SPEC §7.1).
"""

from bench.pipeline.stages import (
    evalplus_groundtruth,
    execute,
    generate_baseline,
    prepare_data,
    promptmark_freq,
    selftest,
    watermark,
)

__all__ = [
    "evalplus_groundtruth",
    "execute",
    "generate_baseline",
    "prepare_data",
    "promptmark_freq",
    "selftest",
    "watermark",
]
