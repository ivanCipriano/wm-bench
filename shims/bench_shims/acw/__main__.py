"""Shim di ACW (SPEC §9.2; audit ``docs/audit/acw.md``). Compatibile con Python 3.9.

Gira nell'ambiente ``acw`` (solo CPU) con ``PYTHONPATH`` = ``shims/`` + ``build/patched/acw/source``
(patch 0000). Usa ``refactor.WatermarkInjector`` così com'è: seme = chiave del framework,
``num_transforms`` dagli iperparametri, ``random_rules=True`` come la CLI. Le regole 1-35 sono di
Sourcery (una chiamata ``sourcery review --fix`` per cartella), le 36-45 regole proprie (libcst,
autopep8, tabulazioni).

Sourcery lavora su cartelle e ogni chiamata costa secondi: lo shim elabora gli item **a lotti**
(``batch_size``), un file per item in una cartella di lavoro.

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
login o rete le regole 1-35 non si applicherebbero in silenzio.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Sequence

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import DetectStatus, EmbedStatus

from bench_shims._common.base import ShimBase
from bench_shims._common.runner import main

BATCH_SIZE = 50
THREADS = 5  # thread delle regole proprie (``batch`` del codice, default 5)
SOURCERY_RULES = range(1, 36)
CANARY = "def canary(value):\n    if value:\n        return 1\n    else:\n        return 2\n"
CANARY_RULE = 1  # remove-unnecessary-else


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
        self._login()
        if any(r in SOURCERY_RULES for r in self.rules):
            self._canary()

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
            path = os.path.join(folder, "canary.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(CANARY)
            self._apply(folder, [CANARY_RULE])
            with open(path, encoding="utf-8") as handle:
                if handle.read() == CANARY:
                    raise RuntimeError(
                        "Sourcery did not modify the canary file: check login, token and network"
                    )
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _apply(self, folder: str, rules: Sequence[int]) -> int:
        """Applica le regole con il codice (``apply_specific_rules``); chiamate a Sourcery fatte."""
        with contextlib.redirect_stdout(io.StringIO()):  # libcst stampa i diff
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

    def _per_rule(self, folder: str, names: Sequence[str]) -> Any:
        """Per ogni regola: quali file cambiano riapplicandola da sola; chiamate a Sourcery."""
        changed: Dict[int, List[bool]] = {}
        calls = 0
        for rule in self.rules:
            copy = self._copy(folder, f"rule{rule}")
            try:
                calls += self._apply(copy, [rule])
                changed[rule] = self._changed(folder, copy, names)
            finally:
                shutil.rmtree(copy, ignore_errors=True)
        return changed, calls

    # ------------------------------------------------------------------ inserimento
    def embed_batch(self, items: List[WorkerItem]) -> List[List[WorkerResult]]:
        folder, names = self._batch_dir(items)
        try:
            changed, calls = self._per_rule(folder, names)
            work = self._copy(folder, "embed")
            calls += self._apply(work, self.rules)
            modified = self._changed(folder, work, names)
            outputs: List[List[WorkerResult]] = []
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
                outputs.append([result])
            shutil.rmtree(work, ignore_errors=True)
            return outputs
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # ------------------------------------------------------------------ rilevazione
    def detect_batch(self, items: List[WorkerItem]) -> List[WorkerResult]:
        folder, names = self._batch_dir(items)
        try:
            changed, calls = self._per_rule(folder, names)
            joint = self._copy(folder, "joint")
            calls += self._apply(joint, self.rules)
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
            return results
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(AcwShim))
