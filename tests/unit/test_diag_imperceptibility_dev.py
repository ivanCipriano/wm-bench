"""``scripts/diag_imperceptibility_dev.py``: stesse caratteristiche e stessa ``C`` della fase
``adversarial_logreg``, caratteristiche verso "marcato", diff e suffissi dei nomi nuovi."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from bench.domain.enums import Language
from bench.metrics.adversarial import logreg_oof, style_features
from tests.unit.test_inventory_scripts import LIBRARY, _load, needs_library

script = _load("diag_imperceptibility_dev")


def _cell(n_problems: int = 12) -> pd.DataFrame:
    """Cella sintetica: i marcati usano nomi con suffisso ``_data`` e ``range(0, n)``."""
    rows = []
    for p in range(n_problems):
        for j in range(2):
            base = (
                f"def f{p}(n):\n    total = {j}\n    for i in range(n):\n"
                "        total += i\n    return total\n"
            )
            marked = base.replace("total", "total_data").replace("range(n)", "range(0, n)")
            rows.append({"sample_id": f"m{p}_{j}", "problem_key": f"p{p}", "code": marked,
                         "label": 1, "parent_id": f"b{p}_{j}"})  # fmt: skip
            rows.append({"sample_id": f"b{p}_{j}", "problem_key": f"p{p}", "code": base,
                         "label": 0, "parent_id": None})  # fmt: skip
    return pd.DataFrame(rows)


@needs_library
def test_suffixes_match_the_library() -> None:
    sys.path.insert(0, str(LIBRARY))
    try:
        from multimark import names
    finally:
        sys.path.remove(str(LIBRARY))
    assert script.EXTRA_SUFFIXES == names.EXTRA_SUFFIXES
    assert script.SUFFIXES == names.SUFFIXES
    assert script.TYPE_SUFFIXES == names.TYPE_SUFFIXES
    assert script.PREFIXES == names.PREFIXES


def test_same_predictions_as_the_stage() -> None:
    """Featurizzazione, scelta di ``C`` e modello dello script = ``logreg_oof`` su un fold."""
    data = _cell()
    codes = data["code"].tolist()
    labels = data["label"].to_numpy()
    groups = data["problem_key"].to_numpy()
    train = np.flatnonzero(~data["problem_key"].isin(["p0", "p1"]).to_numpy())
    test = np.flatnonzero(data["problem_key"].isin(["p0", "p1"]).to_numpy())
    seed = 123
    stage, log = logreg_oof(codes, labels, groups, [(train, test)], Language.PYTHON, seed)
    style = [style_features(c, Language.PYTHON) for c in codes]
    c, auc = script.choose_c([codes[i] for i in train], [style[i] for i in train], labels[train],
                             groups[train], seed)  # fmt: skip
    assert c == log[0]["C"] and auc == pytest.approx(log[0]["inner_auroc"])
    model, feats = script.fit([codes[i] for i in train], [style[i] for i in train],
                              labels[train], c)  # fmt: skip
    x = feats.transform([codes[i] for i in test], [style[i] for i in test])
    assert model.predict_proba(x)[:, 1] == pytest.approx(stage[test])


def test_features_block_finds_the_marked_features() -> None:
    data = _cell()
    marked = data[data["label"] == 1].drop(columns="label")
    base = data[data["label"] == 0].drop(columns="label")
    oof = data[["sample_id", "problem_key", "label"]].assign(
        score=np.where(data["label"] == 1, 0.9, 0.1), fold=0
    )
    oof.loc[oof["sample_id"] == "m3_1", "score"] = 0.99
    d = {"data": data[["sample_id", "problem_key", "code", "label"]], "marked": marked,
         "baseline": base, "oof": oof, "fold_log": [{"fold": 0, "C": 10.0}]}  # fmt: skip
    diag = script.Diagnosis(SimpleNamespace(global_seed=1, paths=SimpleNamespace(artifacts=".")),
                            top=10, n_samples=2)  # fmt: skip
    c = SimpleNamespace(method="multimark", name="B x", label="B")
    lines = diag.features_block(c, d, "m", "python")
    assert "C of the stage per fold: 10.0" in lines[0]
    assert any("total_data" in ln and "ngram" in ln for ln in lines[2:])
    samples = diag.samples_block(d)
    assert "m3_1" in samples[1] and "score 0.9900" in samples[1]
    assert any(ln.strip().startswith("+") and "total_data" in ln for ln in samples)
    suffix = diag.suffix_block(d, "python")
    assert "new names 24" in suffix[0] and "EXTRA_SUFFIXES 1.000" in suffix[0]
    assert "data 24" in suffix[1]
    assert diag.common_lines()[1] == "  B x: none"  # un solo blocco: niente in 3 celle
    for _ in range(2):
        diag.features_block(c, d, "m", "python")
    assert "ngram:'total_data' (3)" in diag.common_lines()[1]


def test_suffix_kind_and_new_names() -> None:
    assert script.suffix_kind("total_data")["extra"] and not script.suffix_kind("data")["extra"]
    assert script.suffix_kind("idx2")["digit"] and script.suffix_kind("x_1")["extra_or_digit"]
    assert script.suffix_kind("count_val")["base"] and script.suffix_kind("cur_x")["prefix"]
    before = "def f(a):\n    s = 0\n    return len(a) + s\n"
    after = "def f(a):\n    s_sum2 = 0\n    print(a)\n    return len(a) + s_sum2\n"
    assert script.new_names(after, before, "python") == {"s_sum2"}  # print è un builtin
    stats = script.suffix_stats([(after, before), (before, before)], "python")
    assert stats["n"] == 1 and stats["digit"] == 1.0 and stats["extra"] == 0.0


def test_diff_lines() -> None:
    assert script.diff_lines("a\n", "a\n") == ["(identical code)"]
    lines = script.diff_lines("x = 1\n", "x_val = 1\n")
    assert "-x = 1" in lines and "+x_val = 1" in lines
    long = script.diff_lines("\n".join(map(str, range(200))), "", limit=10)
    assert len(long) == 11 and long[-1].startswith("...")


def test_refuses_test_split_and_existing_report(tmp_path: object) -> None:
    from pathlib import Path

    out = Path(str(tmp_path)) / "r.txt"
    out.write_text("x", encoding="utf-8")
    assert script.main(["--config", "mcss:abc:mcss", "--split", "test"]) == 1
    assert script.main(["--config", "mcss:abc:mcss", "--report", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "x"
