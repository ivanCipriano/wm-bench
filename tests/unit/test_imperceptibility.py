"""Impercettibilità (M12, ADR-015): misure accoppiate, fold dei classificatori, feature, NLL."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bench.domain.enums import Language
from bench.metrics import imperceptibility as imp
from bench.metrics.adversarial import check_folds, code_tokens, folds, logreg_oof, style_features
from bench.pipeline.stage import Cell
from bench.pipeline.stages.imperceptibility import PerplexityStage, references


def table(rows: list[tuple[str, str, int, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["sample_id", "problem_key", "sample_index", "code"])


def same(a: str, b: str) -> float:
    """Punteggio finto: 1 se uguali, 0 altrimenti."""
    return float(a == b)


MARKED = table([("m0", "p", 0, "x"), ("m1", "p", 1, "y"), ("m2", "q", 0, ""), ("m3", "q", 1, "z")])
BASE = table([("b0", "p", 0, "x"), ("b1", "p", 1, "x"), ("b2", "q", 0, "z"), ("b3", "q", 1, "w")])


def test_cross_mean_averages_against_each_reference_and_skips_empty() -> None:
    got = imp.cross_mean(MARKED, BASE, same)
    # p: m0 vs (x, x) = 1, m1 vs (x, x) = 0 -> 0.5; q: m2 vuoto escluso, m3 vs (z, w) = 0.5
    assert got.to_dict() == {"p": 0.5, "q": 0.5}


def test_within_mean_is_natural_variability() -> None:
    assert imp.within_mean(BASE, same).to_dict() == {"p": 1.0, "q": 0.0}


def test_empty_counts_and_is_empty() -> None:
    assert imp.empty_counts(MARKED) == (1, 4)
    assert imp.is_empty(None) and imp.is_empty("  \n") and imp.is_empty(float("nan"))
    assert not imp.is_empty("x")


def test_difference_of_means_per_problem() -> None:
    m = MARKED.assign(nll=[1.0, 3.0, 9.0, 2.0])
    b = BASE.assign(nll=[1.0, 1.0, 1.0, 3.0])
    # p: 2 - 1 = 1; q: il vuoto (9.0) è escluso -> 2 - 2 = 0
    assert imp.difference_of_means(m, b, "nll").to_dict() == {"p": 1.0, "q": 0.0}


def test_paired_by_index_and_exact_drop_empty_pairs() -> None:
    by_index = imp.paired(MARKED, BASE, ("sample_index", "sample_index"))
    assert sorted(zip(by_index["sample_id_m"], by_index["sample_id_r"], strict=True)) == [
        ("m0", "b0"), ("m1", "b1"), ("m3", "b3"),
    ]  # fmt: skip
    acw = MARKED.assign(parent_id=["b1", "b0", "b2", "b3"])
    exact = imp.paired(acw, BASE, ("parent_id", "sample_id"))
    assert sorted(zip(exact["sample_id_m"], exact["sample_id_r"], strict=True)) == [
        ("m0", "b1"), ("m1", "b0"), ("m3", "b3"),
    ]  # fmt: skip
    assert set(exact["problem_key"]) == {"p", "q"}


def test_acw_difference_of_means_equals_mean_of_exact_differences() -> None:
    """ACW (decisione dell'utente): senza vuoti la differenza delle medie per problema coincide
    con la media delle differenze esatte."""
    base = BASE.assign(nll=[1.0, 2.0, 3.0, 5.0])
    marked = BASE.assign(sample_id=["a0", "a1", "a2", "a3"], parent_id=list(BASE["sample_id"]),
                         nll=[2.0, 2.5, 3.0, 7.0])  # fmt: skip
    means = imp.difference_of_means(marked, base, "nll")
    pairs = imp.paired(marked, base, ("parent_id", "sample_id"))
    exact = imp.per_problem_of_pairs(
        pairs, (pairs["nll_m"] - pairs["nll_r"]).reset_index(drop=True)
    )
    assert np.allclose(means.to_numpy(), exact.to_numpy())


def test_bootstrap_stat_is_deterministic() -> None:
    values = pd.Series([0.1, 0.2, 0.3, 0.4])
    a = imp.bootstrap_stat(values, np.mean, seed=1, n=200)
    assert a == imp.bootstrap_stat(values, np.mean, seed=1, n=200)
    assert a[1][0] <= a[0] <= a[1][1]
    assert math.isnan(imp.bootstrap_stat(pd.Series([], dtype=float), np.mean, 1, 10)[0])


def test_folds_are_disjoint_by_problem() -> None:
    groups = pd.Series([f"p{i // 4}" for i in range(80)])
    labels = pd.Series([i % 2 for i in range(80)])
    splits = folds(groups, labels, seed=7)
    assert len(splits) == 5
    for train, test in splits:
        assert not set(groups[train]) & set(groups[test])
    with pytest.raises(ValueError, match="problems in train and test"):
        check_folds(groups, [(np.arange(0, 41), np.arange(40, 80))])


def test_style_features_and_tokens() -> None:
    code = "def add_one(xValue):\n    return xValue + 1\n"
    feats = style_features(code, Language.PYTHON)
    assert feats["name_snake"] > 0 and feats["name_camel"] > 0
    assert any(k.startswith("node_") for k in feats)
    assert code_tokens("a_b(1)+c") == ["a_b", "(", "1", ")", "+", "c"]


def test_logreg_separates_an_obvious_style() -> None:
    codes, labels, groups = [], [], []
    for i in range(40):
        codes.append(f"def f_{i}(a):\n    return a + {i}\n")  # negativi: snake_case
        codes.append(f"def fMarked{i}(aValue):\n    return aValue * {i}\n")  # positivi
        labels += [0, 1]
        groups += [f"p{i}", f"p{i}"]
    y, g = np.array(labels), np.array(groups)
    splits = folds(pd.Series(g), pd.Series(y), seed=3)
    scores, chosen = logreg_oof(codes, y, g, splits, Language.PYTHON, seed=3)
    assert not np.isnan(scores).any() and len(chosen) == 5
    from sklearn.metrics import roc_auc_score

    assert roc_auc_score(y, scores) > 0.95


def test_token_nll_with_a_uniform_model() -> None:
    torch = pytest.importorskip("torch")
    from bench.metrics.perplexity import token_nll

    vocab = 10

    class Tok:
        bos_token_id, eos_token_id, pad_token_id = 0, 0, 0

        def __call__(self, text: str, add_special_tokens: bool) -> dict[str, list[int]]:
            return {"input_ids": [1 + (ord(c) % (vocab - 1)) for c in text]}

    class Uniform:
        def eval(self) -> None:
            pass

        def __call__(self, input_ids: object, attention_mask: object) -> object:
            ids = input_ids  # type: ignore[assignment]
            return type("Out", (), {"logits": torch.zeros((*ids.shape, vocab))})()  # type: ignore[attr-defined]

    got = token_nll(Uniform(), Tok(), ["abc", "", "abcdef"], batch_size=2, device="cpu")
    assert got[0][1] == 3 and got[2][1] == 6
    assert got[0][0] == pytest.approx(math.log(vocab)) and math.isnan(got[1][0])


def test_perplexity_cells_by_source() -> None:
    base = Cell(method="stone", model_id="m", level="L1", language="python", split="test")
    assert PerplexityStage.normalize_cell(Cell(**{**base.as_dict(), "source": "canonical"})) is None
    shared = PerplexityStage.normalize_cell(Cell(**{**base.as_dict(), "source": "llm_baseline"}))
    assert shared is not None and shared.method is None
    twin = Cell(**{**base.as_dict(), "source": "baseline_twin"})
    assert PerplexityStage.normalize_cell(twin) is None  # STONE non ha la gemella
    assert PerplexityStage.normalize_cell(Cell(**{**twin.as_dict(), "method": "mcgmark"}))
    assert references("mcgmark") == ["twin", "baseline"]
    assert references("stone") == ["baseline"]
