"""Shim di ACW (SPEC §9.2; audit ``docs/audit/acw.md``). Compatibile con Python 3.9.

Gira nell'ambiente ``acw`` (solo CPU) con ``PYTHONPATH`` = ``shims/`` + ``build/patched/acw/source``
(patch 0000). Usa ``refactor.WatermarkInjector`` così com'è: seme = chiave del framework,
``num_transforms`` dagli iperparametri, ``random_rules=True`` come la CLI. Le regole 1-35 sono di
Sourcery (una chiamata ``sourcery review --fix`` per cartella), le 36-45 regole proprie (libcst,
autopep8, tabulazioni).

Sourcery lavora su cartelle e ogni chiamata costa secondi: lo shim elabora gli item **a lotti**
(``batch_size``), un file per item in una cartella di lavoro. Le applicazioni di un lotto (una per
regola selezionata, più quella congiunta) lavorano ciascuna su una propria copia della cartella e
sono indipendenti: girano **in parallelo** su ``RULE_WORKERS`` thread (ogni chiamata a Sourcery è
un processo a sé, con un file di configurazione dal nome univoco). Il risultato è lo stesso
dell'esecuzione in sequenza; il canarino resta in sequenza, dopo il lotto.

- **embed** (un item per campione della baseline): applicazione congiunta delle regole selezionate
  (``apply_specific_rules`` nell'ordine dell'injector); ``OK`` se il codice cambia, ``FAILED``
  se resta invariato. In ``extra`` le regole che modificano il codice originale applicate da
  sole (siti idonei, ``n_applicable``) e le chiamate a Sourcery.
- **detect**: per ogni regola selezionata, riapplicazione **singola** a una copia e confronto per
  hash (identificazione per trasformazione del paper, §III-B); punteggio = frazione delle regole
  che lasciano il codice invariato. Decisione nativa = riapplicazione congiunta del codice
  (``run_single_folder``), solo diagnostica (D2). In ``extra`` l'esito di ogni regola.

All'avvio: login a Sourcery con ``SOURCERY_TOKEN`` (segreto del worker, mai scritto su file dallo
shim) e un controllo su un file canarino: il codice ignora gli errori di Sourcery, quindi senza
login o rete le regole 1-35 non si applicherebbero in silenzio. Il canarino si ripete **dopo ogni
lotto**: se il token scade a metà lavoro il lotto non si scrive e il worker si ferma
(``FatalShimError``, exit code 2); con un token nuovo un nuovo invio riprende dagli item scritti.

**Limite di tempo** (D42): su alcuni codici molto lunghi Sourcery impiega più di mezz'ora per
chiamata e un lotto non finirebbe mai. Ogni lotto gira in un processo figlio (gruppo di processi
proprio, cartella di lavoro propria) con ``BATCH_TIMEOUT_S``; se scade, il gruppo (Sourcery
compreso) si uccide e il lotto si rifà **campione per campione** con ``ITEM_TIMEOUT_S``. Un campione
che sfora anche da solo è ``FAILED`` con errore ``sourcery timeout`` (``extra.sourcery_timeout``).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import io
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Callable, Dict, List, Sequence, Tuple

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import DetectStatus, EmbedStatus

from bench_shims._common.base import FatalShimError, ShimBase
from bench_shims._common.runner import main

BATCH_SIZE = 50
THREADS = 5  # thread delle regole proprie (``batch`` del codice, default 5)
# Applicazioni in parallelo dentro un lotto (job CPU con 16 core, configs/cluster/slurm_cpu.yaml).
# Costante: il worker riceve un ambiente ripulito (worker_client.PASSTHROUGH).
RULE_WORKERS = 8
# Limiti di tempo (D42): un lotto normale di 50 campioni richiede 1-2 minuti.
BATCH_TIMEOUT_S = 20 * 60
ITEM_TIMEOUT_S = 10 * 60
STALE_S = 6 * 3600  # cartelle di lavoro lasciate da worker uccisi (limite del worker: 5 h)
TIMEOUT_ERROR = "sourcery timeout"
SOURCERY_RULES = range(1, 36)
# File canarino per il controllo di partenza: casi tipici di tre regole Sourcery (la 11 ha
# modificato codice anche nell'oracle); basta che uno cambi. ``remove-unnecessary-else`` (1) non
# si usa: nell'oracle non ha mai trovato nulla da fare, neanche nel percorso diretto.
CANARIES = {
    11: "def canary(value):\n    result = value + 1\n    return result\n",  # inline-...-variable
    4: (  # list-comprehension
        "def canary(items):\n    result = []\n    for item in items:\n"
        "        result.append(item * 2)\n    return result\n"
    ),
    7: (  # sum-comprehension
        "def canary(items):\n    total = 0\n    for item in items:\n"
        "        total += item\n    return total\n"
    ),
}


def run_with_timeout(func: Callable[[], Any], timeout_s: float) -> Tuple[bool, Any]:
    """Esegue ``func`` in un processo figlio (fork) con un gruppo di processi proprio.

    Restituisce ``(True, risultato)`` o ``(False, None)`` se il tempo scade: in quel caso il gruppo
    intero (anche i processi di Sourcery lanciati dalla shell) riceve ``SIGKILL``.

    Raises:
        RuntimeError: se ``func`` solleva un'eccezione nel figlio (con il traceback).
    """
    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)

    def target() -> None:
        os.setpgrp()
        try:
            child.send((True, func()))
        except BaseException:  # il genitore rilancia con il traceback
            child.send((False, traceback.format_exc()))
        finally:
            child.close()
            os._exit(0)

    proc = ctx.Process(target=target)
    proc.start()
    child.close()
    try:
        if parent.poll(timeout_s):
            ok, value = parent.recv()
            proc.join()
            if not ok:
                raise RuntimeError(f"batch failed in child process:\n{value}")
            return True, value
        return False, None
    except EOFError:  # figlio morto senza risposta
        proc.join()
        raise RuntimeError(f"child process died (exit {proc.exitcode})") from None
    finally:
        if proc.is_alive():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.join()
        parent.close()


def file_hash(path: str) -> str:
    """SHA-256 del file, come ``ExperimentEvaluator.calculate_hash``."""
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class AcwShim(ShimBase):
    """Inserimento e rilevazione con l'implementazione di ACW, a lotti."""

    method = "acw"
    batch_size = BATCH_SIZE

    def setup(self, request: WorkerRequest) -> None:
        self.request = request
        hp = dict(request.hparams)
        run_dir = os.path.dirname(os.path.abspath(request.output_path))
        self.workdir = os.path.join(run_dir, "acw_work")
        os.makedirs(self.workdir, exist_ok=True)
        self._clean_stale()
        os.chdir(self.workdir)  # il codice scrive qui i file .sourcery_temp_*.yaml
        # Il codice chiama ``sourcery`` e ``autopep8`` dalla shell: gli eseguibili stanno nella
        # cartella dell'interprete dell'ambiente ``acw``, che il PATH ripulito non contiene.
        bin_dir = os.path.dirname(sys.executable)
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        with contextlib.redirect_stdout(io.StringIO()):
            import refactor

        self.refactor = refactor
        self.injector = refactor.WatermarkInjector(
            num_transforms=int(hp["num_transforms"]),
            seed=int(request.key),
            random_rules=bool(hp["random_rules"]),
            batch=THREADS,
        )
        self.rules: List[int] = list(self.injector.selected_rules)
        self.uses_sourcery = any(r in SOURCERY_RULES for r in self.rules)
        self._login()
        if self.uses_sourcery:
            self._canary()

    def _clean_stale(self) -> None:
        """Toglie le cartelle di lavoro lasciate da worker uccisi (più vecchie di ``STALE_S``)."""
        now = time.time()
        for name in os.listdir(self.workdir):
            path = os.path.join(self.workdir, name)
            if os.path.isdir(path) and now - os.path.getmtime(path) > STALE_S:
                shutil.rmtree(path, ignore_errors=True)

    # ------------------------------------------------------------------ limite di tempo
    def _guarded(
        self,
        items: List[WorkerItem],
        core: Callable[[List[WorkerItem]], Tuple[List[WorkerResult], int]],
        timeout_result: Callable[[WorkerItem, Dict[str, Any]], WorkerResult],
    ) -> Tuple[List[WorkerResult], int]:
        """Lotto con il limite di tempo; se scade, campione per campione (D42)."""
        ok, value = self._in_child(lambda: core(items), BATCH_TIMEOUT_S)
        if ok:
            return value
        outputs: List[WorkerResult] = []
        calls = 0
        for item in items:
            ok, value = self._in_child(lambda item=item: core([item]), ITEM_TIMEOUT_S)
            if ok:
                out, c = value
                calls += c
                for r in out:
                    r.extra["batch_timeout_fallback"] = True
                outputs += out
            else:
                extra = {
                    "n_rules": len(self.rules),
                    "rules": self.rules,
                    "sourcery_timeout": True,
                    "batch_timeout_fallback": True,
                    "timeout_s": ITEM_TIMEOUT_S,
                }
                outputs.append(timeout_result(item, extra))
        return outputs, calls

    def _in_child(self, func: Callable[[], Any], timeout_s: float) -> Tuple[bool, Any]:
        """``run_with_timeout`` con una cartella di lavoro propria, tolta alla fine."""
        base = tempfile.mkdtemp(prefix="attempt_", dir=self.workdir)
        workdir = self.workdir

        def run() -> Any:
            self.workdir = base  # solo nel figlio: le copie dei lotti finiscono qui
            return func()

        try:
            return run_with_timeout(run, timeout_s)
        finally:
            self.workdir = workdir
            shutil.rmtree(base, ignore_errors=True)

    @staticmethod
    def _add_calls(results: List[WorkerResult], calls: int, extra_calls: int) -> None:
        """Chiamate a Sourcery del lotto (canarino compreso) negli ``extra``."""
        total = calls + extra_calls
        for r in results:
            if "sourcery_calls_batch" in r.extra:
                r.extra["sourcery_calls_batch"] = total
                r.extra["sourcery_calls_per_sample"] = total / len(results)

    # ------------------------------------------------------------------ Sourcery
    def _login(self) -> None:
        token = os.environ.get("SOURCERY_TOKEN")
        if not token:
            return  # login già fatto nell'ambiente dell'utente: lo verifica il canarino
        proc = subprocess.run(
            ["sourcery", "login", "--token", token], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            raise RuntimeError(f"sourcery login failed (exit {proc.returncode})")

    def _canary(self) -> None:
        folder = tempfile.mkdtemp(prefix="canary_", dir=self.workdir)
        try:
            for rule, code in CANARIES.items():
                with open(os.path.join(folder, f"canary_{rule}.py"), "w", encoding="utf-8") as h:
                    h.write(code)
            self._apply(folder, list(CANARIES))  # una sola chiamata a Sourcery
            modified = []
            for rule, code in CANARIES.items():
                with open(os.path.join(folder, f"canary_{rule}.py"), encoding="utf-8") as h:
                    if h.read() != code:
                        modified.append(rule)
            if not modified:
                raise FatalShimError(
                    "Sourcery did not modify any canary file: check login, token (expired?) "
                    "and network"
                )
            self.canary_rules = modified
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _check_sourcery(self) -> int:
        """Canarino dopo un lotto: Sourcery funziona ancora? Chiamate a Sourcery fatte."""
        if not self.uses_sourcery:
            return 0
        self._canary()
        return 1

    def _apply(self, folder: str, rules: Sequence[int]) -> int:
        """Applica le regole con il codice (``apply_specific_rules``); chiamate a Sourcery fatte."""
        with contextlib.redirect_stdout(io.StringIO()):  # libcst stampa i diff
            return self._apply_raw(folder, rules)

    def _apply_raw(self, folder: str, rules: Sequence[int]) -> int:
        """Come ``_apply`` senza deviare stdout: ``redirect_stdout`` cambia ``sys.stdout`` per
        tutto il processo e non si annida fra thread, quindi lo fa una volta il chiamante."""
        self.injector.apply_specific_rules(folder, list(rules))
        return 1 if any(r in SOURCERY_RULES for r in rules) else 0

    # ------------------------------------------------------------------ cartelle
    def _batch_dir(self, items: Sequence[WorkerItem]) -> Any:
        folder = tempfile.mkdtemp(prefix="batch_", dir=self.workdir)
        names = [f"{i:05d}.py" for i in range(len(items))]
        for name, item in zip(names, items):
            with open(os.path.join(folder, name), "w", encoding="utf-8", newline="") as handle:
                handle.write(item.code or "")
        return folder, names

    def _copy(self, folder: str, tag: str) -> str:
        target = tempfile.mkdtemp(prefix=f"{tag}_", dir=self.workdir)
        shutil.rmtree(target)
        shutil.copytree(folder, target)
        return target

    def _changed(self, a: str, b: str, names: Sequence[str]) -> List[bool]:
        return [file_hash(os.path.join(a, n)) != file_hash(os.path.join(b, n)) for n in names]

    def _per_rule(self, folder: str, names: Sequence[str], joint_tag: str) -> Any:
        """Per ogni regola: quali file cambiano riapplicandola da sola; poi l'applicazione
        congiunta su una copia ``joint_tag`` (tenuta per il chiamante). Tutto in parallelo.
        Restituisce (cambiati per regola, copia congiunta, chiamate a Sourcery)."""
        copies = {rule: self._copy(folder, f"rule{rule}") for rule in self.rules}
        joint = self._copy(folder, joint_tag)
        jobs = [(copy, [rule]) for rule, copy in copies.items()] + [(joint, list(self.rules))]
        try:
            # libcst stampa i diff
            with (
                contextlib.redirect_stdout(io.StringIO()),
                concurrent.futures.ThreadPoolExecutor(max_workers=RULE_WORKERS) as pool,
            ):
                calls = sum(pool.map(lambda job: self._apply_raw(*job), jobs))
            changed = {rule: self._changed(folder, copy, names) for rule, copy in copies.items()}
        except BaseException:
            shutil.rmtree(joint, ignore_errors=True)
            raise
        finally:
            for copy in copies.values():
                shutil.rmtree(copy, ignore_errors=True)
        return changed, joint, calls

    # ------------------------------------------------------------------ inserimento
    def embed_batch(self, items: List[WorkerItem]) -> List[List[WorkerResult]]:
        def on_timeout(item: WorkerItem, extra: Dict[str, Any]) -> WorkerResult:
            return self.result(item, EmbedStatus.FAILED, error=TIMEOUT_ERROR, extra=extra)

        results, calls = self._guarded(items, self._embed_core, on_timeout)
        self._add_calls(results, calls, self._check_sourcery())  # prima di scrivere il lotto
        return [[r] for r in results]

    def _embed_core(self, items: List[WorkerItem]) -> Tuple[List[WorkerResult], int]:
        folder, names = self._batch_dir(items)
        try:
            changed, work, calls = self._per_rule(folder, names, "embed")
            modified = self._changed(folder, work, names)
            outputs: List[WorkerResult] = []
            for i, (item, name) in enumerate(zip(items, names)):
                applicable = [r for r in self.rules if changed[r][i]]
                extra = {
                    "n_rules": len(self.rules),
                    "rules": self.rules,
                    "applicable_rules": applicable,
                    "n_applicable": len(applicable),
                    "batch_size": len(items),
                    "sourcery_calls_batch": calls,
                    "sourcery_calls_per_sample": calls / len(items),
                }
                if not (item.code or "").strip():
                    result = self.result(item, EmbedStatus.FAILED, error="empty code", extra=extra)
                elif not modified[i]:
                    result = self.result(
                        item, EmbedStatus.FAILED, error="no transformation applied", extra=extra
                    )
                else:
                    with open(os.path.join(work, name), encoding="utf-8", newline="") as handle:
                        code = handle.read()
                    result = self.result(item, EmbedStatus.OK, code=code, extra=extra)
                outputs.append(result)
            shutil.rmtree(work, ignore_errors=True)
            return outputs, calls
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # ------------------------------------------------------------------ rilevazione
    def detect_batch(self, items: List[WorkerItem]) -> List[WorkerResult]:
        def on_timeout(item: WorkerItem, extra: Dict[str, Any]) -> WorkerResult:
            return self.result(item, DetectStatus.FAILED, error=TIMEOUT_ERROR, extra=extra)

        results, calls = self._guarded(items, self._detect_core, on_timeout)
        self._add_calls(results, calls, self._check_sourcery())  # prima di scrivere il lotto
        return results

    def _detect_core(self, items: List[WorkerItem]) -> Tuple[List[WorkerResult], int]:
        folder, names = self._batch_dir(items)
        try:
            changed, joint, calls = self._per_rule(folder, names, "joint")
            joint_changed = self._changed(folder, joint, names)
            shutil.rmtree(joint, ignore_errors=True)
            results: List[WorkerResult] = []
            for i, item in enumerate(items):
                unchanged = {str(r): not changed[r][i] for r in self.rules}
                n_unchanged = sum(unchanged.values())
                extra = {
                    "rule_unchanged": unchanged,
                    "n_rules": len(self.rules),
                    "n_rules_changed": len(self.rules) - n_unchanged,
                    "joint_unchanged": not joint_changed[i],
                    "batch_size": len(items),
                    "sourcery_calls_batch": calls,
                    "sourcery_calls_per_sample": calls / len(items),
                }
                if not (item.code or "").strip():
                    results.append(
                        self.result(item, DetectStatus.FAILED, error="empty code", extra=extra)
                    )
                    continue
                results.append(
                    self.result(
                        item,
                        DetectStatus.OK,
                        score=n_unchanged / len(self.rules),
                        native_decision=not joint_changed[i],
                        extra=extra,
                    )
                )
            return results, calls
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(AcwShim))
