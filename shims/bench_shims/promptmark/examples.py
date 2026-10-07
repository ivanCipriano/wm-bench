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
import doctest
from typing import List


def _is_expression(text: str) -> bool:
    try:
        ast.parse(text, mode="eval")
    except SyntaxError:
        return False
    return True


def doctest_examples(text: str) -> List[str]:
    """Asserzioni dagli esempi ``>>>`` del testo."""
    out: List[str] = []
    try:
        examples = doctest.DocTestParser().get_examples(text)
    except ValueError:  # indentazione incoerente dentro un esempio
        return out
    for example in examples:
        source = example.source.strip()
        # L'uscita attesa finisce prima della chiusura della docstring, che doctest le attacca
        # quando è alla stessa indentazione.
        lines = []
        for line in example.want.splitlines():
            if line.strip().startswith(('"""', "'''")):
                break
            lines.append(line)
        want = "\n".join(lines).strip()
        if not want or not _is_expression(source) or not _is_expression(want):
            continue
        out.append(f"assert ({source}) == ({want})")
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
