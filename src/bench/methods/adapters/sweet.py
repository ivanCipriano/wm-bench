"""Adapter di SWEET (SPEC §9.1; audit ``docs/audit/sweet.md``)."""

from __future__ import annotations

from typing import Any, ClassVar

from bench.domain.enums import MethodFamily
from bench.domain.errors import ConfigError
from bench.methods.base import Detector, PromptEmbedder
from bench.registry import METHODS

# Valori fissi come nel repository (audit §3).
FIXED_NATIVE: dict[str, Any] = {
    "seeding_scheme": "simple_1",  # contesto di 1 token
    "select_green_tokens": True,
    "z_threshold": 4.0,  # soglia nativa: solo diagnostica (D2)
}
PROTOCOL = ("gamma", "delta", "entropy_threshold")


@METHODS.register("sweet")
class SweetAdapter(PromptEmbedder, Detector):
    """SWEET: green list solo sui passi ad alta entropia; la rilevazione usa il modello."""

    name: ClassVar[str] = "sweet"
    family: ClassVar[MethodFamily] = MethodFamily.LOGIT
    gpu_for_embed: ClassVar[bool] = True
    gpu_for_detect: ClassVar[bool] = True  # forward del modello per l'entropia (audit §2)

    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        """γ, δ e soglia hanno gli stessi nomi nel repository; la chiave va in ``key``."""
        unknown = sorted(set(hp) - set(PROTOCOL))
        if unknown:
            raise ConfigError(f"sweet: unknown hyperparameters {unknown}")
        missing = [k for k in PROTOCOL if k not in hp]
        if missing:
            raise ConfigError(f"sweet: missing hyperparameters {missing}")
        gamma, delta, threshold = (float(hp[k]) for k in PROTOCOL)
        if not 0.0 < gamma < 1.0:
            raise ConfigError(f"sweet: gamma must be in (0, 1), got {gamma}")
        if threshold < 0:
            raise ConfigError(f"sweet: entropy_threshold must be >= 0, got {threshold}")
        return {"gamma": gamma, "delta": delta, "entropy_threshold": threshold, **FIXED_NATIVE}
