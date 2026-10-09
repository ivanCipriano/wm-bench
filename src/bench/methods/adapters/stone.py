"""Adapter di STONE (SPEC §9.3; audit ``docs/audit/stone.md``)."""

from __future__ import annotations

from typing import Any, ClassVar

from bench.domain.enums import MethodFamily
from bench.domain.errors import ConfigError
from bench.methods.base import Detector, PromptEmbedder
from bench.registry import METHODS

# Valori fissi come in run.py del repository (audit §3).
FIXED_NATIVE: dict[str, Any] = {
    "skipping_rule": "all_pl",
    "watermark_on_pl": "False",
    "prefix_length": 0,
    "z_threshold": 10.0,  # soglia nativa: solo diagnostica (D2)
    "vocab_size": None,  # None -> len(tokenizer) del generatore (audit §7, D15)
}


@METHODS.register("stone")
class StoneAdapter(PromptEmbedder, Detector):
    """STONE: logit processor sui soli token non sintattici.

    La rilevazione usa solo il tokenizer, ma deve girare su CUDA come l'inserimento: la green
    list è una ``torch.randperm`` con un ``torch.Generator`` sul dispositivo, e la permutazione
    su CPU è diversa (audit §2).
    """

    name: ClassVar[str] = "stone"
    family: ClassVar[MethodFamily] = MethodFamily.LOGIT
    gpu_for_embed: ClassVar[bool] = True
    gpu_for_detect: ClassVar[bool] = True  # dispositivo del generatore casuale, non il modello
    # z-score con il denominatore del paper (D16): misura secondaria.
    secondary_scores: ClassVar[dict[str, str]] = {"z_nonsyntax": "z_nonsyntax"}

    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        """γ → ``gamma``, δ → ``delta`` più i valori fissi; la chiave va in ``request.key``."""
        unknown = sorted(set(hp) - {"gamma", "delta"})
        if unknown:
            raise ConfigError(f"stone: unknown hyperparameters {unknown}")
        try:
            gamma, delta = float(hp["gamma"]), float(hp["delta"])
        except KeyError as exc:
            raise ConfigError(f"stone: missing hyperparameter {exc}") from exc
        if not 0.0 < gamma < 1.0:
            raise ConfigError(f"stone: gamma must be in (0, 1), got {gamma}")
        return {"gamma": gamma, "delta": delta, **FIXED_NATIVE}
