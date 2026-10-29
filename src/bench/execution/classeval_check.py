"""Test di una classe ClassEval (executor ``classeval``, SPEC §11.3; ADR-016).

Gira **dentro** la sandbox come file accessorio del job, con il Python dell'immagine, quindi usa
solo la libreria standard e non importa nulla del framework. Uso::

    python3 ../classeval_check.py MODULO CLASSE_DI_TEST[,CLASSE_DI_TEST...] [TIMEOUT_S]

Riproduce ``AutoTest.test`` di ``classeval_evaluation/test_pipeline.py``: importa il modulo
(generazione + test), esegue ogni classe di test con ``unittest`` e un limite di ``TIMEOUT_S``
secondi (default 5, ``func_set_timeout(5)`` dell'originale). La classe è risolta (``success`` di
ClassEval) se ogni classe di test esegue almeno un test e nessuno ha errori o fallimenti. Codici di
uscita, letti dalla regola generica di ``classify`` del runner:

- 0 se tutte le classi di test passano (PASSED);
- 1 con ``Assertion: ...`` su stderr se almeno un test fallisce, va in errore, non viene eseguito
  o supera il limite (FAILED);
- 1 senza ``Assertion`` se il modulo non si importa (RUNTIME_ERROR).

Su stdout una riga JSON con i conteggi per classe di test.
"""

from __future__ import annotations

import importlib
import io
import json
import signal
import sys
import traceback
import unittest
from typing import Any

DEFAULT_TIMEOUT_S = 5


class _Timeout(Exception):
    pass


def _alarm(signum: int, frame: Any) -> None:
    raise _Timeout


def _arm(seconds: int) -> None:
    """Avvia (``seconds`` > 0) o ferma (0) il limite; su Windows (solo test locali) niente."""
    if sys.platform != "win32":
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(seconds)


def run_class(module: Any, name: str, timeout_s: int) -> dict[str, Any]:
    """Conteggi di una classe di test (``testsRun`` = 0 se manca, va in errore o in timeout)."""
    item: dict[str, Any] = {"testsRun": 0, "failures": 0, "errors": 0, "error": None}
    _arm(timeout_s)
    try:
        suite = unittest.TestLoader().loadTestsFromTestCase(getattr(module, name))
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        item.update(
            testsRun=result.testsRun, failures=len(result.failures), errors=len(result.errors)
        )
    except _Timeout:
        item["error"] = f"timeout after {timeout_s} s"
    except BaseException as exc:
        item["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _arm(0)
    return item


def solved(item: dict[str, Any]) -> bool:
    """``success`` di ``get_test_answer``: test eseguiti, nessun errore o fallimento."""
    return bool(item["testsRun"] > 0 and item["errors"] + item["failures"] == 0)


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        sys.stderr.write("usage: classeval_check.py MODULE TEST_CLASSES [TIMEOUT_S]\n")
        return 2
    timeout_s = int(argv[3]) if len(argv) == 4 else DEFAULT_TIMEOUT_S  # noqa: PLR2004
    sys.path.insert(0, ".")
    try:
        module = importlib.import_module(argv[1])
    except BaseException:
        sys.stderr.write(traceback.format_exc())
        return 1
    results = {name: run_class(module, name, timeout_s) for name in argv[2].split(",")}
    sys.stdout.write(json.dumps(results, sort_keys=True) + "\n")
    failed = [name for name, item in results.items() if not solved(item)]
    if failed:
        details = "; ".join(
            f"{n}: run {results[n]['testsRun']}, failures {results[n]['failures']}, "
            f"errors {results[n]['errors']}"
            + (f", {results[n]['error']}" if results[n]["error"] else "")
            for n in failed
        )
        sys.stderr.write(f"Assertion: {len(failed)}/{len(results)} test classes not passed: ")
        sys.stderr.write(details + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
