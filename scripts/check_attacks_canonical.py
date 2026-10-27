"""Gli attacchi T1 preservano la semantica? Verifica sulle soluzioni canoniche di L1 (SPEC §12,
§21.3; decisioni dell'utente del 16 ottobre 2026, ADR-012).

Uso (su un nodo defq con Apptainer; vedi scripts/check_attacks_canonical.sh):
    python scripts/check_attacks_canonical.py [--languages python,java,cpp,javascript]
                                              [--attacks T1.1,T1.2,T1.3,T1.4,T1.5]

Per ogni linguaggio e configurazione T1 applica l'attacco alle canoniche di L1 (sviluppo e test,
lo stesso programma che l'executor esegue per le canoniche) e le esegue con gli executor di L1.
Riporta: canoniche che passano senza attacco (dall'esecuzione delle canoniche della M4), passano
dopo l'attacco, stati dell'attacco, frazione modificata, 1 − CodeBLEU medio; per ogni canonica
**rotta** (passa senza attacco, non passa dopo) il problema, l'esito e un estratto della
differenza. Per T1.2 su Java ogni canonica rotta si riattacca **una ricetta alla volta** di
CommonStaticAnalysis (docs/attacks/t1_2_java_recipes.txt) per trovare quella che la rompe: le
ricette non si tolgono senza la decisione dell'utente.

Il seme di ogni canonica è quello di SPEC §18 con il ``sample_id`` della canonica.

T2.1 (decisioni dell'utente del 26 ottobre 2026): ``--attacks T2.1 --params b=all`` sulle
canoniche; con ``--baseline-dev`` l'attacco si applica invece ai campioni della **baseline di L1
dev** di entrambi i modelli e si riporta, per linguaggio e modello, la frazione dei campioni che
passavano prima e passano ancora (attesa: almeno il 99%). Per ogni campione (o canonica) rotto da
T2.1 si riapplica **un tipo di regola alla volta** fra quelli applicati, per trovare quello che lo
rompe: le regole non si tolgono senza la decisione dell'utente.
"""

from __future__ import annotations

import argparse
import difflib
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bench.attacks.base import AttackContext, AttackPools, grid_attacks, sample_rng
from bench.attacks.t1.common import statement_sites
from bench.attacks.t1.local_edits import LOG_LANGUAGES, _excluded_site, log_sites
from bench.attacks.tools import ToolRunner
from bench.config.builder import load_experiment, repo_root
from bench.data.attack_pools import AttackPool
from bench.domain.enums import AttackStatus, Language, Source
from bench.domain.ids import sample_id
from bench.domain.models import Problem
from bench.execution import sandbox as sandbox_mod
from bench.execution.executors import SampleToRun
from bench.lang.parsers import default_parsers
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import codebleu_distance
from bench.pipeline.stages.attack_pools import attack_pool_ref
from bench.pipeline.stages.execute import ExecuteStage, execution_ref, max_workers
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.store.artifact_store import ArtifactStore

LEVEL = "L1"
RECIPES_FILE = repo_root() / "docs" / "attacks" / "t1_2_java_recipes.txt"
MIN_PRESERVED = 0.99  # baseline di L1 dev: soglia attesa dall'utente per T2.1


def _recipes() -> list[str]:
    """Ricette attive di CommonStaticAnalysis per Java (le ricette Kotlin non toccano Java)."""
    lines = RECIPES_FILE.read_text(encoding="utf-8").split("## Attive", 1)[1].split("##", 1)[0]
    return [r.strip() for r in lines.splitlines() if r.strip() and ".kotlin." not in r]


def _diff(a: str, b: str, limit: int = 30) -> str:
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=1))
    return "\n".join(f"      {line}" for line in lines[2 : 2 + limit])


