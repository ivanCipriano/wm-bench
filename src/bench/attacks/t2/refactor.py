"""T2.1 refactoring a regole (SPEC §12; ADR-014, D35).

Griglia ``b ∈ {1, 2, 3, all}`` (decisione dell'utente del 26 ottobre 2026): con l'RNG
dell'attacco si estraggono ``b`` **tipi di regola** fra quelli applicabili al campione e ognuno si
applica in **tutti** i punti in cui vale; ``all`` = tutti i tipi applicabili. Se i tipi
applicabili sono meno di ``b`` si applicano tutti; se nessuno è applicabile il campione è
``UNCHANGED`` (resta nelle metriche). Il dettaglio registra tipi e punti effettivamente
trasformati.

**Catalogo AST** (tree-sitter, riscrittura del testo; le occorrenze annidate dello stesso tipo si
trasformano insieme). Restrizioni, tutte per non cambiare la semantica:

- ``for_to_while`` (Java, C++, JavaScript): ``for (init; cond; upd) body`` →
  ``{ init while (cond) { body upd; } }``. Esclusi: cicli con un ``continue`` che li riguarda
  (salterebbe l'aggiornamento), ``for (; c; )`` senza inizializzazione né aggiornamento (sono già
  ``while``), cicli con etichetta, condizioni-dichiarazione (C++), in Java i
  corpi che finiscono con ``return``/``break``/``throw`` (aggiornamento irraggiungibile), e in
  JavaScript i cicli con ``let``/``const`` nell'inizializzazione e funzioni nel corpo (ogni
  iterazione ha il suo legame della variabile, che le chiusure vedono);
- ``for_to_while`` (Python): solo ``for v in range(...)`` con passo letterale non nullo →
  ``v = start`` / ``while v < stop:`` / ``v += step``. Esclusi: ``else`` del ciclo, ``continue``
  che lo riguarda, ``v`` riassegnata nel corpo o nominata altrove nella funzione (dopo il ciclo
  ``v`` avrebbe un altro valore), corpo sulla stessa riga; ``stop`` si valuta una volta (in una
  temporanea, se non è un letterale o un nome mai riassegnato nel corpo);
- ``while_to_for`` (Java, C++, JavaScript): ``while (c) body`` → ``for (; c; ) body``. Escluse le
  condizioni-dichiarazione (C++);
- ``if_else_inversion``: ``if c A else B`` → ``if !(c) B else A`` (Python ``not (c)``). Escluse le
  catene ``else if``/``elif``, ``if constexpr`` e ``if`` con inizializzazione (C++); i rami senza
  graffe si racchiudono fra graffe (``else`` pendente); in Python i due blocchi devono avere la
  stessa indentazione e stare su righe proprie;
- ``compound_assignment``: ``x op= e`` → ``x = x op (e)`` con ``x`` identificatore semplice.
  Python: ``/=``, ``//=``, ``%=``, ``**=``, ``<<=``, ``>>=`` sempre; ``+=`` e ``-=`` solo con
  ``e`` letterale numerico (su liste e insiemi la forma composta modifica l'oggetto, quella
  espansa ne crea uno nuovo: ``l *= 2`` vale anche per le liste); mai ``*=``, ``|=``, ``&=``,
  ``^=``, ``@=``. Java (cast implicito della forma composta): solo variabili locali o parametri
  con tipo ``double`` (+ - * / %), ``String`` (+=), ``int``/``long`` con ``e`` letterale intero,
  ``boolean`` (& | ^). JavaScript: mai ``&&=``, ``||=``, ``??=`` (assegnano solo a volte). C++
  (catalogo versione 2, decisione dell'utente del 27 ottobre 2026): solo variabili locali o
  parametri con tipo dichiarato aritmetico (``primitive_type`` o ``sized_type_specifier``: ``int``,
  ``long long``, ``double``, ``char``, ``size_t``, …), senza puntatori, riferimenti o array; niente
  ``string``, ``auto`` o tipi non trovati (``s += 'a' + k`` su ``std::string`` usa la conversione
  implicita di ``operator+=(char)``, mentre ``s = s + ('a' + k)`` non compila: humaneval/89 e
  humaneval/11 di STONE in L1 test). Solo come istruzione o aggiornamento di un ``for``;
- ``commutative_swap``: ``a * b``, ``a == b``, ``a != b`` (e ``===``, ``!==``) → operandi scambiati,
  solo con operandi identificatori o letterali numerici (nessun effetto collaterale, nessun ordine
  di valutazione da conservare) e almeno un identificatore;
- ``comparison_flip``: ``a < b`` → ``b > a`` (e ``<=``/``>=``), stesse condizioni sugli operandi.

Ogni tipo si scarta (``rejected``) se il codice trasformato non si analizza più (tree-sitter; in
Python anche ``compile``) mentre l'originale sì.

**Python, regole Ruff** (D35: lo SPEC parla di "regole Ruff estese usate da ACW", ma ACW usa
Sourcery): le regole di Ruff equivalenti alle regole Sourcery che ACW usa, con la versione di Ruff
fissata (``ruff_version``) e la lista fissata in ``configs/attack/t2_1_refactor.yaml``
(``ruff_rules``: tipo, codici, regole ACW corrispondenti; tabella completa in
``docs/attacks/t2_1_ruff_sourcery.md``). Un tipo Ruff è applicabile se ``ruff check`` trova
violazioni correggibili; si applica con ``ruff check --fix --unsafe-fixes --preview`` sui soli
codici dei tipi estratti (molte correzioni equivalenti a Sourcery sono "unsafe" o in preview nella
0.7.4).

**Sovrapposizione con ACW**: ``acw_equivalent_rules`` elenca le regole di inserimento di ACW
equivalenti ai tipi applicati (Ruff: dalla tabella; ``commutative_swap`` con ``*`` → 37;
``comparison_flip`` con ``<``/``>`` → 38, con ``<=``/``>=`` → 39), per riportare quanto l'attacco
è mirato su ACW.
"""

