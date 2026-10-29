"""``prepare_data`` del Livello 3 sulla fixture ``tiny`` (M13, ADR-016): ClassEval, CodeSearchNet
con l'esclusione D39 e The Stack C++ con i commenti di documentazione."""

from __future__ import annotations

import random

import pandas as pd
import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stages.prepare_data import (
    integration_ref,
    native_ref,
    problems_ref,
    split_ref,
)
from tests.conftest import rebuild
from tests.datafix import STACK_FILES, build_tiny_datasets, synthetic_function, tiny_config

N_PROMPTS, N_STACK_NEG = 20, 100
DOCS = ("/**\n * Returns the value of x after the steps of the computation.\n */\n",
        "// Computes a small value from the argument given by the caller.\n",
        "// Copyright 2020 Example Inc. All rights reserved.\n", "")  # fmt: skip


def _python_with_docstring(name: str, body_lines: int) -> str:
    code = synthetic_function("python", name, body_lines)
    head, rest = code.split("\n", 1)
    return f'{head}\n    """Compute the value {name} from x."""\n{rest}'


def _rewrite(datasets: object) -> None:
    """Docstring nelle funzioni Python di test, commenti sopra le funzioni di The Stack."""
    from pathlib import Path

    root = Path(str(datasets))
    path = root / "codesearchnet" / "python" / "test-00000-of-00001.parquet"
    frame = pd.read_parquet(path)
    rng = random.Random(7)
    frame["whole_func_string"] = [
        _python_with_docstring(n, rng.randint(1, 40)) for n in frame["func_name"]
    ]
    frame.to_parquet(path, index=False)
    for shard in (0, 1):
        path = root / "thestack" / "data" / "cpp" / f"data-0000{shard}-of-00110.parquet"
        frame = pd.read_parquet(path)
        frame["content"] = [
            "#include <cstdio>\n\n" + "\n".join(
                DOCS[(i + j) % len(DOCS)] + synthetic_function("cpp", f"g{j}", rng.randint(1, 40))
                for j in range(5)
            )
            for i in range(STACK_FILES)
        ]  # fmt: skip
        frame.to_parquet(path, index=False)


@pytest.fixture
def l3(cfg: ExperimentConfig) -> BenchmarkFacade:
    build_tiny_datasets(cfg.paths.datasets)
    _rewrite(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    BenchmarkFacade(tcfg).run_stage("prepare_data")  # L1: integrazione di test (D39)
    split = tcfg.model_dump(mode="json")["split"]
    split["families"] |= {"classeval": {"dev": 0, "total": 94},
                          "csn": {"dev": 0, "total": N_PROMPTS},
                          "thestack": {"dev": 0, "total": N_PROMPTS}}  # fmt: skip
    neg = tcfg.model_dump(mode="json")["negatives"]
    neg |= {"l3_prompts": N_PROMPTS, "l3_thestack_negatives": N_STACK_NEG}
    return BenchmarkFacade(rebuild(tcfg, levels=["L3"], split=split, negatives=neg))


def test_prepare_data_l3(l3: BenchmarkFacade) -> None:
    assert l3.run_stage("prepare_data").ran == 1
    store = l3.store
    split = store.read_table(split_ref("L3"))
    assert set(split["split"]) == {"test"}
    py = store.read_table(problems_ref(Language("python"), "L3"))
    assert dict(py["dataset"].value_counts()) == {"classeval": 94, "codesearchnet": N_PROMPTS}
    assert py["contamination_risk"].tolist() == [d != "classeval" for d in py["dataset"]]
    csn_py = py[py["dataset"] == "codesearchnet"]
    assert all('"""' in p and p.endswith('"""\n') for p in csn_py["prompt_text"])
    for lang in ("java", "javascript"):
        problems = store.read_table(problems_ref(Language(lang), "L3"))
        natives = store.read_table(native_ref(Language(lang), "L3"))
        l1 = set(store.read_table(integration_ref(Language(lang), "test"))["problem_key"])
        assert len(problems) == N_PROMPTS and l1
        assert not l1 & set(problems["problem_key"]) and not l1 & set(natives["problem_key"])
        assert all(p.startswith("/**\n * Compute the value") and p.endswith("{\n")
                   for p in problems["prompt_text"])  # fmt: skip
        manifest = store.read_manifest(problems_ref(Language(lang), "L3"))
        assert manifest is not None
        info = manifest.extra["sources"]["codesearchnet"]
        assert info["d39_excluded_from_negatives"] == len(l1)
        assert len(natives) == 400 - len(l1)  # CSN_PER_SPLIT: tutto lo split di test meno D39
    cpp = store.read_table(problems_ref(Language("cpp"), "L3"))
    cpp_neg = store.read_table(native_ref(Language("cpp"), "L3"))
    assert len(cpp) == N_PROMPTS and len(cpp_neg) == N_STACK_NEG
    assert set(cpp["problem_key"]) <= set(cpp_neg["problem_key"])
    assert not any("Copyright" in p for p in cpp["prompt_text"])
    assert all(
        p.lstrip().startswith(("/**", "//")) and p.endswith("{\n") for p in cpp["prompt_text"]
    )
    manifest = store.read_manifest(problems_ref(Language("cpp"), "L3"))
    assert manifest is not None
    filters = manifest.extra["sources"]["thestack_cpp"]["prompt_filters"]
    assert filters["doc_license"] > 0 and filters["no_doc"] > 0


def test_preview_l3_prompts(
    l3: BenchmarkFacade, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util

    from tests.conftest import REPO_ROOT

    l3.run_stage("prepare_data")
    spec = importlib.util.spec_from_file_location(
        "preview_l3_prompts", REPO_ROOT / "scripts" / "preview_l3_prompts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "load_experiment", lambda overrides: l3.cfg)
    assert module.main() == 0
    out = capsys.readouterr().out
    for lang in ("python", "java", "javascript", "cpp"):
        assert f"##### {lang}: problems" in out
    assert out.count("[user message]") == 2 * 4  # 2 esempi per dataset senza test e linguaggio
    assert "d39_excluded_from_prompt_pool" in out and "Complete the following C++ function." in out
