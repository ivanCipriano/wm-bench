"""Controlli di PromptMark senza GPU per l'oracle (ambiente ``promptmark``).

    <python di promptmark> tests/oracle/promptmark_patch_check.py \\
        --original third_party/PromptMark/src --patched build/patched/promptmark/src \\
        --inputs tests/fixtures/oracle/promptmark/inputs.json --out .../patch_check.json

1. Patch 0002 con i valori di default: ``build_green_set`` (100 chiavi), ``get_red_green_sets``
   con la lista del framework e ``detect_watermark`` (soglia 2,12) danno risultati identici al
   codice originale (sola patch 0000) sui codici fissi dell'oracle.
2. Identificatori della procedura degli autori (``CodeNavigator`` di PromptMark, tutte le categorie,
   esclusi builtin e nomi comuni) sui codici fissi: il test li confronta con la reimplementazione di
   ``bench.data.promptmark_freq`` usata per la lista di frequenza.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import textwrap
import types
from typing import Any, Dict


def load(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", required=True)
    parser.add_argument("--patched", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    ns = parser.parse_args()
    with open(ns.inputs, encoding="utf-8") as handle:
        data = json.load(handle)
    hp = data["native_hparams"]

    sys.path.insert(0, ns.patched)  # llm_providers per entrambe le copie (non usato qui)
    original = load("pm_original", os.path.join(ns.original, "shared_utils.py"))
    patched = load("pm_patched", os.path.join(ns.patched, "shared_utils.py"))

    workdir = tempfile.mkdtemp(prefix="promptmark_check_")
    target = os.path.join(workdir, "results", "dataset")
    os.makedirs(target)
    with open(os.path.join(target, "humaneval_letter_freqs.json"), "w", encoding="utf-8") as h:
        json.dump(
            {"letter_freqs": hp["letter_freqs"], "total_identifiers": hp["total_identifiers"]}, h
        )
    with open(os.path.join(target, "mbpp_letter_freqs.json"), "w", encoding="utf-8") as h:
        json.dump({"letter_freqs": {}, "total_identifiers": 0}, h)

    mismatches = []
    candidates = list("abcdefghijklmnopqrstuvwxyz")[:18]
    for i in range(100):
        key = str(i * 7919 + 13)
        if original.build_green_set(key, candidates) != patched.build_green_set(key, candidates):
            mismatches.append(f"build_green_set {key}")
    key = str(data["key"])
    with contextlib.redirect_stdout(io.StringIO()):
        sets_o = original.get_red_green_sets(key, base_dir=workdir)
        sets_p = patched.get_red_green_sets(key, base_dir=workdir, g_min=8, g_max=18)
    if sets_o != sets_p:
        mismatches.append("get_red_green_sets")
    green, red, _ = sets_p
    freqs, total = patched.load_frequency_data(green, workdir)
    gamma = patched.calculate_gamma(freqs, total, green)
    identifiers: Dict[str, Any] = {}
    for item in data["codes"]:
        outcomes = []
        for module, kwargs in ((original, {}), (patched, {"z_threshold": 2.12})):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    outcomes.append(
                        module.detect_watermark("", item["code"], green, red, gamma, True, **kwargs)
                    )
            except KeyError as exc:
                outcomes.append(f"KeyError {exc}")
        if outcomes[0] != outcomes[1]:
            mismatches.append(f"detect_watermark {item['id']}")
        identifiers[item["id"]] = _authors_identifiers(patched, item["code"])
    out = {
        "mismatches": mismatches,
        "n_codes": len(data["codes"]),
        "green_letters": sorted(green),
        "gamma": gamma,
        "identifiers": identifiers,
    }
    with open(ns.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    sys.stdout.write(f"wrote {ns.out}: {len(mismatches)} mismatches\n")
    return 0


def _authors_identifiers(su: types.ModuleType, code: str) -> Any:
    """``extract_identifier_starting_letters`` del notebook degli autori (identificatori validi)."""
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return None
    nav = su.CodeNavigator()
    nav.visit(tree)
    found = (
        nav.public_classes
        | nav.non_public_classes
        | nav.non_public_funcs
        | nav.non_public_vars
        | nav.public_funcs
        | nav.public_vars
    )
    return sorted(i for i in found if i not in su.COMMON_STD_METHODS and i not in su.BUILTIN_NAMES)


if __name__ == "__main__":
    sys.exit(main())