from __future__ import annotations

import ast
import re
import warnings
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from tree_sitter import Node

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome, tool_error
from bench.attacks.t1.common import NameFactory, identifiers_in
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.lang.parsers import ParserService, iter_nodes
from bench.registry import ATTACKS

PY, JAVA, CPP, JS = Language.PYTHON, Language.JAVA, Language.CPP, Language.JAVASCRIPT
C_LIKE = frozenset({JAVA, CPP, JS})
ALL = frozenset(Language)
RUFF_FLAGS = ("--unsafe-fixes", "--preview")
UNIT = b"    "

BLOCKS = frozenset({"block", "compound_statement", "statement_block"})
LOOPS = frozenset({
    "for_statement", "while_statement", "do_statement", "enhanced_for_statement",
    "for_range_loop", "for_in_statement",
})  # fmt: skip
FUNCTIONS = frozenset({
    "function_definition", "lambda", "class_definition", "method_declaration",
    "constructor_declaration", "lambda_expression", "class_declaration", "class_body",
    "function_declaration", "function_expression", "arrow_function", "generator_function",
    "generator_function_declaration", "method_definition", "class",
})  # fmt: skip
NUMBERS = frozenset({
    "integer", "float", "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
    "binary_integer_literal", "decimal_floating_point_literal", "number_literal", "number",
})  # fmt: skip
INT_LITERALS = frozenset({
    "integer", "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
    "binary_integer_literal",
})  # fmt: skip
FLIP = {b"<": b">", b">": b"<", b"<=": b">=", b">=": b"<="}
MAX_RANGE_ARGS = 3  # range(start, stop, step)


# ---------------------------------------------------------------------- strumenti comuni
def _field(node: Node, name: str) -> Node | None:
    return node.child_by_field_name(name)


def _fields(node: Node, name: str) -> list[Node]:
    return [c for i, c in enumerate(node.children) if node.field_name_for_child(i) == name]


def _text(src: bytes, node: Node) -> bytes:
    return src[node.start_byte : node.end_byte]


def _indent(src: bytes, offset: int) -> bytes:
    """Spazi iniziali della riga che contiene ``offset``."""
    start = src.rfind(b"\n", 0, offset) + 1
    line = src[start:]
    return line[: len(line) - len(line.lstrip(b" \t"))]


def _is_atom(node: Node) -> bool:
    return node.type == "identifier" or node.type in NUMBERS


def _atoms_ok(left: Node | None, right: Node | None) -> bool:
    """Operandi senza effetti collaterali (identificatori e numeri), almeno un identificatore,
    non uguali (lo scambio non cambierebbe nulla)."""
    if left is None or right is None or not (_is_atom(left) and _is_atom(right)):
        return False
    if "identifier" not in (left.type, right.type):
        return False
    return left.text != right.text


def _operator(node: Node) -> bytes:
    op = _field(node, "operator") or _field(node, "operators")
    if op is not None and op.text is not None:
        return op.text
    tokens = [c.text for c in node.children if not c.is_named and c.text]
    return tokens[0] if tokens else b""


def _targets_this(continue_node: Node, loop: Node) -> bool:
    """Il ``continue`` riguarda ``loop`` (nessun ciclo o funzione in mezzo)."""
    cur = continue_node.parent
    while cur is not None and cur != loop:
        if cur.type in LOOPS or cur.type in FUNCTIONS:
            return False
        cur = cur.parent
    return cur == loop


def _has_own_continue(loop: Node, body: Node) -> bool:
    return any(n.type == "continue_statement" and _targets_this(n, loop) for n in iter_nodes(body))


JUMPS = frozenset(
    {
        "return_statement",
        "break_statement",
        "throw_statement",
        "continue_statement",
        "yield_statement",
    }
)


def _ends_with_jump(body: Node) -> bool:
    """L'ultima istruzione del corpo (o il corpo stesso) è un salto."""
    if body.type in BLOCKS:
        statements = [c for c in body.named_children if "comment" not in c.type]
        return bool(statements) and statements[-1].type in JUMPS
    return body.type in JUMPS


def _labeled(node: Node) -> bool:
    return node.parent is not None and node.parent.type == "labeled_statement"


