"""La preparazione dei programmi HumanEvalPack coincide con bigcode-evaluation-harness."""

from __future__ import annotations

import pytest

from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.execution import harness


def test_import_helper_is_read_from_the_submodule() -> None:
    helper = harness.import_helper()
    assert helper["cpp"][0] == "using namespace std;"
    assert "#include<boost/any.hpp>" in helper["cpp"]
    assert "import numpy as np" in helper["python"]


def test_replicated_lines_are_still_in_the_harness() -> None:
    # Se il submodule cambia queste righe, la replica in harness.py va riallineata.
    source = harness.harness_source()
    assert 'cpp_imports = "\\n".join(IMPORT_HELPER["cpp"])' in source
    assert '[(cpp_imports + "\\n" + g.split("int main")[0]).strip() for g in gen]' in source
    assert '[g.replace("public class Main {\\n    }", "").strip() for g in gen]' in source
    assert 'language = self.DATASET_NAME if self.DATASET_NAME != "js" else "javascript"' in source
    assert 'return "\\n" + doc["test"]' in source


def test_cpp_program() -> None:
    code = "#include<vector>\nint add(int a, int b){ return a + b; }\nint main(){ return 1; }\n"
    program = harness.check_program(code, "int main(){ assert(add(1,2)==3); }", Language.CPP)
    imports = "\n".join(harness.import_helper()["cpp"])
    assert program.startswith(imports + "\n#include<vector>")
    assert program.count("int main") == 1  # quello del modello è stato tagliato
    assert program.endswith("\nint main(){ assert(add(1,2)==3); }")


def test_java_program() -> None:
    code = "class Solution {\n}\npublic class Main {\n    }\n"
    program = harness.check_program(code, "public class Main {}", Language.JAVA)
    assert program == "class Solution {\n}\npublic class Main {}"


def test_javascript_program_is_unchanged() -> None:
    code = "const f = (x) => x\n"
    assert harness.check_program(code, "f(1)", Language.JAVASCRIPT) == code + "\nf(1)"


def test_python_is_not_a_humanevalpack_language() -> None:
    with pytest.raises(ConfigError):
        harness.prepare_generation("x = 1", Language.PYTHON)
