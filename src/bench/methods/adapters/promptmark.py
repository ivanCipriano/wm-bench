"""Adapter di PromptMark (SPEC §9.4; audit ``docs/audit/promptmark.md``)."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from bench_contracts import WorkerResult, iter_jsonl
from bench_contracts.enums import EmbedStatus
from scipy.stats import norm

from bench.config.schema import ModelSpec
from bench.data.promptmark_freq import LetterFrequencies
from bench.domain.enums import MethodFamily
from bench.domain.errors import ConfigError
from bench.domain.models import Problem
from bench.methods.base import Detector, EmbedRun, PromptEmbedder
from bench.registry import METHODS

PROTOCOL = ("iter_cap", "z_threshold", "green_size")
# Limiti della dimensione della green list nel codice (``get_red_green_sets``): la dimensione è
# derivata dalla chiave in [8, 18] se ``green_size`` è nullo, altrimenti fissata (patch 0002).
G_MIN, G_MAX = 8, 18
CANDIDATES = 18  # pool delle iniziali più frequenti (``top_n`` del codice)
# Dataset su cui il ciclo di correttezza è spento (``test_list`` vuota, D27): su CodeNet gli
# esempi del testo coincidono con l'unico caso di I/O di Pass@1 e il codice del metodo esegue solo
# asserzioni su funzioni.
NO_CORRECTNESS_LOOP = frozenset({"codenet"})


def replay_selection(
    iterations: Sequence[dict[str, Any]], iter_cap: int, z_threshold: float
) -> tuple[int, int]:
    """(indice della candidata scelta, iterazioni eseguite) come ``run_phase1`` (ADR-009).

    ``iterations`` è la traiettoria con soglia irraggiungibile. Stop alla prima iterazione
    t < ``iter_cap`` con codice corretto e ``p_exact < norm.sf(z_threshold)``; altrimenti la
    candidata ``best``: la prima iterazione corretta, se c'è, se no la prima
    (``exp_iterative_wm.py:207-284``).
    """
    threshold = float(norm.sf(z_threshold))
    best: int | None = None
    steps = min(int(iter_cap), len(iterations))
    for t in range(steps):
        it = iterations[t]
        if it["correctness"] and float(it["p_exact"]) < threshold:
            return t, t + 1
        if best is None or (it["correctness"] and not iterations[best]["correctness"]):
            best = t
    return (best if best is not None else 0), steps


def replay_result(result: WorkerResult, iter_cap: int, z_threshold: float) -> WorkerResult:
    """Risultato del worker per (``iter_cap``, η) ricavato da quello della traiettoria.

    Ricalcola i campi dello shim che dipendono dalla scelta: risposta (``raw_output``),
    iterazioni eseguite e ripetizioni, iterazione e codice scelti, esito del watermark, siti
    idonei, semi e iterazioni registrate.
    """
    extra = dict(result.extra or {})
    trajectory = list(extra.get("iterations") or [])
    if result.status != EmbedStatus.OK or not trajectory:
        return result
    chosen, steps = replay_selection(trajectory, iter_cap, z_threshold)
    threshold = float(norm.sf(z_threshold))
    used = [{**it, "meets_z": float(it["p_exact"]) < threshold} for it in trajectory[:steps]]
    selected = used[chosen]
    extra.update(
        {
            "n_iterations": steps,
            "n_retries_correctness": sum(1 for it in used[:-1] if not it["correctness"]),
            "n_retries_watermark": sum(
                1 for it in used[:-1] if it["correctness"] and not it["meets_z"]
            ),
            "selected_iteration": selected.get("iteration", chosen),
            "selected_code": selected.get("code", ""),
            "watermarked": bool(selected["meets_z"]),
            "n_free_identifiers": selected.get("n_free_identifiers"),
            "seeds": list(extra.get("seeds") or [])[:steps],
            "iterations": used,
            "replay": {"iter_cap": int(iter_cap), "z_threshold": float(z_threshold)},
        }
    )
    return dataclasses.replace(result, raw_output=selected.get("response", ""), extra=extra)


@METHODS.register("promptmark")
class PromptMarkAdapter(PromptEmbedder, Detector):
    """PromptMark (expI): istruzioni nel prompt e ciclo di feedback con lo stesso modello."""

    name: ClassVar[str] = "promptmark"
    family: ClassVar[MethodFamily] = MethodFamily.PROMPT
    gpu_for_embed: ClassVar[bool] = True
    gpu_for_detect: ClassVar[bool] = False  # solo AST e lista di frequenza
    # p adattivo del paper (Eq. 5), audit §6: misura secondaria, come −log10 p.
    secondary_scores: ClassVar[dict[str, str]] = {"p_adaptive_paper": "p_adaptive_paper"}
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

    def request_hparams(self, problems: Sequence[Problem]) -> dict[str, Any]:
        """Ciclo di correttezza spento sui dataset di ``NO_CORRECTNESS_LOOP`` (D27).

        Su L1 la richiesta resta identica a quella della M7 e della M8 (nessun parametro in più).

        Raises:
            ConfigError: se i problemi mescolano dataset con e senza ciclo.
        """
        off = {p.dataset in NO_CORRECTNESS_LOOP for p in problems}
        if off == {True, False}:
            raise ConfigError("promptmark: problems mix datasets with and without correctness loop")
        return {"correctness_loop": False} if off == {True} else {}

    def embed_metrics(self, result: WorkerResult | None) -> dict[str, Any]:
        """Siti idonei = identificatori scelti liberamente dal modello (audit §10)."""
        metrics = super().embed_metrics(result)
        extra = result.extra if result is not None else {}
        metrics["n_sites"] = extra.get("n_free_identifiers")
        return metrics

    def embed_from_trajectory(
        self,
        problems: Sequence[Problem],
        hp: dict[str, Any],
        model: ModelSpec,
        key_id: str,
        n: int,
        trajectory_dir: Path,
    ) -> EmbedRun:
        """Campioni di un candidato ricostruiti dai risultati della traiettoria (ADR-009).

        Nessuna generazione: si leggono i risultati del worker della traiettoria
        (``results.jsonl`` nella sua cartella di lavoro) e si sceglie, per ogni campione,
        l'iterazione dove il ciclo con (``iter_cap``, η) si sarebbe fermato.

        Raises:
            ConfigError: se mancano i risultati della traiettoria.
        """
        path = trajectory_dir / "results.jsonl"
        if not path.is_file():
            raise ConfigError(f"{path} not found: run the trajectory first (hpo.part=trajectories)")
        rows = [WorkerResult.from_dict(row) for row in iter_jsonl(path)]
        # La traiettoria deve avere lo stesso ciclo di correttezza del candidato (D27).
        loop = self.request_hparams(problems).get("correctness_loop", True)
        wrong = [
            r.item_id
            for r in rows
            if r.status == EmbedStatus.OK and (r.extra or {}).get("correctness_loop", True) != loop
        ]
        if wrong:
            raise ConfigError(
                f"trajectory {trajectory_dir}: correctness loop differs from the candidate's "
                f"({len(wrong)} sample(s), e.g. {wrong[0]})"
            )
        results: dict[tuple[str, int], WorkerResult] = {}
        for row in rows:  # in caso di righe ripetute vale l'ultima, come WorkerClient.collect
            problem_key, _, index = row.item_id.rpartition("#")
            results[(problem_key, int(index))] = replay_result(
                row, int(hp["iter_cap"]), float(hp["z_threshold"])
            )
        supported = {p.problem_key for p in problems if self.supports(p.language)}
        missing = [(k, i) for k in sorted(supported) for i in range(n) if (k, i) not in results]
        if missing:
            raise ConfigError(f"trajectory {trajectory_dir} lacks {len(missing)} sample(s)")
        samples, metrics = self.samples_from_results(problems, results, hp, model, key_id, n)
        return EmbedRun(
            samples=samples,
            worker=None,
            native_hparams={
                **self.to_native_hparams(hp),
                **self.request_hparams([p for p in problems if self.supports(p.language)]),
            },
            request=None,
            metrics=metrics,
            replayed_from=trajectory_dir.parent.name,
        )

    def secondary_score(self, name: str, extra: dict[str, Any]) -> float | None:
        """−log10 del p adattivo (come il punteggio principale); p = 0 → +inf."""
        p = extra.get(self.secondary_scores[name])
        if p is None:
            return None
        return math.inf if float(p) <= 0.0 else -math.log10(float(p))
