"""Fase ``prepare_data`` per il Livello 1 (SPEC §10, §15.2; ADR-004).

Produce, sotto ``data/``:

- ``splits/split.parquet``: parte (dev/test) di ogni ``problem_key`` di L1 (I4, D7);
- ``problems/L1_<lang>.parquet``: problemi con prompt;
- ``negatives/L1_<lang>_native.parquet``: negativi umani nativi (soluzioni canoniche/originali);
- ``negatives/L1_<lang>_integration_<dev|test>.parquet``: integrazione da CodeSearchNet o
  The Stack, stratificata per decili di righe.

Ogni manifest dichiara ``n_rows_expected`` dalla configurazione: l'``InvariantObserver``
fa quindi rispettare i conteggi della SPEC §10.2.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.data.loaders.base import human_sample
from bench.data.loaders.humanevalpack import HumanEvalPackLoader
from bench.data.loaders.humanevalplus import HumanEvalPlusLoader
from bench.data.loaders.mbpp_original import MbppOriginalLoader
from bench.data.loaders.mbppplus import MbppPlusLoader
from bench.data.negatives import StratifiedSampler, code_fingerprint, deduplicate
from bench.data.sources import Candidate, CodeSearchNetSource, TheStackCppSource
from bench.data.splitter import ProblemSplitter, apply_split, apply_split_samples, family_of
from bench.domain.enums import Language, Level, Split
from bench.domain.errors import ConfigError, DataError
from bench.domain.ids import derive_seed
from bench.domain.models import CodeSample, Problem
from bench.lang.functions import function_loc
from bench.lang.parsers import default_parsers
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

HEP_LANGUAGES = (Language.JAVA, Language.CPP, Language.JAVASCRIPT)
Part = Literal["dev", "test"]
PARTS: dict[Part, Split] = {"dev": Split.DEV, "test": Split.TEST}
# Split di CodeSearchNet usato per ciascuna parte (SPEC §10.4).
CSN_SPLIT: dict[Part, str] = {"dev": "validation", "test": "test"}


def split_ref() -> ArtifactRef:
    """Tabella della divisione dev/test."""
    return ArtifactRef.of("split", "data/splits/split.parquet")


def problems_ref(lang: Language) -> ArtifactRef:
    """Problemi di L1 di un linguaggio."""
    return ArtifactRef.of("problems", f"data/problems/L1_{Language(lang)}.parquet")


def native_ref(lang: Language) -> ArtifactRef:
    """Negativi umani nativi di L1 di un linguaggio."""
    return ArtifactRef.of("negatives", f"data/negatives/L1_{Language(lang)}_native.parquet")


def integration_ref(lang: Language, part: str) -> ArtifactRef:
    """Integrazione dei negativi di L1 per una parte (``dev`` o ``test``)."""
    return ArtifactRef.of(
        "negatives", f"data/negatives/L1_{Language(lang)}_integration_{part}.parquet"
    )


def _loc_stats(values: Sequence[int]) -> dict[str, float]:
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 4) if values else 0.0,
        "pstdev": round(statistics.pstdev(values), 4) if values else 0.0,
    }


@STAGES.register("prepare_data")
class PrepareDataStage(Stage):
    """Problemi, divisione e negativi umani del Livello 1."""

    name: ClassVar[str] = "prepare_data"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"split", "problems", "negatives"})
    cell_axes: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._stack_pools: dict[str, list[Candidate]] | None = None
        self._stack_info: dict[str, Any] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    # ------------------------------------------------------------------ configurazione
    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("prepare_data needs the experiment configuration")
        return self.config

    def _languages(self) -> list[Language]:
        cfg = self._cfg()
        unsupported = sorted(str(lv) for lv in cfg.levels if lv is not Level.L1)
        if unsupported:
            raise ConfigError(
                f"prepare_data: levels {unsupported} are not available yet (L2 from "
                "Milestone 9, L3/L4 from Milestone 13); run with levels=[L1]"
            )
        return [Language(lang) for lang in cfg.languages]

    def _parts(self, lang: Language) -> list[Part]:
        return sorted(self._cfg().negatives.sources.get(lang, {}))

    # ------------------------------------------------------------------ interfaccia Stage
    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        refs = [split_ref()]
        for lang in self._languages():
            refs += [problems_ref(lang), native_ref(lang)]
            refs += [integration_ref(lang, part) for part in self._parts(lang)]
        return refs

    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        langs = self._languages()
        ds = cfg.datasets
        parsers = default_parsers()
        he = HumanEvalPlusLoader(ds["humanevalplus"], parsers)
        mbpp_plus = MbppPlusLoader(ds["mbppplus"], parsers)
        mbpp_orig = MbppOriginalLoader(ds["mbpp_original"], parsers)
        hep = HumanEvalPackLoader(ds["humanevalpack"], parsers)

        # Problemi: quelli Python servono sempre, perché definiscono le famiglie dello split.
        problems: dict[Language, list[Problem]] = {
            Language.PYTHON: he.load_problems(Language.PYTHON)
            + mbpp_plus.load_problems(Language.PYTHON)
        }
        he_keys = {
            p.problem_key
            for p in problems[Language.PYTHON]
            if family_of(p.problem_key) == "humaneval"
        }
        if hep.python_keys() != he_keys:
            raise DataError("HumanEvalPack python IDs differ from HumanEval+ IDs")
        for lang in HEP_LANGUAGES:
            if lang in langs:
                problems[lang] = hep.load_problems(lang)
                if {p.problem_key for p in problems[lang]} != he_keys:
                    raise DataError(f"HumanEvalPack {lang} IDs differ from HumanEval+ IDs")

        splitter = ProblemSplitter(cfg.split, cfg.global_seed)
        split = splitter.assign([p for plist in problems.values() for p in plist])

        natives: dict[Language, list[CodeSample]] = {
            Language.PYTHON: he.load_human_negatives(Language.PYTHON)
            + mbpp_orig.load_human_negatives(Language.PYTHON)
        }
        mbpp_keys = {
            s.problem_key for s in natives[Language.PYTHON] if family_of(s.problem_key) == "mbpp"
        }
        missing = sorted({k for k in split if family_of(k) == "mbpp"} - mbpp_keys)
        if missing:
            raise DataError(f"MBPP+ problems without an original MBPP solution: {missing[:5]}")
        extra = splitter.assign_extra(mbpp_keys, split)
        full_split = {**split, **extra}
        for lang in HEP_LANGUAGES:
            if lang in langs:
                natives[lang] = hep.load_human_negatives(lang)

        self._write_split(ctx, splitter, split, full_split)
        for lang in langs:
            self._write_problems(ctx, lang, apply_split(problems[lang], split))
            assigned = apply_split_samples(natives[lang], full_split)
            native_locs = self._write_natives(ctx, lang, assigned)
            for part in self._parts(lang):
                self._write_integration(ctx, lang, part, assigned, native_locs)

    # ------------------------------------------------------------------ scritture
    def _expected_split_rows(self) -> int:
        cfg = self._cfg()
        orig = cfg.datasets["mbpp_original"].expected_rows.get("all")
        if orig is None:
            raise ConfigError("dataset mbpp_original: expected_rows.all is required")
        return cfg.split.families["humaneval"].total + orig

    def _write_split(
        self,
        ctx: StageContext,
        splitter: ProblemSplitter,
        prompt_split: dict[str, Split],
        full_split: dict[str, Split],
    ) -> None:
        rows = [
            {
                "problem_key": key,
                "family": family_of(key),
                "split": str(part),
                "role": "prompt" if key in prompt_split else "negative_only",
                "split_seed": splitter.rank(key)[0],
            }
            for key, part in sorted(full_split.items())
        ]
        df = pd.DataFrame(rows)
        counts = Counter(f"{r['family']}/{r['role']}/{r['split']}" for r in rows)
        extra = {"counts": dict(sorted(counts.items()))}
        ref = split_ref()
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref,
                n_rows_in=len(full_split),
                n_rows_out=len(df),
                n_rows_expected=self._expected_split_rows(),
                extra=extra,
            ),
        )

    def _expected_problems(self, lang: Language) -> int:
        fams = self._cfg().split.families
        if lang is Language.PYTHON:
            return fams["humaneval"].total + fams["mbpp"].total
        return fams["humaneval"].total

    def _write_problems(self, ctx: StageContext, lang: Language, problems: list[Problem]) -> None:
        df = pd.DataFrame([p.model_dump(mode="json") for p in problems])
        stats = {
            name: _loc_stats([int(x) for x in group["loc_to_generate"]])
            for name, group in df.groupby("dataset")
        }
        extra = {
            "loc_to_generate": stats,
            "split_counts": {str(k): int(v) for k, v in df["split"].value_counts().items()},
        }
        ref = problems_ref(lang)
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref,
                n_rows_in=len(problems),
                n_rows_out=len(df),
                n_rows_expected=self._expected_problems(lang),
                extra=extra,
            ),
        )

    def _expected_natives(self, lang: Language) -> int:
        ds = self._cfg().datasets
        if lang is Language.PYTHON:
            he_rows = ds["humanevalplus"].expected_rows.get("data")
            orig_rows = ds["mbpp_original"].expected_rows.get("all")
            if he_rows is None or orig_rows is None:
                raise ConfigError("expected_rows of humanevalplus/mbpp_original are required")
            return he_rows + orig_rows
        role = {Language.JAVA: "java", Language.CPP: "cpp", Language.JAVASCRIPT: "js"}[lang]
        rows = ds["humanevalpack"].expected_rows.get(role)
        if rows is None:
            raise ConfigError(f"expected_rows.{role} of humanevalpack is required")
        return rows

    def _write_natives(
        self, ctx: StageContext, lang: Language, samples: list[CodeSample]
    ) -> dict[str, int]:
        parsers = default_parsers()
        locs = {s.sample_id: function_loc(s.code, lang, parsers) for s in samples}
        df = pd.DataFrame([s.model_dump(mode="json") for s in samples])
        df["loc"] = [locs[s.sample_id] for s in samples]
        counts = Counter(f"{s.dataset}/{s.split}" for s in samples)
        extra = {"split_counts": dict(sorted(counts.items()))}
        ref = native_ref(lang)
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref,
                n_rows_in=len(samples),
                n_rows_out=len(df),
                n_rows_expected=self._expected_natives(lang),
                extra=extra,
            ),
        )
        return locs

    def _target(self, lang: Language, part: Part, natives: list[CodeSample]) -> int:
        neg = self._cfg().negatives
        own = sum(1 for s in natives if s.split is PARTS[part])
        total = neg.min_dev_negatives if part == "dev" else neg.l1_test_total
        return max(0, total - own)

    def _candidates(
        self, lang: Language, part: Part
    ) -> tuple[list[Candidate], dict[str, Any], str]:
        cfg = self._cfg()
        source = cfg.negatives.sources[lang][part]
        if source == "codesearchnet":
            csn = CodeSearchNetSource(cfg.datasets["codesearchnet"])
            own_split = CSN_SPLIT[part]
            other_split = CSN_SPLIT["test" if part == "dev" else "dev"]
            cands, stats = csn.candidates(lang, own_split)
            other = csn.repos(lang, other_split)
            kept = [c for c in cands if c.repo not in other]
            info = stats.as_dict() | {
                "csn_split": own_split,
                "dropped_repo_overlap": len(cands) - len(kept),
            }
            return kept, info, source
        if source == "thestack_cpp":
            if lang is not Language.CPP:
                raise ConfigError(f"thestack_cpp provides only C++ negatives, not {lang}")
            bucket = "l1_test" if part == "test" else "dev"
            if self._stack_pools is None:  # una sola passata sui file per dev e test
                stack = TheStackCppSource(
                    cfg.datasets["thestack_cpp"], cfg.negatives.thestack_partition, cfg.global_seed
                )
                buckets = ["l1_test" if p == "test" else "dev" for p in self._parts(lang)]
                self._stack_pools, stats = stack.candidates(buckets)
                self._stack_info = stats.as_dict()
            return self._stack_pools[bucket], self._stack_info | {"thestack_bucket": bucket}, source
        raise ConfigError(f"unknown negatives source '{source}' for {lang}/{part}")

    def _write_integration(
        self,
        ctx: StageContext,
        lang: Language,
        part: Part,
        natives: list[CodeSample],
        native_locs: dict[str, int],
    ) -> None:
        cfg = self._cfg()
        n = self._target(lang, part, natives)
        reference = [native_locs[s.sample_id] for s in natives if s.split is PARTS[part]]
        candidates, info, source = self._candidates(lang, part)
        fingerprints = [code_fingerprint(s.code) for s in natives]
        candidates, dropped_dup = deduplicate(candidates, fingerprints)
        seed = derive_seed(cfg.global_seed, "sample", source, str(lang), f"l1_{part}_negatives")
        result = StratifiedSampler(cfg.negatives.n_bins).sample(reference, candidates, n, seed)

        samples = [
            human_sample(
                problem_key=c.key,
                dataset=source,
                language=lang,
                level=Level.L1,
                code=c.code,
                contamination_risk=True,
            ).model_copy(update={"split": PARTS[part]})
            for c in result.selected
        ]
        columns = [*CodeSample.model_fields, "loc", "repo"]
        rows = [
            s.model_dump(mode="json") | {"loc": c.loc, "repo": c.repo}
            for s, c in zip(samples, result.selected, strict=True)
        ]
        df = pd.DataFrame(rows, columns=columns)
        extra = {
            "source": source,
            "seed": seed,
            "reference": _loc_stats(reference),
            "selected_loc": _loc_stats([c.loc for c in result.selected]),
            "dropped_duplicates": dropped_dup,
            "candidates_after_filters": len(candidates),
            **info,
            **result.as_dict(),
        }
        if result.moved:
            logger.warning(
                "%s/%s: %d samples moved between adjacent bins", lang, part, result.moved
            )
        ref = integration_ref(lang, part)
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref, n_rows_in=len(candidates), n_rows_out=len(df), n_rows_expected=n, extra=extra
            ),
        )