class Checker:
    """Esecuzione delle canoniche (con e senza attacco) di un linguaggio."""

    def __init__(self, cfg: Any, store: ArtifactStore, language: str) -> None:
        self.cfg = cfg
        self.store = store
        self.language = language
        table = store.read_table(problems_ref(LEVEL, language))
        self.problems = sorted(
            (Problem.model_validate(r) for r in table.to_dict(orient="records")),
            key=lambda p: p.problem_key,
        )
        stage = ExecuteStage(cfg)
        cell = Cell(source="canonical", level=LEVEL, language=language)
        self.sandbox = sandbox_mod.make_sandbox(cfg.execution)
        self.executors = stage._executors(_Ctx(store), cell, self.problems, self.sandbox)
        canonical = store.read_table(execution_ref(cell))
        canonical = canonical[canonical["sample_index"] == 0]
        self.passed_before = set(canonical.loc[canonical["status"] == "PASSED", "problem_key"])
        self.ids = {
            p.problem_key: sample_id(
                source=Source.HUMAN, problem_key=p.problem_key, language=language, sample_index=0
            )
            for p in self.problems
        }
        self.codes = {p.problem_key: self.executors[p.dataset].canonical(p) for p in self.problems}

    def run(self, codes: dict[str, str]) -> dict[str, Any]:
        """Esegue un codice per problema; ``problem_key`` → ``ExecutionRecord``."""

        def one(problem: Problem) -> tuple[str, Any]:
            sample = SampleToRun(
                sample_id=self.ids[problem.problem_key],
                problem_key=problem.problem_key,
                sample_index=0,
                code=codes[problem.problem_key],
            )
            record = self.executors[problem.dataset].run_problem(problem, [sample])[0]
            return problem.problem_key, record

        todo = [p for p in self.problems if p.problem_key in codes]
        with ThreadPoolExecutor(max_workers=max_workers(self.cfg, self.language)) as pool:
            return dict(pool.map(one, todo))

    def run_samples(self, samples: list[SampleToRun]) -> dict[str, Any]:
        """Esegue campioni qualsiasi (più per problema); ``sample_id`` → ``ExecutionRecord``."""
        by_problem: dict[str, list[SampleToRun]] = {}
        for sample in samples:
            by_problem.setdefault(sample.problem_key, []).append(sample)
        problems = {p.problem_key: p for p in self.problems}

        def one(key: str) -> list[Any]:
            problem = problems[key]
            return list(self.executors[problem.dataset].run_problem(problem, by_problem[key]))

        out: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=max_workers(self.cfg, self.language)) as pool:
            for records in pool.map(one, sorted(by_problem)):
                out.update({r.sample_id: r for r in records})
        return out


