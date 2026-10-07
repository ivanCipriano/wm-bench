"""Adapter di PromptMark (SPEC §9.4; audit ``docs/audit/promptmark.md``)."""

from __future__ import annotations

from typing import Any, ClassVar

from bench_contracts import WorkerResult

from bench.data.promptmark_freq import LetterFrequencies
from bench.domain.enums import MethodFamily
from bench.domain.errors import ConfigError
from bench.methods.base import Detector, PromptEmbedder
from bench.registry import METHODS

PROTOCOL = ("iter_cap", "z_threshold", "green_size")
# Limiti della dimensione della green list nel codice (``get_red_green_sets``): la dimensione è
# derivata dalla chiave in [8, 18] se ``green_size`` è nullo, altrimenti fissata (patch 0002).
G_MIN, G_MAX = 8, 18
CANDIDATES = 18  # pool delle iniziali più frequenti (``top_n`` del codice)


@METHODS.register("promptmark")
class PromptMarkAdapter(PromptEmbedder, Detector):
    """PromptMark (expI): istruzioni nel prompt e ciclo di feedback con lo stesso modello."""

    name: ClassVar[str] = "promptmark"
    family: ClassVar[MethodFamily] = MethodFamily.PROMPT
    gpu_for_embed: ClassVar[bool] = True
    gpu_for_detect: ClassVar[bool] = False  # solo AST e lista di frequenza
    one_sample_per_item: ClassVar[bool] = True  # un ciclo di feedback per campione (audit §10)

    _frequencies: LetterFrequencies | None = None

    def frequencies(self) -> LetterFrequencies:
        """Lista di frequenza del framework (fase ``promptmark_freq``, D8)."""
        if self._frequencies is None:
            from bench.pipeline.stages.promptmark_freq import promptmark_freq_ref
            from bench.store.artifact_store import ArtifactStore

            store = ArtifactStore(self.cfg.paths.artifacts)
            ref = promptmark_freq_ref()
            if not store.exists(ref):
                raise ConfigError(f"{ref.path} not found: run the promptmark_freq stage")
            self._frequencies = store.read_model(ref, LetterFrequencies)
        return self._frequencies

    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        """Iterazioni, soglia del ciclo e dimensione della green list (audit §3)."""
        unknown = sorted(set(hp) - set(PROTOCOL))
        if unknown:
            raise ConfigError(f"promptmark: unknown hyperparameters {unknown}")
        missing = [k for k in PROTOCOL if k not in hp]
        if missing:
            raise ConfigError(f"promptmark: missing hyperparameters {missing}")
        iter_cap = int(hp["iter_cap"])
        if iter_cap < 1:
            raise ConfigError(f"promptmark: iter_cap must be >= 1, got {iter_cap}")
        size = hp["green_size"]
        if size is None:
            g_min, g_max = G_MIN, G_MAX
        else:
            g_min = g_max = int(size)
            if not 1 <= g_min <= CANDIDATES:
                raise ConfigError(f"promptmark: green_size must be in [1, {CANDIDATES}]")
        freqs = self.frequencies()
        return {
            "iter_cap": iter_cap,
            "z_threshold": float(hp["z_threshold"]),
            "g_min": g_min,
            "g_max": g_max,
            "letter_freqs": dict(freqs.letter_freqs),
            "total_identifiers": freqs.total_identifiers,
        }

    def embed_metrics(self, result: WorkerResult | None) -> dict[str, Any]:
        """Siti idonei = identificatori scelti liberamente dal modello (audit §10)."""
        metrics = super().embed_metrics(result)
        extra = result.extra if result is not None else {}
        metrics["n_sites"] = extra.get("n_free_identifiers")
        return metrics
