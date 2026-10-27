"""Fase ``attack``: attacchi di robustezza sui campioni marcati (SPEC §12, §15.2; ADR-012).

Una cella = (metodo, modello, livello, linguaggio, parte, attacco, ``attack_params_hash``): il
``CellPlanner`` espande ogni attacco sulla sua griglia (``configs/attack/*.yaml``) e, sulla parte
di test, dà al metodo la configurazione congelata (ADR-013). Input: i campioni marcati della
cella. Output:
``attacked/<attacco>/<params_hash>/<metodo>/<modello>/<cfg>/<L>_<lang>_<parte>.parquet``, una riga
per campione marcato (I1) con i campi di ``CodeSample`` (``source=attacked``, ``parent_id`` = il
marcato, ``attack_status``; ``embed_status`` e ``expected_message`` del genitore, così un
inserimento ``FAILED`` resta non rilevato, I2) più:

- ``attack_detail``: JSON con i conteggi dell'attacco (es. ``n_comments``,
  ``added_token_fraction``, ``n_renamed``, ``edits``, ``tool``, ``error``);
- ``codebleu_distance``: 1 − CodeBLEU fra marcato e attaccato (0 se il codice non cambia).

Il manifest riassume l'**intensità** dell'attacco: frazione di campioni modificati, distanza media,
conteggi per stato, medie dei conteggi dell'attacco e versioni dei tool. Gli attacchi T1 girano
su CPU; i tool esterni lavorano a lotti (``ToolRunner``). Gli attacchi T2 con LLM (T2.2, T2.3)
girano su GPU (``gpu_attack_groups``) con l'attaccante caricato una volta per fase
(:class:`~bench.attacks.llm.LocalLLM`); il manifest riporta anche modello, decoding e SHA-256 dei
prompt.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import Counter
from typing import Any, ClassVar

import pandas as pd

from bench.attacks.base import Attack, AttackContext, AttackPools, resolve_attack, sample_rng
from bench.attacks.llm import LocalLLM
from bench.attacks.tools import ToolRunner
from bench.config.builder import repo_root
from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.enums import AttackStatus, Language, Source
from bench.domain.errors import ConfigError
from bench.domain.ids import sample_id
from bench.domain.models import CodeSample, Problem
from bench.hpo.grid import resolve
from bench.lang.parsers import default_parsers
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.attack_pools import attack_pool_ref
from bench.pipeline.stages.generate_baseline import problems_ref
from bench.pipeline.stages.watermark import make_adapter, watermarked_ref
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

CODEBLEU_LANG = {"python": "python", "java": "java", "cpp": "cpp", "javascript": "javascript"}
EXTRA_COLUMNS = ["attack_detail", "codebleu_distance"]


def attacked_ref(
    attack_id: str,
    params_hash: str,
    method: str,
    model_id: str,
    cfg_hash: str,
    level: str,
    language: str,
    split: str,
) -> ArtifactRef:
    """Campioni attaccati di una cella."""
    return ArtifactRef.of(
        "attacked",
        f"attacked/{attack_id}/{params_hash}/{method}/{model_id}/{cfg_hash}/"
        f"{level}_{language}_{split}.parquet",
    )


def attacked_subject(attack_id: str, params_hash: str) -> str:
    """Soggetto della rilevazione dei campioni attaccati (SPEC §15.2)."""
    return f"attacked/{attack_id}/{params_hash}"


def codebleu_distance(original: str, attacked: str, language: str) -> float:
    """1 − CodeBLEU (``codebleu==0.7.0``) fra codice di partenza e codice attaccato."""
    if original == attacked:
        return 0.0
    from codebleu import calc_codebleu

    score = calc_codebleu([original], [attacked], lang=CODEBLEU_LANG[language])["codebleu"]
    return round(1.0 - float(score), 6)


@STAGES.register("attack")
class AttackStage(Stage):
    """Attacchi sui campioni marcati di una cella."""

    name: ClassVar[str] = "attack"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"attacked"})
    cell_axes: ClassVar[tuple[str, ...]] = (
        "method",
        "model_id",
        "level",
        "language",
        "split",
        "attack_id",
    )

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._adapters: dict[str, Any] = {}
        self._llms: dict[tuple[str, int, int], LocalLLM] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def llm(self, attack: Attack) -> LocalLLM:
        """Attaccante di un attacco T2 (uno per modello e decoding, caricato una volta)."""
        p = attack.params
        key = (str(p["attacker"]), int(p["max_new_tokens"]), int(p["batch_size"]))
        if key not in self._llms:
            catalog = self._cfg().models_catalog
            if key[0] not in catalog:
                raise ConfigError(f"{attack.attack_id}: no configs/model/{key[0]}.yaml")
            self._llms[key] = LocalLLM(catalog[key[0]], max_new_tokens=key[1], batch_size=key[2])
        return self._llms[key]

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        return cell if cell.attack_id else None

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("attack needs the experiment configuration")
        return self.config

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str, str, str, str, str]:
        if not (
            cell.method and cell.model_id and cell.level and cell.language and cell.split
            and cell.attack_id and cell.attack_params_hash
        ):  # fmt: skip
            raise ConfigError(
                "attack needs method, model_id, level, language, split, attack_id and "
                f"attack_params_hash: {cell.key()}"
            )
        return (
            cell.method, cell.model_id, cell.level, cell.language, cell.split, cell.attack_id,
            cell.attack_params_hash,
        )  # fmt: skip

    def config_hash(self, cell: Cell) -> str:
        """Configurazione del metodo (congelata sulla parte di test)."""
        method = str(cell.method)
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return resolve(self._cfg(), self._adapters[method], cell.config_hash)[1]

    def attack(self, cell: Cell) -> Attack:
        return resolve_attack(str(cell.attack_id), cell.attack_params_hash, cell.language)

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split, _, _ = self._require(cell)
        refs = [
            problems_ref(level, language),
            watermarked_ref(method, model_id, self.config_hash(cell), level, language, split),
        ]
        if self.attack(cell).needs_pools:
            refs.append(attack_pool_ref(language))
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split, attack_id, params_hash = self._require(cell)
        cfg_hash = self.config_hash(cell)
        return [
            attacked_ref(attack_id, params_hash, method, model_id, cfg_hash, level, language, split)
        ]

    # ------------------------------------------------------------------ esecuzione
    def run(self, cell: Cell, ctx: StageContext) -> None:
        method, model_id, level, language, split, _, _ = self._require(cell)
        cfg_hash = self.config_hash(cell)
        marked_ref = watermarked_ref(method, model_id, cfg_hash, level, language, split)
        self.attack_and_write(ctx, cell, marked_ref, self.outputs(cell)[0], cfg_hash)

    def attack_and_write(
        self,
        ctx: StageContext,
        cell: Cell,
        parents_ref: ArtifactRef,
        ref: ArtifactRef,
        cfg_hash: str | None,
    ) -> None:
        """Attacca i campioni di ``parents_ref`` (una riga per campione, I1) e scrive ``ref``."""
        cfg = self._cfg()
        level, language = str(cell.level), str(cell.language)
        attack_id, params_hash = str(cell.attack_id), str(cell.attack_params_hash)
        attack = self.attack(cell)
        lang = Language(language)
        problems = {
            r["problem_key"]: Problem.model_validate(r)
            for r in ctx.store.read_table(problems_ref(level, language)).to_dict(orient="records")
        }
        table = ctx.store.read_table(parents_ref).sort_values(["problem_key", "sample_index"])
        parents = [
            CodeSample.model_validate(_clean(r))
            for r in table[list(CodeSample.model_fields)].to_dict(orient="records")
        ]
        pools = AttackPools()
        if attack.needs_pools:
            from bench.data.attack_pools import AttackPool

            pool = ctx.store.read_model(attack_pool_ref(language), AttackPool)
            pools = AttackPools(tuple(pool.comments), tuple(pool.names))
        tools = (
            ToolRunner(repo_root() / "tools", cfg.paths.tmp / "attack")
            if attack.needs_tools
            else None
        )
        parsers = default_parsers()
        llm = self.llm(attack) if attack.needs_llm else None
        contexts = [
            AttackContext(
                rng=sample_rng(cfg.global_seed, attack_id, params_hash, p.sample_id),
                problem=problems.get(p.problem_key),
                parsers=parsers,
                tools=tools,
                pools=pools,
                sample_id=p.sample_id,
                llm=llm,
            )
            for p in parents
        ]
        logger.info(
            "%s: %d samples, attack %s %s", cell.key(), len(parents), attack_id, attack.params
        )
        outcomes = attack.apply_many([p.code for p in parents], lang, contexts)
        rows = []
        for parent, out in zip(parents, outcomes, strict=True):
            child = parent.model_copy(
                update={
                    "sample_id": sample_id(
                        source=Source.ATTACKED,
                        problem_key=parent.problem_key,
                        language=str(parent.language),
                        model_id=parent.model_id,
                        method=parent.method,
                        config_hash=parent.config_hash,
                        key_id=parent.key_id,
                        sample_index=parent.sample_index,
                        parent_id=parent.sample_id,
                        attack_id=attack_id,
                        attack_params_hash=params_hash,
                    ),
                    "source": Source.ATTACKED,
                    "raw_output": None,
                    "code": out.code,
                    "parent_id": parent.sample_id,
                    "attack_id": attack_id,
                    "attack_params_hash": params_hash,
                    "attack_status": out.status,
                }
            )
            distance = (
                codebleu_distance(parent.code, out.code, language)
                if out.status is AttackStatus.APPLIED
                else 0.0
            )
            rows.append(
                child.model_dump(mode="json")
                | {
                    "attack_detail": json.dumps(out.detail, sort_keys=True, default=str),
                    "codebleu_distance": distance,
                }
            )
        df = pd.DataFrame(rows, columns=[*CodeSample.model_fields, *EXTRA_COLUMNS])
        df["sample_index"] = df["sample_index"].astype("Int64")
        extra = self._summary(attack, outcomes, df, tools, lang)
        if llm is not None:
            extra["attacker"] = llm.info()
        manifest = ctx.make_manifest(
            ref,
            inputs=self.inputs(cell),
            n_rows_in=len(parents),
            n_rows_out=len(df),
            n_rows_expected=len(parents),
            config_hash=cfg_hash,
            extra=extra,
        )
        ctx.store.write_table(ref, df, manifest)
        logger.info(
            "%s: status %s, changed %s, mean distance %s",
            cell.key(),
            extra["status_counts"],
            extra["changed_fraction"],
            extra["codebleu_distance_mean"],
        )

    @staticmethod
    def _summary(
        attack: Attack,
        outcomes: list[Any],
        df: pd.DataFrame,
        tools: ToolRunner | None,
        language: Language,
    ) -> dict[str, Any]:
        """Intensità dell'attacco nella cella (confrontabile fra linguaggi)."""
        statuses = Counter(str(o.status) for o in outcomes)
        applicable = len(outcomes) - statuses.get(AttackStatus.NOT_APPLICABLE.value, 0)
        changed = statuses.get(AttackStatus.APPLIED.value, 0)
        applied = df[df["attack_status"] == AttackStatus.APPLIED.value]
        numeric: dict[str, list[float]] = {}
        for out in outcomes:
            if out.status is not AttackStatus.APPLIED:
                continue
            for key, value in out.detail.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    numeric.setdefault(key, []).append(float(value))
        tool_names = sorted({str(o.detail["tool"]) for o in outcomes if "tool" in o.detail})
        return {
            "attack_id": attack.attack_id,
            "attack_params": attack.params,
            "attack_params_hash": attack.params_hash(language),
            "attack_version": attack.version,
            "catalog_version": attack.catalog_version(language),
            "status_counts": dict(statuses),
            "changed_fraction": round(changed / applicable, 6) if applicable else None,
            "codebleu_distance_mean": round(float(df["codebleu_distance"].mean()), 6)
            if len(df)
            else None,
            "codebleu_distance_mean_changed": round(float(applied["codebleu_distance"].mean()), 6)
            if len(applied)
            else None,
            "detail_means_changed": {
                k: round(statistics.fmean(v), 6) for k, v in sorted(numeric.items())
            },
            "tool_versions": {
                t: tools.version(t) for t in tool_names if tools is not None and t != "black"
            }
            | ({"black": _black_version()} if "black" in tool_names else {}),
            "parent_embed_status_counts": dict(Counter(df["embed_status"].fillna("None"))),
            **attack.manifest_extra(),
        }


def _black_version() -> str:
    from importlib.metadata import version

    return version("black")


def _clean(row: dict[Any, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        try:
            missing = bool(pd.isna(value))
        except (TypeError, ValueError):
            missing = False
        out[str(key)] = None if missing else value
    return out
