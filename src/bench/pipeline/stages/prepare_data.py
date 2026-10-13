"""Fase ``prepare_data`` per i Livelli 1 e 2 (SPEC §10, §15.2; ADR-004, ADR-011).

Una cella per livello. Livello 1, sotto ``data/``:

- ``splits/split.parquet``: parte (dev/test) di ogni ``problem_key`` di L1 (I4, D7);
- ``problems/L1_<lang>.parquet``: problemi con prompt;
- ``negatives/L1_<lang>_native.parquet``: negativi umani nativi (soluzioni canoniche/originali);
- ``negatives/L1_<lang>_integration_<dev|test>.parquet``: integrazione da CodeSearchNet o
  The Stack, stratificata per decili di righe.

Livello 2 (CodeNet, selezione di ``codenet_selected.yaml``):

- ``splits/split_L2.parquet``, ``problems/L2_<lang>.parquet``,
  ``negatives/L2_<lang>_native.parquet`` (5 sottomissioni accettate per problema);
- ``negatives/L1L2_<lang>_integration_dev.parquet``: integrazione dei negativi di sviluppo
  ricampionata sulla distribuzione **combinata** dei nativi di sviluppo L1+L2 (TODO 3, opzione 2
  della regola dell'utente del 13 ottobre 2026: ``M9_negatives_compare.txt``). Le righe sono quelle
  di SPEC §10.3 (``count_code_lines``), perché ``function_loc`` non si applica ai programmi
  completi di CodeNet. Sostituisce l'integrazione di sviluppo di L1 quando la calibrazione usa
  L1+L2; quella di L1 resta per le soglie provvisorie della M7 e della M8.

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
from bench.data.loaders.codenet import CodeNetLoader
from bench.data.loaders.humanevalpack import HumanEvalPackLoader
from bench.data.loaders.humanevalplus import HumanEvalPlusLoader
from bench.data.loaders.mbpp_original import MbppOriginalLoader
from bench.data.loaders.mbppplus import MbppPlusLoader
from bench.data.negatives import StratifiedSampler, code_fingerprint, deduplicate, ks_statistic
from bench.data.sources import Candidate, CodeSearchNetSource, TheStackCppSource
from bench.data.splitter import ProblemSplitter, apply_split, apply_split_samples, family_of
from bench.domain.enums import Language, Level, Split
from bench.domain.errors import ConfigError, DataError
from bench.domain.ids import derive_seed
from bench.domain.models import CodeSample, Problem
from bench.lang.functions import function_loc
from bench.lang.line_counter import count_code_lines
from bench.lang.parsers import default_parsers
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

HEP_LANGUAGES = (Language.JAVA, Language.CPP, Language.JAVASCRIPT)
N_CODENET_NEGATIVES = 5  # negativi umani per problema e linguaggio (codenet_selected.yaml)
POOLED_SEED_LABEL = "l1l2_dev_negatives"
Part = Literal["dev", "test"]
PARTS: dict[Part, Split] = {"dev": Split.DEV, "test": Split.TEST}
# Split di CodeSearchNet usato per ciascuna parte (SPEC §10.4).
CSN_SPLIT: dict[Part, str] = {"dev": "validation", "test": "test"}


def split_ref(level: str = "L1") -> ArtifactRef:
    """Tabella della divisione dev/test di un livello."""
    name = "split.parquet" if Level(level) is Level.L1 else f"split_{Level(level)}.parquet"
    return ArtifactRef.of("split", f"data/splits/{name}")


def problems_ref(lang: Language, level: str = "L1") -> ArtifactRef:
    """Problemi di un livello e di un linguaggio."""
    return ArtifactRef.of("problems", f"data/problems/{Level(level)}_{Language(lang)}.parquet")


def native_ref(lang: Language, level: str = "L1") -> ArtifactRef:
    """Negativi umani nativi di un livello e di un linguaggio."""
    path = f"data/negatives/{Level(level)}_{Language(lang)}_native.parquet"
    return ArtifactRef.of("negatives", path)


def pooled_integration_ref(lang: Language) -> ArtifactRef:
    """Integrazione dei negativi di sviluppo sulla distribuzione combinata L1+L2 (TODO 3)."""
    path = f"data/negatives/L1L2_{Language(lang)}_integration_dev.parquet"
    return ArtifactRef.of("negatives", path)


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
    cell_axes: ClassVar[tuple[str, ...]] = ("level",)

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
        unsupported = sorted(str(lv) for lv in cfg.levels if lv not in (Level.L1, Level.L2))
        if unsupported:
            raise ConfigError(
                f"prepare_data: levels {unsupported} are not available yet (L3/L4 from "
                "Milestone 13); run with levels=[L1] or levels=[L1,L2]"
            )
        return [Language(lang) for lang in cfg.languages]

    @staticmethod
    def _level(cell: Cell) -> Level:
        return Level(cell.level or "L1")

    def _parts(self, lang: Language) -> list[Part]:
        return sorted(self._cfg().negatives.sources.get(lang, {}))

    # ------------------------------------------------------------------ interfaccia Stage
    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        if self._level(cell) is Level.L1:
            return []
        # Il riferimento combinato della nuova integrazione usa i nativi di sviluppo di L1.
        return [native_ref(lang) for lang in self._languages()]

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        langs = self._languages()
        if self._level(cell) is Level.L2:
            refs = [split_ref("L2")]
            for lang in langs:
                refs += [problems_ref(lang, "L2"), native_ref(lang, "L2")]
                refs += [pooled_integration_ref(lang)] if "dev" in self._parts(lang) else []
            return refs
        refs = [split_ref()]
        for lang in langs:
            refs += [problems_ref(lang), native_ref(lang)]
            refs += [integration_ref(lang, part) for part in self._parts(lang)]
        return refs

    def run(self, cell: Cell, ctx: StageContext) -> None:
        if self._level(cell) is Level.L2:
            self._run_l2(ctx)
            return
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

    def _expected_problems(self, lang: Language, level: Level = Level.L1) -> int:
        fams = self._cfg().split.families
        if level is Level.L2:
            return fams["codenet"].total
        if lang is Language.PYTHON:
            return fams["humaneval"].total + fams["mbpp"].total
        return fams["humaneval"].total

    def _write_problems(
        self,
        ctx: StageContext,
        lang: Language,
        problems: list[Problem],
        level: Level = Level.L1,
        extra_info: dict[str, Any] | None = None,
    ) -> None:
        df = pd.DataFrame([p.model_dump(mode="json") for p in problems])
        stats = {
            name: _loc_stats([int(x) for x in group["loc_to_generate"]])
            for name, group in df.groupby("dataset")
        }
        extra = {
            "loc_to_generate": stats,
            "split_counts": {str(k): int(v) for k, v in df["split"].value_counts().items()},
            **(extra_info or {}),
        }
        ref = problems_ref(lang, str(level))
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref,
                n_rows_in=len(problems),
                n_rows_out=len(df),
                n_rows_expected=self._expected_problems(lang, level),
                extra=extra,
            ),
        )

    def _expected_natives(self, lang: Language, level: Level = Level.L1) -> int:
        ds = self._cfg().datasets
        if level is Level.L2:
            return self._cfg().split.families["codenet"].total * N_CODENET_NEGATIVES
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
        self, ctx: StageContext, lang: Language, samples: list[CodeSample], level: Level = Level.L1
    ) -> dict[str, int]:
        parsers = default_parsers()
        locs = {s.sample_id: function_loc(s.code, lang, parsers) for s in samples}
        df = pd.DataFrame([s.model_dump(mode="json") for s in samples])
        df["loc"] = [locs[s.sample_id] for s in samples]
        if level is not Level.L1:  # righe di SPEC §10.3 (programmi completi)
            df["code_lines"] = [count_code_lines(s.code, lang, parsers=parsers) for s in samples]
        counts = Counter(f"{s.dataset}/{s.split}" for s in samples)
        extra = {"split_counts": dict(sorted(counts.items()))}
        ref = native_ref(lang, str(level))
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref,
                n_rows_in=len(samples),
                n_rows_out=len(df),
                n_rows_expected=self._expected_natives(lang, level),
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

    # ------------------------------------------------------------------ Livello 2
    def _run_l2(self, ctx: StageContext) -> None:
        cfg = self._cfg()
        langs = self._languages()
        loader = CodeNetLoader(cfg.datasets["codenet"], default_parsers())
        problems = {lang: loader.load_problems(lang) for lang in langs}
        splitter = ProblemSplitter(cfg.split, cfg.global_seed)
        split = splitter.assign([p for plist in problems.values() for p in plist])
        funnel = loader.funnel()
        self._write_split_l2(ctx, splitter, split, funnel)
        for lang in langs:
            self._write_problems(
                ctx, lang, apply_split(problems[lang], split), Level.L2, {"selection": funnel}
            )
            natives = apply_split_samples(loader.load_human_negatives(lang), split)
            self._write_natives(ctx, lang, natives, Level.L2)
            if "dev" in self._parts(lang):
                self._write_pooled_integration(ctx, lang, natives)

    def _write_split_l2(
        self,
        ctx: StageContext,
        splitter: ProblemSplitter,
        split: dict[str, Split],
        funnel: list[dict[str, Any]],
    ) -> None:
        rows = [
            {
                "problem_key": key,
                "family": family_of(key),
                "split": str(part),
                "role": "prompt",
                "split_seed": splitter.rank(key)[0],
            }
            for key, part in sorted(split.items())
        ]
        df = pd.DataFrame(rows)
        counts = Counter(f"{r['family']}/{r['split']}" for r in rows)
        # Conteggio filtro per filtro della selezione (decisione dell'utente del 13 ottobre 2026).
        extra = {"counts": dict(sorted(counts.items())), "selection": funnel}
        ref = split_ref("L2")
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref,
                n_rows_in=len(split),
                n_rows_out=len(df),
                n_rows_expected=self._cfg().split.families["codenet"].total,
                extra=extra,
            ),
        )

    def _write_pooled_integration(
        self, ctx: StageContext, lang: Language, l2_natives: list[CodeSample]
    ) -> None:
        """Integrazione di sviluppo sulla distribuzione combinata dei nativi L1+L2 (TODO 3).

        Stessa procedura di ADR-004 (stessi candidati, filtri, deduplicazione, stratificazione per
        decili) con tre differenze: il riferimento sono i nativi di sviluppo di L1 **e** di L2;
        le righe sono quelle di SPEC §10.3 (``count_code_lines``); il totale di
        ``min_dev_negatives`` comprende anche i nativi di L2.
        """
        cfg = self._cfg()
        parsers = default_parsers()

        def lines(code: str) -> int:
            return count_code_lines(code, lang, parsers=parsers)

        l1_table = ctx.store.read_table(native_ref(lang))
        l1_codes = [str(c) for c in l1_table["code"]]
        l1_dev = [str(c) for c in l1_table.loc[l1_table["split"] == "dev", "code"]]
        l2_dev = [s.code for s in l2_natives if s.split is Split.DEV]
        reference = [lines(c) for c in l1_dev + l2_dev]
        n = max(0, cfg.negatives.min_dev_negatives - len(l1_dev) - len(l2_dev))

        candidates, info, source = self._candidates(lang, "dev")
        candidates = [
            Candidate(key=c.key, code=c.code, loc=lines(c.code), repo=c.repo) for c in candidates
        ]
        fingerprints = [code_fingerprint(c) for c in l1_codes + [s.code for s in l2_natives]]
        candidates, dropped_dup = deduplicate(candidates, fingerprints)
        seed = derive_seed(cfg.global_seed, "sample", source, str(lang), POOLED_SEED_LABEL)
        result = StratifiedSampler(cfg.negatives.n_bins).sample(reference, candidates, n, seed)

        samples = [
            human_sample(
                problem_key=c.key,
                dataset=source,
                language=lang,
                level=Level.L1,
                code=c.code,
                contamination_risk=True,
            ).model_copy(update={"split": Split.DEV})
            for c in result.selected
        ]
        columns = [*CodeSample.model_fields, "loc", "repo"]
        rows = [
            s.model_dump(mode="json") | {"loc": c.loc, "repo": c.repo}
            for s, c in zip(samples, result.selected, strict=True)
        ]
        df = pd.DataFrame(rows, columns=columns)
        pool = reference + [c.loc for c in result.selected]
        extra = {
            "source": source,
            "seed": seed,
            "line_measure": "count_code_lines (SPEC 10.3)",
            "reference": _loc_stats(reference),
            "reference_parts": {"l1_native_dev": len(l1_dev), "l2_native_dev": len(l2_dev)},
            "selected_loc": _loc_stats([c.loc for c in result.selected]),
            "ks_pool_vs_combined_natives": round(ks_statistic(pool, reference), 4),
            "dropped_duplicates": dropped_dup,
            "candidates_after_filters": len(candidates),
            **info,
            **result.as_dict(),
        }
        if result.moved:
            logger.warning(
                "%s/dev L1+L2: %d samples moved between adjacent bins", lang, result.moved
            )
        ref = pooled_integration_ref(lang)
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref, n_rows_in=len(candidates), n_rows_out=len(df), n_rows_expected=n, extra=extra
            ),
        )