class _Ctx:
    """Contesto minimo per ``ExecuteStage._executors`` (serve solo lo store)."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store


def grid_points(attack_id: str, wanted: dict[str, str], language: str) -> dict[str, Any]:
    """Punti della griglia con i parametri ``wanted`` (tutti se vuoto; ``b=all`` per T2.1), con
    gli hash delle celle di ``language`` (versione del catalogo del linguaggio)."""
    return {
        h: a
        for h, a in grid_attacks(attack_id, language=language).items()
        if all(str(a.params.get(k)) == v for k, v in wanted.items())
    }


def isolate_types(
    checker: Checker,
    attack: Any,
    lang: Language,
    items: list[tuple[SampleToRun, Any, AttackContext]],
) -> None:
    """T2.1: per ogni campione rotto, quali tipi di regola lo rompono da soli."""
    sys.stdout.write("  --- T2.1: un tipo di regola alla volta sui campioni rotti\n")
    for sample, out, ctx in items:
        culprits = []
        for type_name in out.detail.get("applied", []):
            single = attack.apply_types([sample.code], lang, [ctx], forced=[[type_name]])[0]
            if single.code == sample.code:
                continue
            probe = SampleToRun(sample.sample_id, sample.problem_key, sample.sample_index,
                                single.code)  # fmt: skip
            record = checker.run_samples([probe])[sample.sample_id]
            if str(record.status) != "PASSED":
                culprits.append(type_name)
        found = ", ".join(culprits) or "nessuno da solo (effetto combinato)"
        sys.stdout.write(f"  {sample.problem_key} {sample.sample_id[:12]}: {found}\n")


def check_language(
    cfg: Any,
    store: ArtifactStore,
    language: str,
    attack_ids: list[str],
    wanted: dict[str, str] | None = None,
) -> None:
    lang = Language(language)
    checker = Checker(cfg, store, language)
    tools = ToolRunner(repo_root() / "tools", cfg.paths.tmp / "attack_canonical")
    parsers = default_parsers()
    pool_ref = attack_pool_ref(language)
    pools = AttackPools()
    if store.exists(pool_ref):
        pool = store.read_model(pool_ref, AttackPool)
        pools = AttackPools(tuple(pool.comments), tuple(pool.names))
    n_before = len(checker.passed_before)
    sys.stdout.write(
        f"\n########## {language}: {len(checker.problems)} canoniche, {n_before} passano senza "
        "attacco (esecuzione delle canoniche della M4)\n"
    )
    if "T1.5" in attack_ids:
        report_log_sites(checker, language)
    for attack_id in attack_ids:
        for params_hash, attack in grid_points(attack_id, wanted or {}, language).items():
            if attack.needs_pools and not (pools.comments and pools.names):
                sys.stdout.write(f"{attack_id} {attack.params}: SKIPPED (run stage attack_pools)\n")
                continue
            keys = [p.problem_key for p in checker.problems]
            ctxs = [
                AttackContext(
                    rng=sample_rng(cfg.global_seed, attack_id, params_hash, checker.ids[k]),
                    problem=next(p for p in checker.problems if p.problem_key == k),
                    parsers=parsers,
                    tools=tools,
                    pools=pools,
                    sample_id=checker.ids[k],
                )
                for k in keys
            ]
            outcomes = attack.apply_many([checker.codes[k] for k in keys], lang, ctxs)
            attacked = {k: o.code for k, o in zip(keys, outcomes, strict=True)}
            records = checker.run(attacked)
            statuses = Counter(str(o.status) for o in outcomes)
            changed = [k for k, o in zip(keys, outcomes, strict=True)
                       if o.status is AttackStatus.APPLIED]  # fmt: skip
            distances = [
                codebleu_distance(checker.codes[k], attacked[k], language) for k in changed
            ]
            passed = {k for k, r in records.items() if str(r.status) == "PASSED"}
            broken = sorted(checker.passed_before - passed)
            sys.stdout.write(
                f"{attack_id} {json.dumps(attack.params, sort_keys=True)}: passano "
                f"{len(passed & checker.passed_before)}/{n_before}, rotte {len(broken)}, "
                f"stati {dict(statuses)}, modificate {len(changed)}/{len(keys)}, 1-CodeBLEU "
                f"{statistics.fmean(distances) if distances else 0.0:.4f}\n"
            )
            for o in outcomes:
                if o.status is AttackStatus.TOOL_ERROR:
                    sys.stdout.write(f"    TOOL_ERROR: {str(o.detail.get('error'))[:300]}\n")
                    break
            for key in broken:
                record = records[key]
                sys.stdout.write(
                    f"  ROTTA {key}: {record.status} {str(record.stderr_tail or '')[-300:]!r}\n"
                    f"{_diff(checker.codes[key], attacked[key])}\n"
                )
            if attack_id == "T1.2" and lang is Language.JAVA and broken:
                isolate_recipes(checker, tools, broken)
            if attack_id == "T2.1" and broken:
                by_key = dict(zip(keys, zip(outcomes, ctxs, strict=True), strict=True))
                items = [
                    (SampleToRun(checker.ids[k], k, 0, checker.codes[k]), *by_key[k])
                    for k in broken
                ]
                isolate_types(checker, attack, lang, items)


def check_baseline_dev(
    cfg: Any, store: ArtifactStore, language: str, attack_ids: list[str], wanted: dict[str, str]
) -> None:
    """Attacco sulla baseline di L1 dev (entrambi i modelli): campioni che passavano prima e
    passano ancora dopo l'attacco, per modello e in totale (decisione dell'utente del 26 ottobre
    2026, T2.1 con ``b = all``)."""
    lang = Language(language)
    checker = Checker(cfg, store, language)
    tools = ToolRunner(repo_root() / "tools", cfg.paths.tmp / "attack_baseline_dev")
    parsers = default_parsers()
    sys.stdout.write(f"\n########## {language}: baseline di L1 dev\n")
    for attack_id in attack_ids:
        for params_hash, attack in grid_points(attack_id, wanted, language).items():
            total_before = total_still = 0
            for model_id in cfg.models:
                table = store.read_table(baseline_ref(model_id, LEVEL, language, "dev"))
                cell = Cell(source="llm_baseline", model_id=model_id, level=LEVEL,
                            language=language, split="dev")  # fmt: skip
                executed = store.read_table(execution_ref(cell, None))
                passed_before = set(executed.loc[executed["status"] == "PASSED", "sample_id"])
                problems = {p.problem_key: p for p in checker.problems}
                rows = [
                    r for r in table.sort_values(["problem_key", "sample_index"]).to_dict("records")
                    if r["problem_key"] in problems
                ]  # fmt: skip
                samples = [
                    SampleToRun(
                        r["sample_id"],
                        r["problem_key"],
                        int(r["sample_index"] or 0),
                        str(r["code"] or ""),
                    )  # fmt: skip
                    for r in rows
                ]
                ctxs = [
                    AttackContext(
                        rng=sample_rng(cfg.global_seed, attack_id, params_hash, s.sample_id),
                        problem=problems[s.problem_key],
                        parsers=parsers,
                        tools=tools,
                        sample_id=s.sample_id,
                    )  # fmt: skip
                    for s in samples
                ]
                outcomes = attack.apply_many([s.code for s in samples], lang, ctxs)
                changed = [
                    (s, o, c) for s, o, c in zip(samples, outcomes, ctxs, strict=True)
                    if o.status is AttackStatus.APPLIED and s.sample_id in passed_before
                ]  # fmt: skip
                probes = [
                    SampleToRun(s.sample_id, s.problem_key, s.sample_index, o.code)
                    for s, o, _ in changed
                ]
                records = checker.run_samples(probes) if probes else {}
                broken = [
                    (s, o, c) for s, o, c in changed if str(records[s.sample_id].status) != "PASSED"
                ]  # fmt: skip
                statuses = Counter(str(o.status) for o in outcomes)
                n_before = len(passed_before & {s.sample_id for s in samples})
                still = n_before - len(broken)
                total_before += n_before
                total_still += still
                sys.stdout.write(
                    f"{attack_id} {json.dumps(attack.params.get('b'))} {model_id}: campioni "
                    f"{len(samples)}, passavano {n_before}, passano ancora {still} "
                    f"({still / n_before if n_before else 1:.2%}), stati {dict(statuses)}\n"
                )
                for s, o, _ in broken:
                    record = records[s.sample_id]
                    sys.stdout.write(
                        f"  ROTTO {s.problem_key} #{s.sample_index}: {record.status} "
                        f"{str(record.stderr_tail or '')[-300:]!r} tipi {o.detail.get('applied')}\n"
                        f"{_diff(s.code, o.code)}\n"
                    )
                if broken and attack_id == "T2.1":
                    isolate_types(checker, attack, lang, broken)
            share = total_still / total_before if total_before else 1.0
            sys.stdout.write(
                f"TOTALE {language} {attack_id} {json.dumps(attack.params.get('b'))}: "
                f"{total_still}/{total_before} ({share:.2%}) "
                f"{'OK' if share >= MIN_PRESERVED else 'SOTTO IL 99%'}\n"
            )


def report_log_sites(checker: Checker, language: str) -> None:
    """T1.5: punti del log su stderr nelle canoniche, con e senza l'esclusione delle funzioni
    ricorsive (decisione dell'utente del 19 ottobre 2026)."""
    lang = Language(language)
    parsers = default_parsers()
    with_rec = without_rec = recursive_codes = 0
    for code in checker.codes.values():
        sites = [s for s in statement_sites(code, lang, parsers) if not _excluded_site(s, lang)]
        everywhere = log_sites(sites, lang, exclude_recursive=False)
        kept = log_sites(sites, lang)
        with_rec += len(everywhere)
        without_rec += len(kept)
        recursive_codes += len(kept) < len(everywhere)
    lost = with_rec - without_rec
    sys.stdout.write(
        f"T1.5 punti del log su stderr: {with_rec} nel corpo delle funzioni, {without_rec} dopo "
        f"aver escluso le ricorsive (persi {lost}, in {recursive_codes} canoniche)"
        + (" [JavaScript: nessun log, D30]" if lang not in LOG_LANGUAGES else "")
        + "\n"
    )


def isolate_recipes(checker: Checker, tools: ToolRunner, broken: list[str]) -> None:
    """T1.2 Java: quale ricetta di CommonStaticAnalysis rompe ogni canonica rotta."""
    sys.stdout.write("  --- T1.2 Java: una ricetta alla volta sulle canoniche rotte\n")
    culprits: dict[str, list[str]] = {k: [] for k in broken}
    for recipe in _recipes():
        results = tools.run(
            "openrewrite", [checker.codes[k] for k in broken], extra_args=[f"-Dwmb.recipe={recipe}"]
        )
        codes = {
            k: r.code for k, r in zip(broken, results, strict=True)
            if r.code is not None and r.code != checker.codes[k]
        }  # fmt: skip
        if not codes:
            continue
        for key, record in checker.run(codes).items():
            if str(record.status) != "PASSED":
                culprits[key].append(recipe)
    for key, recipes in culprits.items():
        found = ", ".join(recipes) or "nessuna da sola (effetto combinato)"
        sys.stdout.write(f"  {key}: {found}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--languages", default="python,java,cpp,javascript")
    parser.add_argument("--attacks", default="T1.1,T1.2,T1.3,T1.4,T1.5")
    parser.add_argument("--params", default="", help="filtro sui parametri: chiave=valore[,...]")
    parser.add_argument("--baseline-dev", action="store_true", help="baseline di L1 dev")
    args = parser.parse_args()
    wanted = dict(kv.split("=", 1) for kv in args.params.split(",") if kv)
    cfg = load_experiment(["paths=cluster", "stage=execute"])
    store = ArtifactStore(cfg.paths.artifacts)
    target = "baseline di L1 dev" if args.baseline_dev else "canoniche di L1"
    sys.stdout.write(
        f"##### attacchi {args.attacks} {wanted or ''} sulle {target} (repo {Path(repo_root())})\n"
    )
    for language in args.languages.split(","):
        if args.baseline_dev:
            check_baseline_dev(cfg, store, language, args.attacks.split(","), wanted)
        else:
            check_language(cfg, store, language, args.attacks.split(","), wanted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