class Rule:
    """Tipo di regola del catalogo AST: ``match`` sceglie i nodi, ``build`` il testo nuovo."""

    name: ClassVar[str]
    languages: ClassVar[frozenset[Language]] = ALL

    def __init__(self, lang: Language, names: NameFactory | None = None, version: int = 1) -> None:
        self.lang = lang
        self.names = names
        self.version = version  # versione del catalogo del linguaggio (catalog_version)
        self.ops: set[str] = set()

    def match(self, node: Node, src: bytes) -> bool:
        raise NotImplementedError

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        raise NotImplementedError

    def acw_rules(self) -> set[int]:
        """Regole di inserimento di ACW equivalenti a quanto applicato (vuoto se nessuna)."""
        return set()


def rewrite(src: bytes, root: Node, rule: Rule) -> tuple[bytes, int]:
    """Riscrive ``src`` applicando ``rule`` in ogni nodo che la soddisfa (anche annidati: il
    testo di un nodo trasformato usa il testo già trasformato dei figli)."""
    count = 0

    def text_of(node: Node) -> bytes:
        nonlocal count
        if rule.match(node, src):
            count += 1
            return rule.build(node, src, text_of)
        if not node.children:
            return _text(src, node)
        out = bytearray()
        pos = node.start_byte
        for child in node.children:
            out += src[pos : child.start_byte]
            out += text_of(child)
            pos = child.end_byte
        out += src[pos : node.end_byte]
        return bytes(out)

    new = src[: root.start_byte] + text_of(root) + src[root.end_byte :]
    return new, count


def count_matches(src: bytes, root: Node, rule: Rule) -> int:
    return sum(1 for n in iter_nodes(root) if rule.match(n, src))


# ---------------------------------------------------------------------- for → while
class ForToWhileC(Rule):
    name = "for_to_while"
    languages = C_LIKE

    def match(self, node: Node, src: bytes) -> bool:
        if node.type != "for_statement" or _labeled(node):
            return False
        body = _field(node, "body")
        if body is None or _has_own_continue(node, body):
            return False
        cond = _field(node, "condition")
        if self.lang is CPP and cond is not None and cond.type == "declaration":
            return False
        if self.lang is JS:
            init = _field(node, "initializer")
            if (
                init is not None
                and init.type == "lexical_declaration"
                and any(n.type in FUNCTIONS for n in iter_nodes(body))
            ):
                return False
        # for (; c; ) senza inizializzazione né aggiornamento è già un while: trasformarlo
        # annullerebbe while_to_for.
        init_fields = ("init", "initializer")
        update_fields = ("update", "increment")
        has_init = any(
            f.type != "empty_statement" for name in init_fields for f in _fields(node, name)
        )
        if not has_init and not any(_fields(node, name) for name in update_fields):
            return False
        # Java: un aggiornamento dopo return/break/throw sarebbe irraggiungibile (javac lo rifiuta).
        return not (self.lang is JAVA and _ends_with_jump(body) and _fields(node, "update"))

    def _parts(
        self, node: Node, text_of: Callable[[Node], bytes]
    ) -> tuple[bytes | None, bytes, list[bytes]]:
        if self.lang is JAVA:
            inits = _fields(node, "init")
            if len(inits) == 1 and inits[0].type == "local_variable_declaration":
                init: bytes | None = text_of(inits[0])
            else:
                init = b" ".join(text_of(i) + b";" for i in inits) or None
            cond_node = _field(node, "condition")
            cond = text_of(cond_node) if cond_node is not None else b"true"
            updates = [text_of(u) + b";" for u in _fields(node, "update")]
        elif self.lang is CPP:
            init_node = _field(node, "initializer")
            if init_node is None:
                init = None
            elif init_node.type == "declaration":
                init = text_of(init_node)
            else:
                init = text_of(init_node) + b";"
            cond_node = _field(node, "condition")
            cond = text_of(cond_node) if cond_node is not None else b"true"
            updates = [text_of(u) + b";" for u in _fields(node, "update")]
        else:
            init_node = _field(node, "initializer")
            init = (
                None
                if init_node is None or init_node.type == "empty_statement"
                else text_of(init_node)
            )
            cond_node = _field(node, "condition")
            if cond_node is None or cond_node.type == "empty_statement":
                cond = b"true"
            else:
                named = cond_node.named_children
                cond = text_of(named[0]) if named else b"true"
            updates = [text_of(u) + b";" for u in _fields(node, "increment")]
        return init, cond, updates

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        init, cond, updates = self._parts(node, text_of)
        body_node = _field(node, "body")
        assert body_node is not None
        body = text_of(body_node)
        ind = _indent(src, node.start_byte)
        upd = b"".join(b"\n" + ind + UNIT + u for u in updates)
        if body_node.type in BLOCKS and body.endswith(b"}"):
            new_body = body[:-1].rstrip() + upd + b"\n" + ind + b"}" if updates else body
        else:
            new_body = b"{\n" + ind + UNIT + body + upd + b"\n" + ind + b"}"
        loop = b"while (" + cond + b") " + new_body
        if init is None:
            return loop
        return b"{\n" + ind + UNIT + init + b"\n" + ind + UNIT + loop + b"\n" + ind + b"}"


