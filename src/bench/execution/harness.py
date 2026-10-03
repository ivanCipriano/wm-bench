"""Logica d'esecuzione di HumanEvalPack ripresa da ``bigcode-evaluation-harness`` (ADR-001).

L'harness (``bigcode_eval/tasks/humanevalpack.py``, ``process_results``) prepara ogni
generazione per linguaggio e poi chiama la metrica ``Muennighoff/code_eval_octopack``, che
compone ``generazione + "\\n" + test`` e lo compila ed esegue. La metrica viene scaricata da
Hugging Face e il modulo dell'harness importa ``evaluate``: nessuno dei due è usabile offline
nella sandbox. Qui si replica la stessa sequenza:

- le costanti (``IMPORT_HELPER``) si leggono dal sorgente del submodule con ``ast``, senza
  importarlo né copiarle;
- la preparazione per linguaggio riproduce le righe di ``process_results``, verificate dai test
  sul sorgente del submodule (``tests/unit/test_harness.py``);
- comandi e regole di esito riproducono ``execute.py`` di ``code_eval_octopack``
  (``g++ -std=c++11 test.cpp -lcrypto -lssl``; ``javac Main.java`` e ``java -cp . Main``;
  ``node test.js``).
"""

from __future__ import annotations

import ast
import warnings
from functools import lru_cache
from pathlib import Path

from bench.config.builder import repo_root
from bench.domain.enums import Language
from bench.domain.errors import ConfigError

HARNESS_DIR = Path("third_party") / "bigcode-evaluation-harness"
HUMANEVALPACK_TASK = HARNESS_DIR / "bigcode_eval" / "tasks" / "humanevalpack.py"

# Comandi di code_eval_octopack/execute.py (unsafe_execute_cpp, _java, _js).
COMPILE_CMD: dict[Language, list[str] | None] = {
    Language.CPP: ["g++", "-std=c++11", "test.cpp", "-lcrypto", "-lssl"],
    Language.JAVA: ["javac", "Main.java"],
    Language.JAVASCRIPT: None,
}
RUN_CMD: dict[Language, list[str]] = {
    Language.CPP: ["./a.out"],
    Language.JAVA: ["java", "-cp", ".", "Main"],
    Language.JAVASCRIPT: ["node", "test.js"],
}
# Stringa rimossa dalle generazioni Java in process_results.
JAVA_EMPTY_MAIN = "public class Main {\n    }"


def harness_source(root: Path | None = None) -> str:
    """Sorgente di ``humanevalpack.py`` del submodule.

    Raises:
        ConfigError: se il submodule non è inizializzato.
    """
    path = (root or repo_root()) / HUMANEVALPACK_TASK
    if not path.is_file():
        raise ConfigError(f"{path} not found: initialise the bigcode-evaluation-harness submodule")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def import_helper() -> dict[str, list[str]]:
    """``IMPORT_HELPER`` dell'harness, letto dal sorgente senza importarlo."""
    with warnings.catch_warnings():
        # Il sorgente dell'harness contiene sequenze di escape non valide (innocue).
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(harness_source())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "IMPORT_HELPER" for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            return {str(k): [str(x) for x in v] for k, v in value.items()}
    raise ConfigError("IMPORT_HELPER not found in the harness source")


def prepare_generation(code: str, language: Language) -> str:
    """Preparazione della generazione come in ``process_results`` dell'harness."""
    if language is Language.CPP:
        cpp_imports = "\n".join(import_helper()["cpp"])
        return (cpp_imports + "\n" + code.split("int main")[0]).strip()
    if language is Language.JAVA:
        return code.replace(JAVA_EMPTY_MAIN, "").strip()
    if language is Language.JAVASCRIPT:
        return code
    raise ConfigError(f"humanevalpack execution is not defined for {language}")


def check_program(code: str, test: str, language: Language) -> str:
    """Programma eseguito: generazione preparata + ``"\\n"`` + test (``code_eval_octopack``)."""
    return prepare_generation(code, language) + "\n" + test
