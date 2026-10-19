"""T1.3 commenti: (a) rimozione, (b) inserimento (SPEC §12; decisioni dell'utente del 16 ottobre
2026, ADR-012).

**(a) Rimozione** dei nodi commento di tree-sitter (``COMMENT_TYPES``): una riga che contiene solo
il commento sparisce; un commento a fine riga si toglie con gli spazi che lo precedono; un
commento in mezzo al codice diventa uno spazio. Le docstring Python **restano** (sono stringhe,
SPEC §12): deviazione dichiarata.

**(b) Inserimento:** ``max(1, round(density × righe di codice))`` commenti (``density`` = 1/3 di
default, nell'hash dei parametri), in punti scelti con il seme dell'attacco fra i punti prima delle
istruzioni (``statement_sites``), con la sintassi di riga e l'indentazione del linguaggio. I testi
vengono dalla raccolta di commenti umani del linguaggio (fase ``attack_pools``). Per campione si
salvano ``n_comments`` e ``added_token_fraction`` (token aggiunti / token del codice originale,
token = ``\\w+`` o un carattere non spazio).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome, tool_error
from bench.attacks.t1.common import LINE_COMMENT, byte_to_char, insert_line, statement_sites
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.lang.line_counter import code_rows
from bench.lang.parsers import COMMENT_TYPES, iter_nodes
from bench.registry import ATTACKS

_TOKEN = re.compile(r"\w+|\S")


def count_tokens(text: str) -> int:
    """Token per ``added_token_fraction``: sequenze di caratteri di parola o simboli singoli."""
    return len(_TOKEN.findall(text))


def remove_comments(code: str, language: Language, parsers: Any) -> tuple[str, int]:
    """Codice senza commenti e numero di commenti tolti."""
    root = parsers.parse(code, Language(language)).root_node
    to_char = byte_to_char(code)
    spans = sorted(
        ((to_char[n.start_byte], to_char[n.end_byte]) for n in iter_nodes(root)
         if n.type in COMMENT_TYPES),
        reverse=True,
    )  # fmt: skip
    out = code
    for start, end in spans:
        line_start = out.rfind("\n", 0, start) + 1
        line_end = out.find("\n", end)
        line_end = len(out) if line_end < 0 else line_end
        before, after = out[line_start:start], out[end:line_end]
        if not before.strip() and not after.strip():
            out = out[:line_start] + out[min(line_end + 1, len(out)) :]
        elif not after.strip():
            out = out[: line_start + len(before.rstrip())] + out[line_end:]
        else:
            pad = "" if (before.endswith((" ", "\t")) or after.startswith((" ", "\t"))) else " "
            out = out[:start] + pad + out[end:]
    return out, len(spans)


@ATTACKS.register("T1.3")
class CommentsAttack(Attack):
    """T1.3: ``mode=remove`` o ``mode=insert`` (con ``density``)."""

    attack_id: ClassVar[str] = "T1.3"
    group: ClassVar[str] = "T1"
    needs_pools: ClassVar[bool] = True

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        mode = self.params.get("mode")
        if mode not in ("remove", "insert"):
            raise ConfigError(f"T1.3: mode must be remove or insert, got {mode!r}")
        if mode == "insert" and not 0 < float(self.params.get("density", 0)) <= 1:
            raise ConfigError("T1.3 insert: density must be in (0, 1]")

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        lang = Language(language)
        had_error = ctx.parsers.has_error(code, lang)
        if self.params["mode"] == "remove":
            new, n = remove_comments(code, lang, ctx.parsers)
            detail: dict[str, Any] = {"n_removed": n}
        else:
            new, detail = self._insert(code, lang, ctx)
        if not had_error and ctx.parsers.has_error(new, lang):
            return tool_error(code, "the attacked code no longer parses", detail)
        return outcome(code, new, detail)

    def _insert(self, code: str, lang: Language, ctx: AttackContext) -> tuple[str, dict[str, Any]]:
        if not ctx.pools.comments:
            raise ConfigError("T1.3 insert: empty comment pool (run stage attack_pools)")
        root = ctx.parsers.parse(code, lang).root_node
        n_lines = len(code_rows(root, lang))
        wanted = max(1, round(float(self.params["density"]) * n_lines))
        sites = statement_sites(code, lang, ctx.parsers)
        chosen = sorted(ctx.rng.sample(range(len(sites)), min(wanted, len(sites))))
        texts = [ctx.rng.choice(ctx.pools.comments) for _ in chosen]
        new = code
        added = 0
        # Dal fondo, così gli offset dei punti precedenti restano validi.
        for index, text in sorted(zip(chosen, texts, strict=True), reverse=True):
            line = f"{LINE_COMMENT[lang]} {text}"
            new = insert_line(new, sites[index], line)
            added += count_tokens(line)
        original = count_tokens(code)
        return new, {
            "n_comments": len(chosen),
            "n_wanted": wanted,
            "n_sites": len(sites),
            "n_code_lines": n_lines,
            "added_tokens": added,
            "added_token_fraction": round(added / original, 6) if original else None,
        }
