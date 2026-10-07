"""Esempi del prompt usati come test nel ciclo di correttezza di PromptMark (audit §8).

Decisione dell'utente del 7 ottobre 2026: il ciclo esegue **solo** gli esempi già presenti nel
prompt comune a tutti i metodi, mai i test di valutazione. Compatibile con Python 3.9.

- HumanEval+ (esempi doctest ``>>>``): ogni esempio con un'uscita attesa che è un'espressione
  Python valida diventa ``assert (<sorgente>) == (<uscita attesa>)``. Il confronto per valore
  tollera le differenze di forma (virgolette, spazi) che un confronto testuale alla doctest
  segnalerebbe come errori; gli esempi con sorgente non valutabile come espressione o uscita
  non valutabile si scartano.
- MBPP+ (righe ``assert`` nella docstring del prompt): le righe così come sono.
Gli esempi in altre forme (per esempio ``f(1) == 2`` in prosa, ``➞``) non si estraggono.
"""

from __future__ import annotations

import ast
import re
from typing import List

_PROMPT = re.compile(r"^(\s*)>>> ?(.*)$")
_STOP = (">>>", '"""', "'''", "```")


def _is_expression(text: str) -> bool:
    try:
        ast.parse(text, mode="eval")
    except SyntaxError:
        return False
    return True


def doctest_examples(text: str) -> List[str]:
    """Asserzioni dagli esempi ``>>>`` del testo.

    Lettura riga per riga, come doctest ma tollerante: il prompt del framework racchiude il codice
    in un blocco recintato, e la recinzione subito dopo la docstring farebbe fallire
    ``doctest.DocTestParser`` (indentazione incoerente) per l'intero testo. L'uscita attesa sono le
    righe successive con la stessa indentazione, fino a una riga vuota, un nuovo ``>>>``, la
    chiusura della docstring o una recinzione.
    """
    out: List[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _PROMPT.match(lines[i])
        i += 1
        if match is None:
            continue
        indent, source = match.group(1), match.group(2)
        while i < len(lines) and lines[i].startswith(indent + "..."):
            source += "\n" + lines[i][len(indent) + 3 :].lstrip(" ")
            i += 1
        want: List[str] = []
        while i < len(lines):
            line = lines[i]
            if not line.strip() or line.strip().startswith(_STOP) or not line.startswith(indent):
                break
            want.append(line[len(indent) :])
            i += 1
        expected = "\n".join(want).strip()
        source = source.strip()
        if not expected or not _is_expression(source) or not _is_expression(expected):
            continue
        out.append(f"assert ({source}) == ({expected})")
    return out


def assert_lines(text: str) -> List[str]:
    """Righe ``assert`` del testo (prompt di MBPP+), valide come istruzioni Python."""
    out: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("assert "):
            continue
        try:
            ast.parse(stripped)
        except SyntaxError:
            continue
        out.append(stripped)
    return out


def prompt_examples(text: str) -> List[str]:
    """Esempi del prompt, senza ripetizioni e nell'ordine in cui compaiono."""
    seen: List[str] = []
    for test in doctest_examples(text) + assert_lines(text):
        if test not in seen:
            seen.append(test)
    return seen
