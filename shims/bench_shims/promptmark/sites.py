"""Siti idonei di PromptMark: identificatori scelti liberamente dal modello (audit §10).

Decisione dell'utente dell'8 ottobre 2026: per ogni campione si conta quanti identificatori del
codice il modello ha scelto liberamente, cioè gli identificatori che la rilevazione valuta
(``CodeNavigator``: non pubblici unici, esclusi ``self`` e ``cls``) e che **non** compaiono già
nel prompt del problema
(firma e docstring di HumanEval+, testo e assert di esempio di MBPP+). Compatibile con Python 3.9.
"""

from __future__ import annotations

import ast
import re
import textwrap
from typing import Any, Optional, Set

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Recinzioni a inizio riga: il testo del template nomina la recinzione anche in mezzo alla frase
# ("in a single ```python code block").
_BLOCK = re.compile(r"^```[^\n`]*\n(.*?)^```", re.DOTALL | re.MULTILINE)


def prompt_block(user_message: str) -> str:
    """Il problema dentro il messaggio utente del framework: l'ultimo blocco recintato.

    Senza blocco (non accade con i template di L1) si usa l'intero messaggio.
    """
    blocks = _BLOCK.findall(user_message)
    return blocks[-1] if blocks else user_message


def evaluated_identifiers(code: str, navigator: Any) -> Optional[Set[str]]:
    """Identificatori valutati dalla rilevazione di PromptMark (``None`` se non si analizza)."""
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return None
    nav = navigator()
    nav.visit(tree)
    tokens = nav.non_public_classes | nav.non_public_funcs | nav.non_public_vars
    return {t for t in tokens if t not in {"self", "cls"}}


def free_identifiers(code: str, user_message: str, navigator: Any) -> Optional[int]:
    """Numero di identificatori valutati che non compaiono già nel prompt del problema."""
    found = evaluated_identifiers(code, navigator)
    if found is None:
        return None
    given = set(_WORD.findall(prompt_block(user_message)))
    return len(found - given)