def _scope(node: Node) -> Node:
    cur = node.parent
    while cur is not None:
        if cur.type in ("function_definition", "lambda"):
            return cur
        if cur.parent is None:
            return cur
        cur = cur.parent
    return node


_PY_BINDERS = {
    "assignment": "left",
    "augmented_assignment": "left",
    "for_statement": "left",
    "for_in_clause": "left",
    "named_expression": "name",
    "as_pattern_target": None,
    "delete_statement": None,
    "global_statement": None,
    "nonlocal_statement": None,
}


def _bound_names(root: Node) -> set[bytes]:
    """Nomi assegnati (o dichiarati) dentro ``root``, Python."""
    out: set[bytes] = set()
    for node in iter_nodes(root):
        if node.type not in _PY_BINDERS:
            continue
        field = _PY_BINDERS[node.type]
        target = _field(node, field) if field else node
        if target is None:
            continue
        out.update(n.text for n in iter_nodes(target) if n.type == "identifier" and n.text)
    return out


class ForToWhilePython(Rule):
    name = "for_to_while"
    languages = frozenset({PY})

    @staticmethod
    def _range_args(node: Node) -> list[Node] | None:
        right = _field(node, "right")
        if right is None or right.type != "call":
            return None
        fn, args = _field(right, "function"), _field(right, "arguments")
        if fn is None or fn.text != b"range" or args is None or args.type != "argument_list":
            return None
        values = list(args.named_children)
        if not 1 <= len(values) <= MAX_RANGE_ARGS or any(
            v.type in ("keyword_argument", "list_splat", "dictionary_splat", "comment")
            for v in values
        ):
            return None
        return values

    @staticmethod
    def _step(values: list[Node]) -> int | None:
        if len(values) < MAX_RANGE_ARGS:
            return 1
        step = values[2]
        text = (step.text or b"").replace(b" ", b"")
        if not re.fullmatch(rb"-?\d+", text):
            return None
        value = int(text)
        return value or None

    def match(self, node: Node, src: bytes) -> bool:
        if node.type != "for_statement" or _field(node, "alternative") is not None:
            return False
        var, body = _field(node, "left"), _field(node, "body")
        values = self._range_args(node)
        if var is None or var.type != "identifier" or body is None or values is None:
            return False
        name = var.text
        if (
            self._step(values) is None
            or body.start_point.row == node.start_point.row  # corpo sulla riga del for
            or _has_own_continue(node, body)
            or name in _bound_names(body)
            or any(n.type == "identifier" and n.text == name for v in values for n in iter_nodes(v))
        ):
            return False
        scope = _scope(node)
        return not any(
            n.type == "identifier"
            and n.text == name
            and not (node.start_byte <= n.start_byte < node.end_byte)
            for n in iter_nodes(scope)
        )

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        var, body = _field(node, "left"), _field(node, "body")
        values = self._range_args(node)
        assert var is not None and body is not None and values is not None
        name = _text(src, var)
        step = self._step(values) or 1
        texts = [text_of(v) for v in values]
        start, stop = (b"0", texts[0]) if len(texts) == 1 else (texts[0], texts[1])
        stop_node = values[0] if len(values) == 1 else values[1]
        ind = _indent(src, node.start_byte)
        prefix = name + b" = " + start + b"\n" + ind
        if not (
            stop_node.type in NUMBERS
            or (stop_node.type == "identifier" and stop_node.text not in _bound_names(body))
        ):
            temp = (self.names.random() if self.names else "_stop").encode("utf-8")
            prefix += temp + b" = " + stop + b"\n" + ind
            stop = temp
        colon = next(c for c in node.children if c.type == ":")
        cmp = b" < " if step > 0 else b" > "
        body_ind = _indent(src, body.start_byte)
        return (
            prefix + b"while " + name + cmp + stop + b":" + src[colon.end_byte : body.start_byte]
            + text_of(body) + b"\n" + body_ind + name + b" += " + str(step).encode("ascii")
        )  # fmt: skip


# ---------------------------------------------------------------------- while → for
def _c_condition(node: Node) -> Node | None:
    """Espressione della condizione di ``if``/``while`` (``None`` se è una dichiarazione o ha
    un'inizializzazione, C++)."""
    cond = _field(node, "condition")
    if cond is None:
        return None
    if cond.type == "condition_clause":
        if _field(cond, "initializer") is not None:
            return None
        value = _field(cond, "value")
        return value if value is not None and value.type != "declaration" else None
    if cond.type == "parenthesized_expression":
        named = cond.named_children
        return named[0] if len(named) == 1 else None
    return None


class WhileToFor(Rule):
    name = "while_to_for"
    languages = C_LIKE

    def match(self, node: Node, src: bytes) -> bool:
        return node.type == "while_statement" and _c_condition(node) is not None

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        inner, cond, body = _c_condition(node), _field(node, "condition"), _field(node, "body")
        assert inner is not None and cond is not None and body is not None
        return (
            b"for (; " + text_of(inner) + b"; )" + src[cond.end_byte : body.start_byte]
            + text_of(body)
        )  # fmt: skip


