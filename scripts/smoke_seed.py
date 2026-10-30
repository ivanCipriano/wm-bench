"""Preparazione della cartella dello smoke test (fase 2 della chiusura, M13_M14_plan.md).

Uso (con bench-core, nodo di login, nessun job; pochi minuti):
    python scripts/smoke_seed.py            # crea $WMB/smoke/artifacts (si ferma se esiste già)

Scrive **solo** sotto ``$WMB/smoke`` (``WMB_WRITE_ROOT``; ogni copia controlla la destinazione) e
legge gli artefatti veri senza modificarli. Nessun dato di **test** entra nello smoke test
(condizione dell'utente del 30 ottobre 2026 per il congelamento finto di SWEET):

- L1 e L2: per dataset e linguaggio due problemi di **sviluppo**, uno resta ``dev`` e l'altro è
  rietichettato ``test`` (in L2 di preferenza un programma con almeno 2 funzioni, per T2.4k), con i
  loro negativi nativi; tre negativi di integrazione per parte (quelli di test presi da sviluppo);
- L3 (solo test nel benchmark): problemi costruiti da fonti fuori dal test: una classe di ClassEval
  **esclusa** (oracle di MCGMark, D21), una funzione di CodeSearchNet **validation** per linguaggio,
  una funzione C++ del bucket ``dev`` di The Stack; i negativi sono le loro canoniche;
- copie identiche (stesso sha256) di: ground truth di EvalPlus, raccolte degli attacchi, liste di
  frequenza di PromptMark, congelamenti e soglie L1+L2 dei metodi congelati;
- **congelamento finto di SWEET**: scelta e soglie definitive su L1+L2 della M9 copiate, record
  ``freeze/sweet.json`` scritto solo nello smoke test (estensione D31 non compresa).

Le baseline non si copiano: le generano i passi ``base/*`` delle catene nello smoke test.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from bench.attacks.t2.truncate_k import MIN_FUNCTIONS
from bench.config.builder import load_experiment
from bench.data.l3_prompts import brace_prompt, cpp_prompts, python_prompt
from bench.data.loaders.base import normalize_newlines, read_parquet
from bench.data.loaders.classeval import ClassEvalLoader, classeval_key
from bench.data.loaders.codesearchnet import COLUMNS, csn_key, human_negative, problem_of
from bench.data.sources import STACK_COLUMNS, TheStackCppSource
from bench.domain.enums import Language, Level, Split
from bench.domain.models import CodeSample, Problem, SelectedConfig
from bench.hpo.freeze import (
    FINAL_LEVELS,
    FreezeRecord,
    FrozenCell,
    data_sha256,
    freeze_ref,
    load_freeze,
)
from bench.lang.functions import outermost_functions
from bench.lang.parsers import default_parsers
from bench.pipeline.stages.attack_pools import attack_pool_ref
from bench.pipeline.stages.calibrate import threshold_ref
from bench.pipeline.stages.evalplus_groundtruth import groundtruth_ref
from bench.pipeline.stages.prepare_data import (
    integration_ref,
    native_ref,
    pooled_integration_ref,
    problems_ref,
)
from bench.pipeline.stages.promptmark_freq import promptmark_freq_ref
from bench.pipeline.stages.tune import selected_ref
from bench.store.artifact_store import WRITE_ROOT_ENV, ArtifactStore
from bench.store.refs import ArtifactRef

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")
FROZEN = ("stone", "mcgmark", "acw", "promptmark")
ALL_LANGS = [Language.PYTHON, Language.JAVA, Language.CPP, Language.JAVASCRIPT]
SWEET_LANGS = [str(x) for x in ALL_LANGS]


class Seeder:
    def __init__(self, real: ArtifactStore, smoke: ArtifactStore, root: Path) -> None:
        self.real, self.smoke, self.root = real, smoke, root.resolve()
        self.parsers = default_parsers()
        self.log: list[str] = []

    def _guard(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.root):
            raise SystemExit(f"refusing to write {path}: outside {self.root}")

    def copy(self, ref: ArtifactRef) -> bool:
        """Copia identica di dati e manifest (stesso sha256)."""
        if not self.real.exists(ref):
            self.log.append(f"missing in the real store: {ref.path}")
            return False
        for src, dst in (
            (self.real.path_of(ref), self.smoke.path_of(ref)),
            (self.real.manifest_of(ref), self.smoke.manifest_of(ref)),
        ):
            self._guard(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        self.log.append(f"copied {ref.path}")
        return True

    def write(self, ref: ArtifactRef, table: pd.DataFrame, origin: ArtifactRef | None) -> None:
        """Tabella ridotta con il manifest dell'originale (``extra.smoke_seed``)."""
        self._guard(self.smoke.path_of(ref))
        manifest = self.real.read_manifest(origin) if origin is not None else None
        if manifest is None:
            manifest = self.real.read_manifest(problems_ref(Language.PYTHON))
        assert manifest is not None
        extra = {**(manifest.extra or {}), "smoke_seed": True}
        manifest = manifest.model_copy(
            update={
                "extra": extra,
                "n_rows_out": len(table),
                "n_rows_expected": None,
                "finished_at": None,
            }
        )
        self.smoke.write_table(ref, table.reset_index(drop=True), manifest)
        self.log.append(f"wrote {ref.path} ({len(table)} rows)")

    # ----------------------------------------------------------------- L1 e L2
    def level(self, level: str, lang: Language) -> None:
        ref = problems_ref(lang, level)
        problems = self.real.read_table(ref)
        dev = problems[problems["split"] == "dev"].sort_values("problem_key")
        chosen: dict[str, str] = {}
        for _, group in dev.groupby("dataset"):
            keys = list(group["problem_key"])
            if level == "L2":  # di preferenza un programma con almeno 2 funzioni (T2.4k)
                multi = [
                    k
                    for k, code in zip(
                        group["problem_key"], group["canonical_solution"], strict=True
                    )
                    if self.functions(str(code), lang) >= MIN_FUNCTIONS
                ]
                keys = [*multi, *[k for k in keys if k not in multi]]
            chosen[keys[0]] = "dev"
            if len(keys) > 1:
                chosen[keys[1]] = "test"
        picked = problems[problems["problem_key"].isin(chosen)].copy()
        picked["split"] = picked["problem_key"].map(chosen)
        self.write(ref, picked, ref)
        nat = native_ref(lang, level)
        natives = self.real.read_table(nat)
        natives = natives[natives["problem_key"].isin(chosen)].copy()
        natives["split"] = natives["problem_key"].map(chosen)
        self.write(nat, natives, nat)
        if level == "L1":
            for part in ("dev", "test"):
                iref = integration_ref(lang, part)
                if self.real.exists(integration_ref(lang, "dev")) and (
                    part == "dev" or self.real.exists(iref)
                ):
                    rows = self.real.read_table(integration_ref(lang, "dev")).head(3).copy()
                    rows["split"] = part  # quelli di test presi dallo sviluppo
                    self.write(iref, rows, integration_ref(lang, "dev"))
        else:
            pref = pooled_integration_ref(lang)
            if self.real.exists(pref):
                self.write(pref, self.real.read_table(pref).head(3), pref)

    def functions(self, code: str, lang: Language) -> int:
        return len(outermost_functions(self.parsers.parse(code, lang).root_node, lang))

    # ----------------------------------------------------------------- L3
    def level3(self, cfg: Any) -> None:
        problems: dict[Language, list[Problem]] = {lang: [] for lang in ALL_LANGS}
        loader = ClassEvalLoader(cfg.datasets["classeval"])
        excluded = sorted(loader.excluded)[0]  # classe dell'oracle, fuori dal benchmark
        row = next(r for r in loader.all_rows if classeval_key(r["task_id"]) == excluded)
        problems[Language.PYTHON].append(
            Problem(
                problem_key=excluded,
                dataset="classeval",
                level=Level.L3,
                language=Language.PYTHON,
                split=Split.TEST,
                prompt_text=normalize_newlines(row["skeleton"]),
                entry_point=row["class_name"],
                canonical_solution=normalize_newlines(row["solution_code"]),
                test_ref=row["task_id"],
                contamination_risk=False,
                loc_to_generate=1,
            )
        )
        csn = cfg.datasets["codesearchnet"]
        for lang in (Language.PYTHON, Language.JAVA, Language.JAVASCRIPT):
            frame = read_parquet(csn.file(f"{lang}/validation"), COLUMNS)
            for r in frame.head(200).itertuples(index=False):
                code = normalize_newlines(str(r.whole_func_string))
                doc = str(r.func_documentation_string or "")
                if not doc.strip():
                    continue
                fn = (
                    python_prompt(code, self.parsers)
                    if lang is Language.PYTHON
                    else brace_prompt(code, lang, doc, self.parsers)
                )
                if not isinstance(fn, str):
                    problems[lang].append(
                        problem_of(csn_key(lang, str(r.func_code_url)), lang, fn).model_copy(
                            update={"split": Split.TEST}
                        )
                    )
                    break
        stack = TheStackCppSource(
            cfg.datasets["thestack_cpp"],
            cfg.negatives.thestack_partition,
            cfg.global_seed,
            self.parsers,
        )
        import pyarrow.parquet as pq

        table = pq.read_table(
            stack.spec.file(sorted(stack.spec.files)[0]), columns=list(STACK_COLUMNS)
        )
        for row in table.to_pylist():
            if stack.bucket(str(row["max_stars_repo_name"] or row["hexsha"])) != "dev":
                continue
            found, _ = cpp_prompts(normalize_newlines(str(row["content"] or "")), self.parsers)
            if found:
                pf = found[0]
                problems[Language.CPP].append(
                    Problem(
                        problem_key=f"thestack/{row['hexsha']}:{pf.start_line}",
                        dataset="thestack_cpp",
                        level=Level.L3,
                        language=Language.CPP,
                        split=Split.TEST,
                        prompt_text=pf.prompt,
                        entry_point=pf.entry_point,
                        canonical_solution=pf.code,
                        test_ref=None,
                        contamination_risk=True,
                        loc_to_generate=pf.loc_to_generate,
                    )
                )
                break
        for lang, items in problems.items():
            origin = problems_ref(lang, "L3")
            self.write(origin, pd.DataFrame([p.model_dump(mode="json") for p in items]), origin)
            natives = [
                (human_negative(_cand(p), lang, p.dataset)).model_copy(update={"split": Split.TEST})
                for p in items
            ]
            df = pd.DataFrame(
                [s.model_dump(mode="json") for s in natives], columns=list(CodeSample.model_fields)
            )
            self.write(native_ref(lang, "L3"), df, native_ref(lang, "L3"))

    # ----------------------------------------------------------------- riferimenti e SWEET
    def references(self) -> None:
        self.copy(groundtruth_ref())
        self.copy(promptmark_freq_ref("python"))
        for lang in ALL_LANGS:
            self.copy(attack_pool_ref(str(lang)))
        for method in FROZEN:
            record = load_freeze(self.real, method)
            if record is None:
                self.log.append(f"{method} not frozen in the real store")
                continue
            self.copy(freeze_ref(method))
            for cell in record.cells:
                if cell.config_hash:
                    self.copy(
                        threshold_ref(
                            method, cell.model_id, cell.language, cell.config_hash, FINAL_LEVELS
                        )
                    )

    def fake_sweet_freeze(self) -> None:
        cells = []
        for model in MODELS:
            for lang in SWEET_LANGS:
                sel_ref = selected_ref("sweet", model, lang, FINAL_LEVELS)
                if not self.copy(sel_ref):
                    continue
                selected = self.smoke.read_model(sel_ref, SelectedConfig)
                thr = threshold_ref("sweet", model, lang, str(selected.config_hash), FINAL_LEVELS)
                if not self.copy(thr):
                    continue
                cells.append(
                    FrozenCell(
                        model_id=model,
                        language=lang,
                        config_hash=selected.config_hash,
                        hparams=dict(selected.hparams),
                        selected_sha256=data_sha256(self.smoke, sel_ref),
                        threshold_sha256=data_sha256(self.smoke, thr),
                        constraint_satisfied=selected.constraint_satisfied,
                    )
                )
        record = FreezeRecord(
            method="sweet",
            frozen_at=datetime.now(UTC),
            levels=list(FINAL_LEVELS),
            grid_sha256=None,
            candidate_hashes=[],
            cells=cells,
        )
        ref = freeze_ref("sweet")
        self._guard(self.smoke.path_of(ref))
        manifest = self.real.read_manifest(freeze_ref("stone"))
        assert manifest is not None
        self.smoke.write_model(
            ref,
            record,
            manifest.model_copy(
                update={"extra": {"smoke_seed": True, "fake_freeze": True}, "finished_at": None}
            ),
        )
        self.log.append(f"wrote the FAKE freeze of sweet ({len(cells)} cells), smoke test only")


def _cand(p: Problem) -> Any:
    from bench.data.sources import Candidate

    return Candidate(p.problem_key, p.canonical_solution or "", 1, "")


def main() -> int:
    cfg = load_experiment(["paths=cluster", "stage=selftest"])
    root = Path(cfg.paths.wmb) / "smoke"
    artifacts = root / "artifacts"
    if artifacts.exists():
        sys.stdout.write(f"{artifacts} exists: the smoke test is already seeded\n")
        return 1
    os.environ[WRITE_ROOT_ENV] = str(root)
    seeder = Seeder(ArtifactStore(Path(cfg.paths.artifacts)), ArtifactStore(artifacts), root)
    for level in ("L1", "L2"):
        for lang in ALL_LANGS:
            seeder.level(level, lang)
    seeder.level3(cfg)
    seeder.references()
    seeder.fake_sweet_freeze()
    sys.stdout.write("\n".join(seeder.log) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
