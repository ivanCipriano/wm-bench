"""Costruzione della fixture ``tiny`` nella disposizione dei dataset di cluster_info §8."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pandas as pd

from bench.config.schema import ExperimentConfig
from tests.conftest import REPO_ROOT, rebuild

TINY = REPO_ROOT / "tests" / "fixtures" / "tiny"

# Conteggi della fixture (cfr. SPEC §10.2 per i valori reali).
HE_TOTAL, HE_DEV = 5, 2
MBPP_TOTAL, MBPP_DEV = 5, 1
MBPP_ORIG_TOTAL = 12
MIN_DEV, L1_TEST_TOTAL = 20, 13
CSN_PER_SPLIT = 400
STACK_FILES, STACK_FUNCS = 200, 5


def _jsonl(name: str) -> list[dict[str, Any]]:
    with open(TINY / name, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def synthetic_function(lang: str, name: str, body_lines: int) -> str:
    """Funzione sintetica con ``body_lines`` istruzioni nel corpo."""
    if lang == "python":
        body = "".join(f"    y{j} = x + {j}\n" for j in range(body_lines))
        return f"def {name}(x):\n{body}    return x\n"
    if lang == "java":
        body = "".join(f"    int y{j} = x + {j};\n" for j in range(body_lines))
        return f"public int {name}(int x) {{\n{body}    return x;\n}}"
    if lang == "javascript":
        body = "".join(f"  var y{j} = x + {j};\n" for j in range(body_lines))
        return f"function {name}(x) {{\n{body}  return x;\n}}"
    body = "".join(f"    int y{j} = x + {j};\n" for j in range(body_lines))
    return f"int {name}(int x) {{\n{body}    return x;\n}}\n"


def _csn_frame(lang: str, split: str, rng: random.Random) -> pd.DataFrame:
    rows = []
    for i in range(CSN_PER_SPLIT):
        code = synthetic_function(lang, f"f_{split}_{i}", rng.randint(1, 40))
        rows.append(
            {
                "repository_name": f"org/{split}-repo{i % 25}",
                "func_path_in_repository": f"src/file{i}.x",
                "func_name": f"f_{split}_{i}",
                "whole_func_string": code,
                "language": lang,
                "func_code_string": code,
                "func_code_url": f"https://example.org/{lang}/{split}/{i}",
            }
        )
    return pd.DataFrame(rows)


def _stack_frame(shard: int, rng: random.Random) -> pd.DataFrame:
    rows = []
    for i in range(STACK_FILES):
        funcs = [synthetic_function("cpp", f"g{j}", rng.randint(1, 40)) for j in range(STACK_FUNCS)]
        rows.append(
            {
                "content": "#include <cstdio>\n\n" + "\n".join(funcs),
                "max_stars_repo_name": f"stack/repo{shard}-{i}",
                "max_stars_repo_path": f"src/f{i}.cpp",
                "hexsha": f"{shard:02d}{i:038d}",
                "lang": "C++",
            }
        )
    return pd.DataFrame(rows)


def build_tiny_datasets(datasets: Path) -> None:
    """Scrive la fixture sotto ``datasets`` con i nomi di file della configurazione reale."""
    (datasets / "evalplus").mkdir(parents=True, exist_ok=True)
    for name, target in (
        ("humanevalplus.jsonl", "HumanEvalPlus-v0.1.10.jsonl"),
        ("mbppplus.jsonl", "MbppPlus-v0.2.0.jsonl"),
    ):
        (datasets / "evalplus" / target).write_text(
            (TINY / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    orig = pd.DataFrame(_jsonl("mbpp_original.jsonl"))
    (datasets / "mbpp" / "full").mkdir(parents=True, exist_ok=True)
    for role in ("train", "test", "validation", "prompt"):
        part = orig[orig["role"] == role][["task_id", "code"]].reset_index(drop=True)
        part.to_parquet(datasets / "mbpp" / "full" / f"{role}-00000-of-00001.parquet", index=False)

    hep = pd.DataFrame(_jsonl("humanevalpack.jsonl"))
    for lang, frame in hep.groupby("dir"):
        out = datasets / "humanevalpack" / str(lang)
        out.mkdir(parents=True, exist_ok=True)
        frame.drop(columns=["dir"]).to_parquet(out / "test-00000-of-00001.parquet", index=False)

    rng = random.Random(20261001)
    for lang in ("python", "java", "javascript"):
        out = datasets / "codesearchnet" / lang
        out.mkdir(parents=True, exist_ok=True)
        for split in ("train", "validation", "test"):
            _csn_frame(lang, split, rng).to_parquet(
                out / f"{split}-00000-of-00001.parquet", index=False
            )

    out = datasets / "thestack" / "data" / "cpp"
    out.mkdir(parents=True, exist_ok=True)
    for shard in (0, 1):
        _stack_frame(shard, rng).to_parquet(out / f"data-0000{shard}-of-00110.parquet", index=False)


def tiny_config(cfg: ExperimentConfig) -> ExperimentConfig:
    """Configurazione reale con i conteggi della fixture al posto di quelli della SPEC."""
    data = cfg.model_dump(mode="json")
    ds = data["datasets"]
    ds["humanevalplus"]["expected_rows"] = {"data": HE_TOTAL}
    ds["mbppplus"]["expected_rows"] = {"data": MBPP_TOTAL}
    ds["mbpp_original"]["expected_rows"] = {
        "prompt": 10,
        "test": 2,
        "train": 0,
        "validation": 0,
        "all": MBPP_ORIG_TOTAL,
    }
    ds["humanevalpack"]["expected_rows"] = {k: HE_TOTAL for k in ("python", "js", "java", "cpp")}
    ds["codesearchnet"]["expected_rows"] = {}
    ds["thestack_cpp"]["expected_rows"] = {"shard0": STACK_FILES, "shard1": STACK_FILES}
    split = data["split"]
    split["families"] = {
        "humaneval": {"dev": HE_DEV, "total": HE_TOTAL},
        "mbpp": {"dev": MBPP_DEV, "total": MBPP_TOTAL},
    }
    neg = data["negatives"]
    neg["min_dev_negatives"] = MIN_DEV
    neg["l1_test_total"] = L1_TEST_TOTAL
    neg["thestack_partition"] = {"l3_test": 0.25, "l1_test": 0.25, "dev": 0.25, "promptmark": 0.25}
    data["levels"] = ["L1"]
    return rebuild(cfg, **{k: data[k] for k in ("datasets", "split", "negatives", "levels")})


def expected_tiny_counts() -> dict[str, int]:
    """Conteggi attesi della fixture (calcolati come la SPEC calcola quelli reali)."""
    extra = MBPP_ORIG_TOTAL - MBPP_TOTAL
    extra_dev = int(extra * MBPP_DEV / MBPP_TOTAL + 0.5)
    py_dev = HE_DEV + MBPP_DEV + extra_dev
    py_test = HE_TOTAL + MBPP_ORIG_TOTAL - py_dev
    return {
        "python_problems": HE_TOTAL + MBPP_TOTAL,
        "hep_problems": HE_TOTAL,
        "python_native": HE_TOTAL + MBPP_ORIG_TOTAL,
        "python_native_dev": py_dev,
        "python_native_test": py_test,
        "extra_dev": extra_dev,
        "python_integration_dev": MIN_DEV - py_dev,
        "hep_integration_dev": MIN_DEV - HE_DEV,
        "hep_integration_test": L1_TEST_TOTAL - (HE_TOTAL - HE_DEV),
        "split_rows": HE_TOTAL + MBPP_ORIG_TOTAL,
    }