# ---------------------------------------------------------------------- inversione if/else
class IfElseInversion(Rule):
    name = "if_else_inversion"

    def _c_parts(self, node: Node) -> tuple[Node, Node, Node] | None:
        if node.type != "if_statement" or any(c.type == "constexpr" for c in node.children):
            return None
        inner, cons, alt = (
            _c_condition(node),
            _field(node, "consequence"),
            _field(node, "alternative"),
        )
        if inner is None or cons is None or alt is None:
            return None
        if alt.type == "else_clause":
            named = alt.named_children
            if len(named) != 1:
                return None
            alt = named[0]
        if alt.type == "if_statement":
            return None  # catena else if
        return inner, cons, alt

    def _py_parts(self, node: Node) -> tuple[Node, Node, Node] | None:
        if node.type != "if_statement":
            return None
        cond, cons = _field(node, "condition"), _field(node, "consequence")
        alts = _fields(node, "alternative")
        if cond is None or cons is None or len(alts) != 1 or alts[0].type != "else_clause":
            return None
        body = _field(alts[0], "body")
        if body is None:
            return None
        if (
            cons.start_point.row == node.start_point.row
            or body.start_point.row == alts[0].start_point.row
        ):
            return None
        if cons.start_point.column != body.start_point.column:
            return None
        return cond, cons, body

    def match(self, node: Node, src: bytes) -> bool:
        parts = self._py_parts(node) if self.lang is PY else self._c_parts(node)
        return parts is not None

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        if self.lang is PY:
            parts = self._py_parts(node)
            assert parts is not None
            cond, cons, body = parts
            return (
                src[node.start_byte : cond.start_byte] + b"not (" + text_of(cond) + b")"
                + src[cond.end_byte : cons.start_byte] + text_of(body)
                + src[cons.end_byte : body.start_byte] + text_of(cons)
            )  # fmt: skip
        parts = self._c_parts(node)
        assert parts is not None
        inner, cons, alt = parts
        cond_node = _field(node, "condition")
        assert cond_node is not None

        def wrap(n: Node) -> bytes:
            text = text_of(n)
            return text if n.type in BLOCKS else b"{ " + text + b" }"

        return (
            src[node.start_byte : cond_node.start_byte] + b"(!(" + text_of(inner) + b"))"
            + src[cond_node.end_byte : cons.start_byte] + wrap(alt)
            + src[cons.end_byte : alt.start_byte] + wrap(cons)
        )  # fmt: skip


# ---------------------------------------------------------------------- assegnazioni composte
_PY_ALWAYS = frozenset({b"/=", b"//=", b"%=", b"**=", b"<<=", b">>="})
_PY_LITERAL_ONLY = frozenset({b"+=", b"-="})
_JS_NEVER = frozenset({b"&&=", b"||=", b"??="})
_JAVA_INT_OPS = frozenset(
    {b"+=", b"-=", b"*=", b"/=", b"%=", b"<<=", b">>=", b">>>=", b"&=", b"|=", b"^="}
)
# C++: dalla versione 2 del catalogo, operatori composti solo su variabili di tipo aritmetico.
CPP_TYPED_COMPOUND = 2
# Java: operatori ammessi per tipo della variabile (int e long solo con un letterale intero).
_JAVA_OPS = {
    b"double": frozenset({b"+=", b"-=", b"*=", b"/=", b"%="}),
    b"String": frozenset({b"+="}),
    b"boolean": frozenset({b"&=", b"|=", b"^="}),
    b"int": _JAVA_INT_OPS,
    b"long": _JAVA_INT_OPS,
}


def _cpp_arithmetic_local(node: Node, name: bytes) -> bool:
    """C++: ``name`` è una variabile locale o un parametro della funzione (o lambda) con un solo
    tipo dichiarato, aritmetico, e dichiaratore semplice (niente puntatori, riferimenti, array)."""
    fn = node.parent
    while fn is not None and fn.type not in ("function_definition", "lambda_expression"):
        fn = fn.parent
    if fn is None:
        return False
    kinds: set[bool] = set()
    for n in iter_nodes(fn):
        if n.type not in ("declaration", "parameter_declaration", "optional_parameter_declaration",
                          "for_range_loop"):  # fmt: skip
            continue
        t = _field(n, "type")
        for decl in _fields(n, "declarator"):
            target = _field(decl, "declarator") if decl.type == "init_declarator" else decl
            if target is not None and target.type == "identifier" and target.text == name:
                kinds.add(t is not None and t.type in ("primitive_type", "sized_type_specifier"))
            elif target is not None and (target.text or b"").lstrip(b"*&").strip() == name:
                kinds.add(False)  # puntatore, riferimento o array con lo stesso nome
    return kinds == {True}


def _numeric_literal(node: Node | None) -> bool:
    if node is None:
        return False
    if node.type == "unary_operator" and node.named_children:
        return node.children[0].type == "-" and _numeric_literal(node.named_children[0])
    return node.type in NUMBERS


