"""Test di ProblemSplitter: conteggi della SPEC §10.4, I4 e regola D7."""

from __future__ import annotations

import random

import pytest

from bench.config.schema import ExperimentConfig, SplitConfig
from bench.data.splitter import ProblemSplitter, apply_split, family_of
from bench.domain.enums import Language, Split
from bench.domain.errors import DataError
from bench.domain.ids import derive_seed
from bench.domain.models import Problem

SEED = 20261001


def _problem(key: str, lang: Language = Language.PYTHON) -> Problem:
    return Problem(
        problem_key=key,
        dataset="d",
        level="L1",  # type: ignore[arg-type]
        language=lang,
        split=None,
        prompt_text="",
        entry_point=None,
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )


def _real_problems() -> list[Problem]:
    he = [_problem(f"humaneval/{i}") for i in range(164)]
    hep = [
        _problem(f"humaneval/{i}", lang)
        for i in range(164)
        for lang in (Language.JAVA, Language.CPP)
    ]
    mbpp = [_problem(f"mbpp/{i}") for i in range(1, 379)]
    return he + hep + mbpp


@pytest.fixture
def splitter(cfg: ExperimentConfig) -> ProblemSplitter:
    return ProblemSplitter(cfg.split, SEED)


def test_spec_counts_and_d7(splitter: ProblemSplitter) -> None:
    split = splitter.assign(_real_problems())

    def count(family: str, part: Split) -> int:
        return sum(1 for k, v in split.items() if family_of(k) == family and v is part)

    assert (count("humaneval", Split.DEV), count("humaneval", Split.TEST)) == (24, 140)
    assert (count("mbpp", Split.DEV), count("mbpp", Split.TEST)) == (72, 306)
    # 974 soluzioni MBPP originali: 378 in MBPP+ (già assegnate) + 596 extra (D7).
    extra = splitter.assign_extra([f"mbpp/{i}" for i in range(1, 975)], split)
    assert len(extra) == 596
    assert sum(v is Split.DEV for v in extra.values()) == 114
    assert sum(v is Split.TEST for v in extra.values()) == 482
    assert not set(extra) & set(split)
    assert len(split) + len(extra) == 164 + 974


def test_rule_is_first_k_by_derived_seed(splitter: ProblemSplitter) -> None:
    split = splitter.assign(_real_problems())
    he_keys = sorted(
        (k for k in split if k.startswith("humaneval/")),
        key=lambda k: (derive_seed(SEED, "split", k), k),
    )
    assert {k for k in he_keys[:24]} == {
        k for k, v in split.items() if k.startswith("humaneval/") and v is Split.DEV
    }


def test_order_independent(splitter: ProblemSplitter) -> None:
    problems = _real_problems()
    shuffled = list(problems)
    random.Random(1).shuffle(shuffled)
    assert splitter.assign(problems) == splitter.assign(shuffled)


def test_same_split_across_languages(splitter: ProblemSplitter) -> None:
    split = splitter.assign(_real_problems())
    java = apply_split([_problem(f"humaneval/{i}", Language.JAVA) for i in range(164)], split)
    python = apply_split([_problem(f"humaneval/{i}") for i in range(164)], split)
    assert [p.split for p in java] == [p.split for p in python]


def test_wrong_counts_and_unknown_family(splitter: ProblemSplitter) -> None:
    with pytest.raises(DataError, match="expected 164"):
        splitter.assign([_problem(f"humaneval/{i}") for i in range(10)])
    with pytest.raises(DataError, match="no split rule"):
        splitter.assign([_problem("codenet/p1")])


def test_extra_assignment_variants(cfg: ExperimentConfig) -> None:
    base = cfg.split.model_dump()
    for rule, expected in (("dev", 596), ("test", 0), ("proportional", 114)):
        s = ProblemSplitter(
            SplitConfig.model_validate({**base, "mbpp_extra_assignment": rule}), SEED
        )
        assert s.extra_dev_count(596) == expected


def test_apply_split_requires_every_key(splitter: ProblemSplitter) -> None:
    with pytest.raises(DataError, match="without split"):
        apply_split([_problem("humaneval/0")], {})
