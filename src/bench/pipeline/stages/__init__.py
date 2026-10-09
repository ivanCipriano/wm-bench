"""Fasi concrete; l'import di questo pacchetto popola ``bench.registry.STAGES``.

Gli import sono espliciti (niente scoperta dal filesystem, SPEC §7.1).
"""

from bench.pipeline.stages import (
    calibrate,
    detect,
    evalplus_groundtruth,
    execute,
    generate_baseline,
    metrics,
    prepare_data,
    promptmark_freq,
    selftest,
    watermark,
)

__all__ = [
    "calibrate",
    "detect",
    "evalplus_groundtruth",
    "execute",
    "generate_baseline",
    "metrics",
    "prepare_data",
    "promptmark_freq",
    "selftest",
    "watermark",
]