def _java_local_type(node: Node, name: bytes) -> bytes | None:
    """Tipo della variabile locale o del parametro ``name`` nel metodo (``None`` se non è unico,
    è ``var``, è un array o non è locale)."""
    method = node.parent
    while method is not None and method.type not in (
        "method_declaration", "constructor_declaration", "lambda_expression",
    ):  # fmt: skip
        method = method.parent
    if method is None:
        return None
    types: set[bytes] = set()
    for n in iter_nodes(method):
        if n.type in ("local_variable_declaration",):
            for decl in _fields(n, "declarator"):
                ident = _field(decl, "name")
                if ident is not None and ident.text == name:
                    if _field(decl, "dimensions") is not None:
                        return None
                    t = _field(n, "type")
                    types.add(t.text if t is not None and t.text else b"?")
        elif n.type in ("formal_parameter", "enhanced_for_statement", "catch_formal_parameter"):
            ident = _field(n, "name")
            if ident is not None and ident.text == name:
                if _field(n, "dimensions") is not None:
                    return None
                t = _field(n, "type")
                types.add(t.text if t is not None and t.text else b"?")
    if len(types) != 1:
        return None
    (only,) = types
    return None if only in (b"var", b"?") else only


class CompoundAssignment(Rule):
    name = "compound_assignment"

    def _parts(self, node: Node) -> tuple[Node, bytes, Node] | None:
        kinds = {PY: "augmented_assignment", JAVA: "assignment_expression",
                 CPP: "assignment_expression", JS: "augmented_assignment_expression"}  # fmt: skip
        if node.type != kinds[self.lang]:
            return None
        left, right = _field(node, "left"), _field(node, "right")
        op = _operator(node)
        if left is None or right is None or left.type != "identifier" or op == b"=":
            return None
        if not op.endswith(b"="):
            return None
        parent = node.parent
        if parent is None:
            return None
        if self.lang is not PY:
            in_update = parent.type == "for_statement" and node in (
                _fields(parent, "update") + _fields(parent, "increment")
            )
            if parent.type != "expression_statement" and not in_update:
                return None
        return left, op, right

    def match(self, node: Node, src: bytes) -> bool:
        parts = self._parts(node)
        if parts is None:
            return False
        left, op, right = parts
        if self.lang is PY:
            return op in _PY_ALWAYS or (op in _PY_LITERAL_ONLY and _numeric_literal(right))
        if self.lang is JS:
            return op not in _JS_NEVER
        if self.lang is JAVA:
            kind = _java_local_type(node, left.text or b"") or b""
            literal_ok = kind not in (b"int", b"long") or right.type in INT_LITERALS
            return op in _JAVA_OPS.get(kind, frozenset()) and literal_ok
        return self.version < CPP_TYPED_COMPOUND or _cpp_arithmetic_local(node, left.text or b"")

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        parts = self._parts(node)
        assert parts is not None
        left, op, right = parts
        name = text_of(left)
        return name + b" = " + name + b" " + op[:-1] + b" (" + text_of(right) + b")"


# ---------------------------------------------------------------------- scambi di operandi
def _binary_parts(node: Node, lang: Language) -> tuple[Node, bytes, Node] | None:
    if lang is PY:
        if node.type == "binary_operator":
            left, right = _field(node, "left"), _field(node, "right")
        elif node.type == "comparison_operator":
            operands = node.named_children
            if len(operands) != 2 or len(_fields(node, "operators")) != 1:  # noqa: PLR2004
                return None
            left, right = operands
        else:
            return None
    elif node.type == "binary_expression":
        left, right = _field(node, "left"), _field(node, "right")
    else:
        return None
    if left is None or right is None:
        return None
    return left, _operator(node), right


class CommutativeSwap(Rule):
    name = "commutative_swap"
    OPS: ClassVar[frozenset[bytes]] = frozenset({b"*", b"==", b"!=", b"===", b"!=="})

    def match(self, node: Node, src: bytes) -> bool:
        parts = _binary_parts(node, self.lang)
        return parts is not None and parts[1] in self.OPS and _atoms_ok(parts[0], parts[2])

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        parts = _binary_parts(node, self.lang)
        assert parts is not None
        left, op, right = parts
        self.ops.add(op.decode("ascii"))
        return text_of(right) + src[left.end_byte : right.start_byte] + text_of(left)

    def acw_rules(self) -> set[int]:
        return {37} if "*" in self.ops else set()


class ComparisonFlip(Rule):
    name = "comparison_flip"

    def match(self, node: Node, src: bytes) -> bool:
        parts = _binary_parts(node, self.lang)
        return parts is not None and parts[1] in FLIP and _atoms_ok(parts[0], parts[2])

    def build(self, node: Node, src: bytes, text_of: Callable[[Node], bytes]) -> bytes:
        parts = _binary_parts(node, self.lang)
        assert parts is not None
        left, op, right = parts
        self.ops.add(op.decode("ascii"))
        middle = src[left.end_byte : right.start_byte].replace(op, FLIP[op], 1)
        return text_of(right) + middle + text_of(left)

    def acw_rules(self) -> set[int]:
        out = set()
        if self.ops & {"<", ">"}:
            out.add(38)
        if self.ops & {"<=", ">="}:
            out.add(39)
        return out


