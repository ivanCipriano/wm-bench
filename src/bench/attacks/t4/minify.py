"""T4.1 minificazione e T4.2 bundling (SPEC §12; ADR-014 §4; decisioni dell'utente del 28 ottobre
2026).

- T4.1, Python: ``python-minifier`` 2.11.3 in processo con le trasformazioni di default della
  versione fissata; ``rename`` = ``rename_locals`` (i parametri non si rinominano: la libreria li
  copia in una variabile locale), ``rename_globals`` sempre falso, così il nome della funzione
  d'ingresso non cambia; docstring conservate (``remove_literal_statements`` falso, default).
- T4.1, JavaScript: ``terser`` 5.36.0, ``compress`` con le opzioni di default; con ``rename``
  anche ``mangle`` con ``toplevel`` falso (i nomi globali, compresa la funzione d'ingresso,
  restano).
- T4.2, JavaScript: ``esbuild`` 0.24.0 ``--bundle --format=cjs --platform=node
  --tree-shaking=false``: in un progetto la funzione è raggiungibile da chi la chiama e non verrebbe
  eliminata; senza tree-shaking e con l'output per Node si riproduce questa condizione sul
  campione isolato.

Nessuna scelta casuale: l'esito dipende solo dal codice. Un errore del tool dà ``TOOL_ERROR`` con il
codice originale.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import ClassVar

from bench.attacks.t1.tooling import ToolAttack
from bench.attacks.tools import ToolRunner
from bench.domain.enums import Language
from bench.registry import ATTACKS

PYTHON_MINIFIER = "python-minifier"
# require("x"), import ... from "x", import "x", import("x"): il nome del modulo importato.
_JS_IMPORT = re.compile(
    r"""(?:\brequire\s*\(\s*|\bimport\s*\(\s*|\bfrom\s+|\bimport\s+)(['"])([^'"\n]+)\1"""
)


def node_builtins(runner: ToolRunner) -> frozenset[str]:
    """Moduli predefiniti del Node dell'ambiente dei tool (``require('module').builtinModules``)."""
    proc = subprocess.run(
        ["node", "-e", "console.log(JSON.stringify(require('module').builtinModules))"],
        capture_output=True, text=True, env=runner.env(), check=True, timeout=60,
    )  # fmt: skip
    return frozenset(json.loads(proc.stdout))


def npm_packages(code: str, builtins: frozenset[str]) -> list[str]:
    """Pacchetti npm importati dal codice (non relativi, non ``node:``, non predefiniti), in
    ordine di comparsa; per lo scoped ``@a/b/c`` il pacchetto è ``@a/b``, per ``a/b`` è ``a``."""
    out: list[str] = []
    for _, spec in _JS_IMPORT.findall(code):
        if spec.startswith((".", "/", "node:")):
            continue
        parts = spec.split("/")
        package = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        if package not in builtins and package not in out:
            out.append(package)
    return out


@ATTACKS.register("T4.1")
class MinifyAttack(ToolAttack):
    """T4.1: minificazione con o senza rinomina (parametro ``rename``); solo Python e JavaScript."""

    attack_id: ClassVar[str] = "T4.1"
    group: ClassVar[str] = "T4"
    supported_languages: ClassVar[frozenset[Language]] = frozenset(
        {Language.PYTHON, Language.JAVASCRIPT}
    )
    in_process_tool: ClassVar[str] = PYTHON_MINIFIER

    @property
    def rename(self) -> bool:
        return bool(self.params.get("rename", False))

    def tool(self, language: Language) -> str:
        if Language(language) is Language.PYTHON:
            return PYTHON_MINIFIER
        return "terser-mangle" if self.rename else "terser"

    def apply_python(self, code: str) -> str | None:
        import python_minifier

        return str(python_minifier.minify(code, rename_locals=self.rename, rename_globals=False))


@ATTACKS.register("T4.2")
class BundleAttack(ToolAttack):
    """T4.2: bundling con esbuild; solo JavaScript."""

    attack_id: ClassVar[str] = "T4.2"
    group: ClassVar[str] = "T4"
    supported_languages: ClassVar[frozenset[Language]] = frozenset({Language.JAVASCRIPT})
    tools: ClassVar[dict[Language, str]] = {Language.JAVASCRIPT: "esbuild"}
