"""Shim di PromptMark (SPEC §9.4; audit ``docs/audit/promptmark.md``). Compatibile con Python 3.9.

Gira nell'ambiente ``promptmark`` con ``PYTHONPATH`` = ``shims/`` + ``build/patched/promptmark/src``
(patch 0001 provider in-process, 0002 soglia e dimensione della green list come parametri). La
variante è expI (``src/watermarking/exp_iterative_wm.py``, ciclo iterativo con green list per
frequenza).

Configurazione del metodo (i punti di configurazione del codice, audit §3): ``SEED_KEY`` = chiave
del framework, ``Z_THRESHOLD``, ``G_MIN``/``G_MAX`` e ``max_iterations`` dagli iperparametri; la
lista di frequenza (D8) è scritta in ``<lavoro>/results/dataset/`` e la cartella di lavoro è la
directory corrente, perché il codice legge ``base_dir="."``.

- **embed** (un campione per item): record con ``prompt`` = messaggio utente del framework e
  ``test_list`` = esempi del prompt (``examples.prompt_examples``, audit §8); ``run_phase1`` con il
  provider ``inprocess_hf`` (seme del campione alla prima generazione, poi
  ``derive_seed(seme, "promptmark-iter", t)``). ``raw_output`` = risposta completa della candidata
  scelta (la regola D4 si applica a quel testo); in ``extra`` tutte le iterazioni e i conteggi.
- **detect**: ``detect_watermark("", code, ...)`` come ``evaluate_candidate``; punteggio
  ``generated_score`` = −log10 p; variante del paper (p adattivo) come misura secondaria.

Esecuzione degli esempi (audit §9): processo figlio del repository (timeout 2 s invariato) con
limiti applicati sostituendo ``shared_utils.run_code_with_tests`` con un wrapper che, nel figlio,
usa una cartella temporanea dedicata sotto ``$WMB_TMP``, ripulisce l'ambiente, nasconde la GPU e
imposta ``setrlimit`` su memoria, file, processi e CPU prima di eseguire il codice.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult, derive_seed
from bench_contracts.enums import DetectStatus, EmbedStatus

from bench_shims._common.base import ShimBase
from bench_shims._common.decoding import (
    build_generation_config,
    effective_config,
    torch_dtype,
)
from bench_shims._common.runner import main
from bench_shims.promptmark.examples import prompt_examples
from bench_shims.promptmark.sites import free_identifiers

KEEP_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
EXACT_BELOW = 30  # sotto questa soglia il paper usa il binomiale esatto (Eq. 5)
CHILD_ENV = {
    "CUDA_VISIBLE_DEVICES": "",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}
MEMORY_EXTRA = 2 * 1024**3  # 2 GB oltre lo spazio virtuale ereditato dal worker
FILE_SIZE = 64 * 1024**2
CPU_SECONDS = 10  # oltre il timeout di 2 s del repository


def _virtual_memory() -> int:
    """Spazio virtuale del processo (byte), da ``/proc/self/statm``."""
    with open("/proc/self/statm") as handle:
        pages = int(handle.read().split()[0])
    return pages * os.sysconf("SC_PAGE_SIZE")


def apply_limits() -> None:
    """Limiti del processo figlio che esegue il codice generato (audit §9)."""
    import resource

    limit = _virtual_memory() + MEMORY_EXTRA
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE, FILE_SIZE))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))  # nessun nuovo processo né thread
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))


def limited(original: Callable[..., Any], tmp_root: str) -> Callable[..., Any]:
    """Wrapper di ``run_code_with_tests`` eseguito nel processo figlio, con i limiti."""

    def run(code: str, test_imports: Any, tests: Any, return_dict: Any) -> Any:
        workdir = tempfile.mkdtemp(prefix="exec_", dir=tmp_root)
        try:
            os.chdir(workdir)
            for key in list(os.environ):
                if key not in KEEP_ENV:
                    del os.environ[key]
            os.environ.update(CHILD_ENV)
            os.environ["HOME"] = workdir
            os.environ["TMPDIR"] = workdir
            apply_limits()
            return original(code, test_imports, tests, return_dict)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return run


def adaptive_p(green: int, total: int, gamma: float) -> Optional[float]:
    """p adattivo del paper (Eq. 5): binomiale esatto se N < 30, normale altrimenti."""
    if total == 0 or not 0 < gamma < 1:
        return None
    from scipy.stats import binom, norm

    if total < EXACT_BELOW:
        return float(binom.sf(green - 1, total, gamma))
    z = (green - total * gamma) / math.sqrt(total * gamma * (1 - gamma))
    return float(norm.sf(z))


class PromptMarkShim(ShimBase):
    """Inserimento (ciclo iterativo) e rilevazione con l'implementazione di PromptMark."""

    method = "promptmark"

    def setup(self, request: WorkerRequest) -> None:
        self.request = request
        self.hp = dict(request.hparams)
        run_dir = os.path.dirname(os.path.abspath(request.output_path))
        self.workdir = os.path.join(run_dir, "promptmark_work")
        self._write_frequencies(self.workdir)
        os.chdir(self.workdir)  # il codice legge le liste da base_dir="."
        tmp = os.environ.get("WMB_TMP") or tempfile.gettempdir()
        os.makedirs(tmp, exist_ok=True)
        self.tmp_root = tempfile.mkdtemp(prefix="promptmark_", dir=tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            import llm_providers
            import shared_utils

            sys.path.insert(0, os.path.join(os.path.dirname(shared_utils.__file__), "watermarking"))
            import exp_iterative_wm as exp
        self.su, self.lp, self.exp = shared_utils, llm_providers, exp
        exp.SEED_KEY = str(request.key)
        exp.Z_THRESHOLD = float(self.hp["z_threshold"])
        exp.G_MIN = int(self.hp["g_min"])
        exp.G_MAX = int(self.hp["g_max"])
        self.iter_cap = int(self.hp["iter_cap"])
        shared_utils.run_code_with_tests = limited(shared_utils.run_code_with_tests, self.tmp_root)
        self.model = None
        self.generation_config = None
        self._seeds: List[int] = []
        self._used_seeds: List[int] = []
        if request.op == "embed":
            self._load_model(request)
        self._logged_config = False

    def _write_frequencies(self, workdir: str) -> None:
        """Lista di frequenza del framework (D8) nei file letti dal codice."""
        target = os.path.join(workdir, "results", "dataset")
        os.makedirs(target, exist_ok=True)
        lists = {
            "humaneval_letter_freqs.json": {
                "letter_freqs": self.hp["letter_freqs"],
                "total_identifiers": self.hp["total_identifiers"],
            },
            # Il codice somma due liste: la seconda resta vuota.
            "mbpp_letter_freqs.json": {"letter_freqs": {}, "total_identifiers": 0},
        }
        for name, data in lists.items():
            with open(os.path.join(target, name), "w", encoding="utf-8") as handle:
                json.dump(data, handle)

    def _load_model(self, request: WorkerRequest) -> None:
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            request.tokenizer_path, local_files_only=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            request.model_path, torch_dtype=torch_dtype(request.decoding), local_files_only=True
        ).to(request.device)
        self.model.eval()
        self.generation_config = build_generation_config(
            request.decoding, self.tokenizer, self.model
        )
        provider = self.lp.LLMProviderFactory.create(
            "inprocess_hf",
            model=self.model,
            tokenizer=self.tokenizer,
            generation_config=self.generation_config,
            system_prompt=request.system_prompt,
            device=request.device,
            seed_fn=self._next_seed,
        )
        self.su._llm_provider = provider
        self.su._current_provider_name = "inprocess_hf"

    def _next_seed(self) -> int:
        seed = self._seeds.pop(0)
        self._used_seeds.append(seed)
        return seed

    # ------------------------------------------------------------------ green list e γ
    def green_red_gamma(self) -> Any:
        exp = self.exp
        green, red, size = exp.get_red_green_sets(
            secret_key=exp.SEED_KEY, base_dir=".", g_min=exp.G_MIN, g_max=exp.G_MAX
        )
        freqs, total = exp.load_frequency_data(green, ".")
        return green, red, size, exp.calculate_gamma(freqs, total, green)

    # ------------------------------------------------------------------ inserimento
    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        if item.n != 1:
            raise ValueError("PromptMark generates one sample per item (n must be 1)")
        messages = item.prompt_messages or []
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        examples = prompt_examples(user)
        record = {
            "task_id": item.item_id,
            "prompt": user,
            "test_list": examples,
            "test_imports": [],
            "canonical_solution": "",
        }
        self._seeds = [item.seed] + [
            derive_seed(item.seed, "promptmark-iter", t) for t in range(1, self.iter_cap)
        ]
        self._used_seeds = []
        evaluations: List[Dict[str, Any]] = []
        original_evaluate = self.exp.evaluate_candidate

        def recorded(record_: Any, code: str) -> Dict[str, Any]:
            result: Dict[str, Any] = original_evaluate(record_, code)
            evaluations.append(result)  # run_phase1 aggiunge poi codice e risposta allo stesso dict
            return result

        self.exp.evaluate_candidate = recorded
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # il repository stampa molto
                selected, _ = self.exp.run_phase1(record, max_iterations=self.iter_cap)
        finally:
            self.exp.evaluate_candidate = original_evaluate
            self._clean_tmp()
        if not selected:
            return [self.result(item, EmbedStatus.FAILED, sample_index=0, error="no candidate")]
        iterations = [self._iteration(e) for e in evaluations]
        retries_wm = retries_corr = 0
        for it in iterations[:-1]:
            if not it["correctness"]:
                retries_corr += 1
            elif not it["meets_z"]:
                retries_wm += 1
        green, red, size, gamma = self.green_red_gamma()
        extra: Dict[str, Any] = {
            "seed_scheme": "per_sample",
            "n_iterations": len(iterations),
            "n_retries_watermark": retries_wm,
            "n_retries_correctness": retries_corr,
            "n_example_tests": len(examples),
            "example_tests": examples,
            "selected_iteration": selected.get("iteration"),
            "selected_code": selected.get("code", ""),
            "watermarked": bool(selected.get("meets_z")),
            # Siti idonei: identificatori scelti liberamente dal modello (audit §10).
            "n_free_identifiers": free_identifiers(
                selected.get("code", ""), user, self.su.CodeNavigator
            ),
            "seeds": list(self._used_seeds),
            "iterations": iterations,
            "green_letters": sorted(green),
            "green_size": size,
            "gamma": gamma,
        }
        if not self._logged_config:
            extra["generation_config"] = effective_config(self.generation_config)
            self._logged_config = True
        return [
            self.result(
                item,
                EmbedStatus.OK,
                sample_index=0,
                raw_output=selected.get("full_llm_response", ""),
                extra=extra,
            )
        ]

    @staticmethod
    def _iteration(e: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "iteration": e.get("iteration"),
            "correctness": bool(e.get("correctness")),
            "tests_passed": int(e.get("tests_passed", 0)),
            "tests_failed": int(e.get("tests_failed", 0)),
            "meets_z": bool(e.get("meets_z")),
            "p_exact": float(e.get("generated_p_exact", 1.0)),
            "token_count": int(e.get("generated_token_count", 0)),
            "green_count": int(e.get("generated_green_count", 0)),
            "code": e.get("code", ""),
            "response": e.get("full_llm_response", ""),
            "error": e.get("error_message", ""),
        }

    def _clean_tmp(self) -> None:
        """Cartelle temporanee rimaste da esecuzioni interrotte dal timeout."""
        for name in os.listdir(self.tmp_root):
            shutil.rmtree(os.path.join(self.tmp_root, name), ignore_errors=True)

    # ------------------------------------------------------------------ rilevazione
    def detect(self, item: WorkerItem) -> WorkerResult:
        green, red, size, gamma = self.green_red_gamma()
        code = item.code or ""
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = self.su.detect_watermark(
                    "",
                    code,
                    green,
                    red,
                    gamma,
                    comment_enabled=self.exp.COMMENT_ENABLED,
                    z_threshold=self.exp.Z_THRESHOLD,
                )
        except KeyError:
            # Errore di sintassi: nel codice il ramo d'errore non ha "unique_starts" (audit §6).
            return self.result(
                item, DetectStatus.FAILED, error="syntax error", extra={"gamma": gamma}
            )
        total = int(res["generated_token_count"])
        green_count = int(res["generated_green_count"])
        extra = {
            "token_count": total,
            "green_count": green_count,
            "p_exact": float(res["generated_p_exact"]),
            "z_score": float(res["generated_z_score"]),
            "gamma": gamma,
            "green_size": size,
            "p_adaptive_paper": adaptive_p(green_count, total, gamma),
        }
        if total == 0:
            return self.result(item, DetectStatus.FAILED, error="no identifiers", extra=extra)
        return self.result(
            item,
            DetectStatus.OK,
            score=float(res["generated_score"]),
            native_decision=bool(res["generated_is_watermarked"]),
            extra=extra,
        )


if __name__ == "__main__":
    raise SystemExit(main(PromptMarkShim))