# Ordine fisso di applicazione dei tipi AST.
CATALOG: dict[str, dict[Language, type[Rule]]] = {
    # while_to_for prima di for_to_while (che salta i for (; c; ) creati da while_to_for).
    "while_to_for": {JAVA: WhileToFor, CPP: WhileToFor, JS: WhileToFor},
    "for_to_while": {PY: ForToWhilePython, JAVA: ForToWhileC, CPP: ForToWhileC, JS: ForToWhileC},
    "if_else_inversion": dict.fromkeys(ALL, IfElseInversion),
    "compound_assignment": dict.fromkeys(ALL, CompoundAssignment),
    "commutative_swap": dict.fromkeys(ALL, CommutativeSwap),
    "comparison_flip": dict.fromkeys(ALL, ComparisonFlip),
}


def ast_types(language: Language) -> list[str]:
    """Tipi AST del catalogo per un linguaggio, nell'ordine di applicazione."""
    return [t for t, by_lang in CATALOG.items() if Language(language) in by_lang]


def _compiles(code: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    return True


def apply_ast_type(
    code: str,
    lang: Language,
    type_name: str,
    parsers: ParserService,
    names: NameFactory | None,
    version: int = 1,
) -> tuple[str, int, set[int]] | None:
    """Applica un tipo AST in tutti i suoi punti: (codice, punti, regole ACW equivalenti);
    ``None`` se il risultato non si analizza più mentre l'originale sì. ``version``: versione
    del catalogo del linguaggio."""
    rule = CATALOG[type_name][lang](lang, names, version)
    src = code.encode("utf-8")
    tree = parsers.parse(code, lang)
    new_src, points = rewrite(src, tree.root_node, rule)
    if not points:
        return code, 0, set()
    new = new_src.decode("utf-8")
    if not tree.root_node.has_error and parsers.has_error(new, lang):
        return None
    if lang is PY and _compiles(code) and not _compiles(new):
        return None
    return new, points, rule.acw_rules()


def ast_applicable(
    code: str, lang: Language, parsers: ParserService, version: int = 1
) -> dict[str, int]:
    """Punti di ogni tipo AST nel codice (solo i tipi con almeno un punto)."""
    src = code.encode("utf-8")
    root = parsers.parse(code, lang).root_node
    out = {}
    for type_name in ast_types(lang):
        n = count_matches(src, root, CATALOG[type_name][lang](lang, None, version))
        if n:
            out[type_name] = n
    return out


# ---------------------------------------------------------------------- attacco
@ATTACKS.register("T2.1")
class RefactorAttack(Attack):
    """T2.1: ``b`` tipi di regola fra catalogo AST e (Python) regole Ruff equivalenti ad ACW."""

    attack_id: ClassVar[str] = "T2.1"
    group: ClassVar[str] = "T2"
    needs_tools: ClassVar[bool] = True

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        b = self.params.get("b")
        if b != "all" and not (isinstance(b, int) and b >= 1):
            raise ConfigError(f"T2.1: b must be a positive integer or 'all', not {b!r}")
        if not self.params.get("ruff_version") or not self.params.get("ruff_rules"):
            raise ConfigError("T2.1: ruff_version and ruff_rules are required")
        self.ruff_types: dict[str, tuple[list[str], list[int]]] = {
            f"ruff:{r['type']}": (list(r["codes"]), list(r.get("acw") or []))
            for r in self.params["ruff_rules"]
        }

    def ruff_codes(self) -> list[str]:
        return sorted({c for codes, _ in self.ruff_types.values() for c in codes})

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        return self.apply_many([code], language, [ctx])[0]

    def _check_ruff(self, ctx: AttackContext) -> None:
        if ctx.tools is None:
            raise ConfigError("T2.1 (Python) needs the ToolRunner for ruff")
        version = ctx.tools.version("ruff")
        if str(self.params["ruff_version"]) not in version.split():
            raise ConfigError(
                f"T2.1: ruff {self.params['ruff_version']} required, found {version!r}"
            )

    def _ruff_applicable(
        self, codes: Sequence[str], ctx: AttackContext
    ) -> list[dict[str, int] | None]:
        assert ctx.tools is not None
        found = ctx.tools.ruff_fixable(codes, self.ruff_codes(), RUFF_FLAGS)
        out: list[dict[str, int] | None] = []
        for counts in found:
            if counts is None:
                out.append(None)
                continue
            out.append({
                t: n
                for t, (codes_t, _) in self.ruff_types.items()
                if (n := sum(counts.get(c, 0) for c in codes_t))
            })  # fmt: skip
        return out

    def _choose(self, applicable: dict[str, int], ctx: AttackContext) -> list[str]:
        types = sorted(applicable)
        b = self.params["b"]
        if b == "all" or len(types) <= int(b):
            return types
        return sorted(ctx.rng.sample(types, int(b)))

    def apply_many(
        self, codes: Sequence[str], language: Language, ctxs: Sequence[AttackContext]
    ) -> list[AttackOutcome]:
        return self.apply_types(codes, language, ctxs)

    def apply_types(
        self,
        codes: Sequence[str],
        language: Language,
        ctxs: Sequence[AttackContext],
        forced: Sequence[list[str]] | None = None,
    ) -> list[AttackOutcome]:
        """Come ``apply_many``; con ``forced`` i tipi da applicare a ogni campione sono dati (fra
        quelli applicabili) invece che estratti: serve a trovare quale tipo rompe un campione."""
        lang = Language(language)
        out: list[AttackOutcome | None] = [None] * len(codes)
        todo = [i for i, c in enumerate(codes) if c.strip()]
        for i, code in enumerate(codes):
            if not code.strip():
                out[i] = AttackOutcome(code, AttackStatus.UNCHANGED, {"empty": True})
        ruff_found: list[dict[str, int] | None] = [{} for _ in codes]
        if lang is PY and todo:
            self._check_ruff(ctxs[todo[0]])
            found = self._ruff_applicable([codes[i] for i in todo], ctxs[todo[0]])
            for i, counts in zip(todo, found, strict=True):
                ruff_found[i] = counts
        plans: dict[int, tuple[list[str], dict[str, int]]] = {}
        for i in todo:
            if ruff_found[i] is None:
                out[i] = tool_error(codes[i], "ruff could not analyse the code", {"tool": "ruff"})
                continue
            applicable = {
                **ast_applicable(codes[i], lang, ctxs[i].parsers, self.catalog_version(lang)),
                **(ruff_found[i] or {}),
            }
            chosen = (
                self._choose(applicable, ctxs[i])
                if forced is None
                else sorted(t for t in forced[i] if t in applicable)
            )
            plans[i] = (chosen, applicable)
        current = {i: codes[i] for i in plans}
        ruff_done = self._apply_ruff(plans, current, ctxs, out)
        for i, (chosen, applicable) in plans.items():
            if out[i] is not None:
                continue
            out[i] = self._finish(codes[i], current[i], lang, ctxs[i], chosen, applicable,
                                  ruff_done.get(i, {}))  # fmt: skip
        return [o for o in out if o is not None]

    def _apply_ruff(
        self,
        plans: dict[int, tuple[list[str], dict[str, int]]],
        current: dict[int, str],
        ctxs: Sequence[AttackContext],
        out: list[AttackOutcome | None],
    ) -> dict[int, dict[str, int]]:
        """Correzioni di Ruff dei tipi estratti, a lotti per insieme di codici."""
        groups: dict[tuple[str, ...], list[int]] = {}
        for i, (chosen, _) in plans.items():
            codes = tuple(
                sorted(c for t in chosen if t in self.ruff_types for c in self.ruff_types[t][0])
            )
            if codes:
                groups.setdefault(codes, []).append(i)
        done: dict[int, dict[str, int]] = {}
        for codes, indices in groups.items():
            tools = ctxs[indices[0]].tools
            assert tools is not None
            results = tools.run(
                "ruff",
                [current[i] for i in indices],
                extra_args=["--select", ",".join(codes), *RUFF_FLAGS],
            )
            for i, res in zip(indices, results, strict=True):
                chosen, applicable = plans[i]
                if res.code is None:
                    out[i] = tool_error(current[i], res.error or "ruff error", {"tool": "ruff"})
                    continue
                changed = res.code != current[i]
                done[i] = {t: applicable[t] for t in chosen if t in self.ruff_types and changed}
                current[i] = res.code
        return done

    def _finish(
        self,
        original: str,
        code: str,
        lang: Language,
        ctx: AttackContext,
        chosen: list[str],
        applicable: dict[str, int],
        ruff_points: dict[str, int],
    ) -> AttackOutcome:
        names = NameFactory(ctx.rng, lang, identifiers_in(original))
        points: dict[str, int] = dict(ruff_points)
        acw: dict[str, list[int]] = {t: self.ruff_types[t][1] for t in ruff_points}
        rejected: list[str] = []
        for type_name in ast_types(lang):
            if type_name not in chosen:
                continue
            result = apply_ast_type(
                code, lang, type_name, ctx.parsers, names, self.catalog_version(lang)
            )
            if result is None:
                rejected.append(type_name)
                continue
            code, n, rules = result
            if n:
                points[type_name] = n
                acw[type_name] = sorted(rules)
        detail = {
            "b": self.params["b"],
            "n_types_applicable": len(applicable),
            "chosen": chosen,
            "applied": sorted(points),
            "n_types": len(points),
            "n_points": sum(points.values()),
            "points": points,
            # regole di inserimento di ACW equivalenti a ogni tipo applicato (vuoto: nessuna)
            "acw_equivalent": acw,
            "acw_equivalent_rules": sorted({r for rules in acw.values() for r in rules}),
            "rejected": rejected,
        }
        if lang is PY:
            detail["tool"] = "ruff"  # versione di Ruff nel manifest della cella
        return outcome(original, code, detail)
