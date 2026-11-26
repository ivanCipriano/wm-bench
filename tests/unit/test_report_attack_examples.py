"""``scripts/report_attack_examples.py`` (P6): scelta degli esempi fissata (terzi per righe, seme),
filtro APPLIED sulle tre versioni, uscite Markdown e LaTeX mai sovrascritte, su uno store finto
(tabelle generate dal percorso: nessun artefatto vero)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from bench.hpo.freeze import FreezeRecord, FrozenCell
from tests.unit.test_inventory_scripts import _load

ex = _load("report_attack_examples")
MODEL = "qwen25_coder_7b"
HASHES = {("mcss", "main"): "hm", ("multimark", "prudent"): "hp",
          ("multimark", "high_capacity"): "hh"}  # fmt: skip
PROBLEMS = [f"humaneval/{i}" for i in range(9)]


def _code(i: int) -> str:
    return "".join(f"x{j} = {j}\n" for j in range(i + 1))


def _ids(level: str) -> list[str]:
    ids = [f"b|{level}|{p}" for p in PROBLEMS]
    for (method, _), h in HASHES.items():
        ids += [f"{kind}|{method}|{h}|{level}|{p}" for kind in ("w", "a") for p in PROBLEMS]
    return ids


def _fake_table(path: str) -> pd.DataFrame:
    """Tabelle finte dal percorso dell'artefatto (stesse colonne lette dallo script)."""
    parts = path.split("/")
    level = parts[-1].split("_")[0]
    rows: list[dict[str, Any]] = []
    if parts[0] == "baseline":
        rows = [{"sample_id": f"b|{level}|{p}", "problem_key": p, "sample_index": 0,
                 "code": _code(i)} for i, p in enumerate(PROBLEMS)]  # fmt: skip
    elif parts[0] == "watermarked":
        method, h = parts[1], parts[3]
        rows = [{"sample_id": f"w|{method}|{h}|{level}|{p}", "problem_key": p, "sample_index": 0,
                 "code": _code(i) + f"# {method} {h}\n", "embed_status": "PARTIAL", "n_sites": 3}
                for i, p in enumerate(PROBLEMS)]  # fmt: skip
    elif parts[0] == "attacked":
        method, h = parts[3], parts[5]
        rows = [{"sample_id": f"a|{method}|{h}|{level}|{p}", "problem_key": p, "sample_index": 0,
                 "code": _code(i) + "# attacked\n", "codebleu_distance": 0.25,
                 "attack_status": "UNCHANGED" if (i == 0 and method == "mcss") else "APPLIED"}
                for i, p in enumerate(PROBLEMS)]  # fmt: skip
    elif parts[0] == "detection":
        score = 0.5 if "neg_llm" in path else (2.0 if "positives" in path else 1.0)
        rows = [{"sample_id": s, "score": score, "status": "OK", "embed_status": "PARTIAL"}
                for s in _ids(level)]  # fmt: skip
    elif parts[0] == "execution":
        rows = [{"sample_id": s, "status": "PASSED", "n_passed": 3, "n_tests": 3}
                for s in _ids(level)]  # fmt: skip
    return pd.DataFrame(rows)


def _record(method: str) -> FreezeRecord:
    cells = [FrozenCell(model_id=MODEL, language="python", label=label, config_hash=h)
             for (m, label), h in HASHES.items() if m == method]  # fmt: skip
    return FreezeRecord(
        method=method, frozen_at=datetime(2026, 11, 24, tzinfo=UTC), levels=["L1", "L2"],
        grid_sha256=None, candidate_hashes=[], cells=cells,
    )  # fmt: skip


def test_choose_is_fixed_by_length_thirds_and_seed() -> None:
    lengths = {(MODEL, f"p{i}", 0): i for i in range(9)}
    picked = ex.choose(lengths, "T1.1", 3)
    assert [s for s, _ in picked] == ["corto", "medio", "lungo"]
    assert [lengths[k] < 3 for _, k in picked] == [True, False, False]
    assert 3 <= lengths[picked[1][1]] < 6 and lengths[picked[2][1]] >= 6
    assert picked == ex.choose(dict(reversed(list(lengths.items()))), "T1.1", 3)  # ordine
    assert [s for s, _ in ex.choose(lengths, "mark L2", 2)] == ["corto", "lungo"]
    assert ex.choose({}, "x", 3) == [] and len(ex.choose({(MODEL, "p", 0): 1}, "x", 3)) == 1


def test_tex_escape_and_listing_guard() -> None:
    assert ex.tex_escape("a_b & 50% #1") == r"a\_b \& 50\% \#1"
    assert "\\end{lstlisting}" in ex.listing("x = 1\n", "Python", "t")
    with pytest.raises(ValueError):
        ex.listing("s = r'\\end{lstlisting}'\n", "Python", "t")


def test_report_end_to_end_on_a_fake_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "load_freeze", lambda store, method, key_id="k1": _record(method))
    monkeypatch.setattr(ex.Reader, "table", lambda self, ref: _fake_table(str(ref.path)))
    monkeypatch.setattr(ex.Reader, "threshold", lambda self, m, model, h, key_id="k1": 1.5)
    fake_cfg = SimpleNamespace(paths=SimpleNamespace(artifacts=tmp_path / "artifacts"))
    monkeypatch.setattr(ex, "load_experiment", lambda overrides: fake_cfg)
    md, tex = tmp_path / "P6_examples_test.md", tmp_path / "examples"
    monkeypatch.setattr(sys, "argv", ["x", "--md", str(md), "--tex-dir", str(tex)])
    assert ex.main() == 0
    text = md.read_text(encoding="utf-8")
    assert f"SEED = {ex.SEED}" in text and "Problemi di lettura:** nessuno" in text
    titles = [line for line in text.splitlines() if line.startswith("### ")]
    assert sum(t.startswith("### L1 ") for t in titles) == 3
    assert sum(t.startswith("### L2 ") for t in titles) == 2
    for label, *_ in ex.ATTACKS:
        assert sum(t.startswith(f"### {label} ") for t in titles) == 3, label
    assert "humaneval/0 " not in text.split("## Attacchi")[1]  # mcss UNCHANGED: escluso
    assert "2.000 (rilevato)" in text and "1.000 (non rilevato)" in text
    assert "0.500 (non rilevato)" in text and "PASSED 3/3" in text
    assert "punteggio dopo (k2)" in text and "Parametri del punto: `{'k': 2}`" in text
    files = sorted(p.name for p in tex.iterdir())
    assert len(files) == 1 + 5 + 3 * len(ex.ATTACKS) and "preamble.tex" in files
    sample = (tex / "attack_T1-4-natural-r-1-0_corto.tex").read_text(encoding="utf-8")
    assert sample.count("\\begin{lstlisting}") == 9 and "\\begin{tabular}" in sample
    before = text
    assert ex.main() == 1 and md.read_text(encoding="utf-8") == before  # mai sovrascritto
