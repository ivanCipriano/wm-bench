"""Fasi concrete; l'import di questo pacchetto popola ``bench.registry.STAGES``.

Gli import sono espliciti (niente scoperta dal filesystem, SPEC §7.1).
"""

from bench.pipeline.stages import (
    attack,
    attack_pools,
    calibrate,
    detect,
    evalplus_groundtruth,
    execute,
    freeze,
    generate_baseline,
    metrics,
    prepare_data,
    promptmark_freq,
    selftest,
    tune,
    watermark,
)

__all__ = [
    "attack",
    "attack_pools",
    "calibrate",
    "detect",
    "evalplus_groundtruth",
    "execute",
    "freeze",
    "generate_baseline",
    "metrics",
    "prepare_data",
    "promptmark_freq",
    "selftest",
    "tune",
    "watermark",
]
