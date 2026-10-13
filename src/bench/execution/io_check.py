"""Verifica stdin/stdout di un programma CodeNet (executor ``codenet``, SPEC §11.3).

Gira **dentro** la sandbox come file accessorio del job, con il Python dell'immagine, quindi usa
solo la libreria standard e non importa nulla del framework. Uso::

    python3 ../io_check.py INPUT EXPECTED -- COMANDO...

Esegue ``COMANDO`` con ``INPUT`` su stdin e confronta lo stdout con ``EXPECTED``, normalizzando gli
spazi finali di ogni riga e del file (``bench.data.codenet.normalize_output``, stessa regola).
Codici di uscita, letti dalla regola generica di ``classify`` del runner:

- 0 se l'output coincide (PASSED);
- 1 con ``Assertion: output mismatch`` su stderr se il programma termina bene ma l'output è
  diverso (FAILED);
- il codice del programma (o 1), con il suo stderr, se il programma fallisce (RUNTIME_ERROR).

Il tempo massimo e i limiti di memoria li applica il runner all'intero gruppo di processi.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def normalize_output(text: str) -> str:
    """Spazi finali di riga e di file tolti, fine riga Unix."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip()


def main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[3] != "--":  # noqa: PLR2004 - forma fissa della riga di comando
        sys.stderr.write("usage: io_check.py INPUT EXPECTED -- COMMAND...\n")
        return 2
    stdin = Path(argv[1]).read_bytes()
    expected = Path(argv[2]).read_text(encoding="utf-8", errors="replace")
    proc = subprocess.run(argv[4:], input=stdin, capture_output=True, check=False)
    err = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(err or f"exit code {proc.returncode}\n")
        return proc.returncode if 0 < proc.returncode < 256 else 1  # noqa: PLR2004
    out = proc.stdout.decode("utf-8", errors="replace")
    if normalize_output(out) == normalize_output(expected):
        return 0
    got, want = normalize_output(out), normalize_output(expected)
    sys.stderr.write(
        f"Assertion: output mismatch\n--- expected ({len(want)} chars)\n{want[:500]}\n"
        f"--- got ({len(got)} chars)\n{got[:500]}\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
