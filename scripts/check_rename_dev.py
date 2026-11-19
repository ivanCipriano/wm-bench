"""Campioni dev già marcati colpiti dal bug di rinomina di MultiMark (corretto in 3c47a1f).

Il bug: LibCST attaccava le letture locali di un nome alla variabile di modulo omonima, quindi la
rinomina poteva cambiare anche nomi di un altro scope. ``multimark.rename_check.check_renaming``
(commit 3c47a1f) verifica in sola lettura che la rinomina dall'originale al marcato sia una
biiezione fra le variabili, scope per scope.

Lo script importa ``check_renaming`` dalla **copia separata** della libreria (``--multimark``,
default ``/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_multimark``), aggiunta al path solo qui e
importata isolata (le voci ``multimark`` già importate si mettono da parte e poi si ripristinano);
il commit della copia va in testa al log e deve cominciare con ``--expect-commit`` (default
``3c47a1f``). Il submodule ``third_party/multimark`` non si tocca e nessun ``config_hash`` cambia:
si leggono solo gli artefatti già prodotti.

Campioni: i marcati senza attacco di ogni ``--config metodo:hash[:etichetta]`` (giro di
capacità), per modello e livello dev, contro il campione della baseline da cui derivano
(``parent_id``). Per cella:

- ``samples``: righe dei marcati; ``checked``: marcato e genitore non vuoti e analizzabili (gli
  altri sono ``skipped``: vuoti, genitore assente, ``error`` del controllo);
- ``violations``: campioni con almeno una violazione, numero e percentuale **sui controllati**;
- ``unpaired``: somma di ``n_unpaired_original`` e ``n_unpaired_marked`` (variabili solo in
  sottoalberi riscritti, non controllate) e campioni con almeno una variabile non appaiata;
- ``fail_vs_base``: fra i campioni con violazioni, quanti falliscono i test (esecuzione del
  marcato non ``PASSED``) mentre il genitore li passa;
- i primi ``--cases`` campioni con violazioni (default 3), con le violazioni.

In fondo, per configurazione, la percentuale massima di campioni con violazioni sulle celle,
contro la soglia dell'1% fissata prima di vedere i numeri (se ogni cella è ≤ 1% non si rifanno i
giri dev).

Gira in **bench-core**, CPU, solo lettura::

    python scripts/check_rename_dev.py --config mcss:<hash>:mcss ... [--levels L1 L2] \\
        [--multimark DIR] [--report FILE] [override Hydra ...]

``--report`` non deve esistere (i log non si sovrascrivono); ``--split test`` non è ammesso.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import math
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MULTIMARK = Path("/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_multimark")
EXPECTED_COMMIT = "3c47a1f"
THRESHOLD = 0.01  # regola fissata prima dei numeri: ogni cella ≤ 1%
DEFAULT_CASES = 3
MAX_VIOLATIONS_SHOWN = 5


def _load(name: str) -> Any:
    """Uno script di ``scripts/`` come modulo (registrato in ``sys.modules`` per le dataclass)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rad = _load("report_attacks_dev")
Config = rad.Config


# ---------------------------------------------------------------------- copia della libreria
def git_commit(repo: Path) -> str:
    """Commit di un repository Git (``unknown`` se non si legge)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True, timeout=30)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def load_checker(repo: Path, expect: str) -> tuple[Callable[[str, str], Any], str]:
    """``check_renaming`` dalla copia ``repo`` e il suo commit.

    Raises:
        RuntimeError: copia senza ``multimark/rename_check.py``, commit diverso da ``expect`` o
            modulo importato da un'altra cartella.
    """
    repo = repo.resolve()
    if not (repo / "multimark" / "rename_check.py").is_file():
        raise RuntimeError(f"{repo}: multimark/rename_check.py not found")
    commit = git_commit(repo)
    if expect and not commit.startswith(expect):
        raise RuntimeError(f"{repo} is at {commit}, expected {expect}")
    saved = {k: v for k, v in sys.modules.items() if k == "multimark" or k.startswith("multimark.")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(repo))
    try:
        module = importlib.import_module("multimark.rename_check")
    finally:
        sys.path.remove(str(repo))
        for k in [k for k in sys.modules if k == "multimark" or k.startswith("multimark.")]:
            del sys.modules[k]
        sys.modules.update(saved)
    where = Path(str(module.__file__)).resolve()
    if repo not in where.parents:
        raise RuntimeError(f"multimark.rename_check imported from {where}, not from {repo}")
    return module.check_renaming, commit


# ---------------------------------------------------------------------- cella
def _empty(code: Any) -> bool:
    return code is None or (isinstance(code, float) and math.isnan(code)) or not str(code).strip()


def cell_stats(
    marked: pd.DataFrame,
    baseline: pd.DataFrame,
    exec_marked: dict[str, str],
    exec_base: dict[str, str],
    check: Callable[[str, str], Any],
    n_cases: int = DEFAULT_CASES,
) -> dict[str, Any]:
    """Conteggi di una cella (vedi la docstring del modulo) e i primi ``n_cases`` casi."""
    originals = {str(k): v for k, v in zip(baseline["sample_id"], baseline["code"], strict=True)}
    order = [c for c in ("problem_key", "sample_index", "sample_id") if c in marked.columns]
    rows = marked.sort_values(order) if order else marked
    out: dict[str, Any] = {"samples": len(rows), "checked": 0, "skipped": 0, "errors": 0,
                           "violations": 0, "unpaired": 0, "with_unpaired": 0,
                           "fail_vs_base": 0, "cases": []}  # fmt: skip
    sids: list[str] = [str(x) for x in rows["sample_id"]]
    parents: list[str] = [str(x) for x in rows["parent_id"]]
    pkeys: list[str] = (
        [str(x) for x in rows["problem_key"]] if "problem_key" in rows else [""] * len(rows)
    )
    for sid, parent, code, pkey in zip(sids, parents, list(rows["code"]), pkeys, strict=True):
        original = originals.get(parent)
        if _empty(code) or _empty(original):
            out["skipped"] += 1
            continue
        result = check(str(original), str(code))
        if getattr(result, "error", None):  # codice non analizzabile
            out["skipped"] += 1
            out["errors"] += 1
            continue
        out["checked"] += 1
        unpaired = int(result.n_unpaired_original) + int(result.n_unpaired_marked)
        out["unpaired"] += unpaired
        out["with_unpaired"] += unpaired > 0
        if result.ok:
            continue
        out["violations"] += 1
        fails = exec_marked.get(sid) not in (None, "PASSED") and exec_base.get(parent) == "PASSED"
        out["fail_vs_base"] += fails
        if len(out["cases"]) < n_cases:
            out["cases"].append({"sample_id": sid, "problem_key": str(pkey), "parent": parent,
                                 "fails_vs_base": fails,
                                 "violations": list(result.violations)})  # fmt: skip
    out["rate"] = out["violations"] / out["checked"] if out["checked"] else math.nan
    return out


def _pct(x: float) -> str:
    return "-" if math.isnan(x) else f"{100 * x:.2f}%"


def cell_lines(name: str, model: str, level: str, s: dict[str, Any]) -> list[str]:
    out = [
        f"  {name:<34}{model:<24}{level:<4}{s['samples']:>8}{s['checked']:>9}{s['skipped']:>9}"
        f"{s['violations']:>6} {_pct(s['rate']):>8}{s['unpaired']:>10}{s['with_unpaired']:>8}"
        f"{s['fail_vs_base']:>14}"
    ]
    for case in s["cases"]:
        shown = case["violations"][:MAX_VIOLATIONS_SHOWN]
        more = len(case["violations"]) - len(shown)
        out.append(f"      {case['problem_key']} sample {case['sample_id'][:12]} (parent "
                   f"{case['parent'][:12]}), fails tests while parent passes: "
                   f"{'yes' if case['fails_vs_base'] else 'no'}")  # fmt: skip
        out += [f"        {v}" for v in shown]
        if more > 0:
            out.append(f"        ... {more} more violations")
    return out


def summary_lines(rates: dict[str, list[tuple[str, float]]]) -> list[str]:
    """Percentuale massima di campioni con violazioni per configurazione, contro la soglia."""
    limit = f"{THRESHOLD:.0%}"
    out = [f"## summary: max share of samples with violations over the cells (threshold "
           f"{limit}: if every cell is <= {limit} the dev rounds are not redone)"]  # fmt: skip
    verdicts = []
    for name, cells in rates.items():
        known = [r for _, r in cells if not math.isnan(r)]
        missing = [w for w, r in cells if math.isnan(r)]
        worst = max(known) if known else math.nan
        where = next((w for w, r in cells if r == worst), "-")
        if missing or math.isnan(worst):
            verdict = "INCOMPLETE"
        else:
            verdict = "ok (<= 1%)" if worst <= THRESHOLD else "ABOVE 1%"
        verdicts.append(verdict)
        extra = f"; missing {', '.join(missing)}" if missing else ""
        out.append(f"  {name:<34}max {_pct(worst):>8} ({where})  {verdict}{extra}")
    if verdicts and all(v.startswith("ok") for v in verdicts):
        out.append("  -> every cell <= 1%: the dev rounds are not redone")
    elif any(v == "ABOVE 1%" for v in verdicts):
        out.append("  -> at least one cell above 1%")
    return [*out, ""]


# ---------------------------------------------------------------------- lettura degli artefatti
CellData = tuple[pd.DataFrame, pd.DataFrame, dict[str, str], dict[str, str]]


def read_cell(store: Any, c: Any, model: str, level: str, lang: str, split: str) -> CellData:
    """Marcati, baseline ed esiti dell'esecuzione (vuoti se l'esecuzione manca)."""
    from bench.pipeline.stage import Cell
    from bench.pipeline.stages.execute import execution_ref
    from bench.pipeline.stages.generate_baseline import baseline_ref
    from bench.pipeline.stages.watermark import watermarked_ref

    marked = store.read_table(watermarked_ref(c.method, model, c.config_hash, level, lang, split))
    baseline = store.read_table(baseline_ref(model, level, lang, split))

    def statuses(ref: Any) -> dict[str, str]:
        if not store.exists(ref):
            return {}
        t = store.read_table(ref)
        return {str(k): str(v) for k, v in zip(t["sample_id"], t["status"], strict=True)}

    wm_cell = Cell(source="llm_watermarked", method=c.method, model_id=model, level=level,
                   language=lang, split=split)  # fmt: skip
    base_cell = Cell(source="llm_baseline", model_id=model, level=level, language=lang,
                     split=split)  # fmt: skip
    return (marked, baseline, statuses(execution_ref(wm_cell, c.config_hash)),
            statuses(execution_ref(base_cell)))  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument(
        "--multimark",
        type=Path,
        default=DEFAULT_MULTIMARK,
        help="separate copy of the MultiMark library with multimark/rename_check.py",
    )
    p.add_argument("--expect-commit", default=EXPECTED_COMMIT,
                   help="required prefix of the commit of the copy ('' = any)")  # fmt: skip
    p.add_argument("--cases", type=int, default=DEFAULT_CASES)
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.split == "test":
        print("[ERROR] development check only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1
    try:
        check, commit = load_checker(args.multimark, args.expect_commit)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment
    from bench.store.artifact_store import ArtifactStore

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    store = ArtifactStore(Path(cfg.paths.artifacts))
    lines = [
        "# rename check of the dev marked samples (scripts/check_rename_dev.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# multimark.rename_check from {args.multimark} at commit {commit}; "
        f"wm-bench {git_commit(REPO_ROOT)}; submodule third_party/multimark not touched",
        f"# configs: {', '.join(c.name for c in args.config)}; split {args.split}, language "
        f"{args.language}; marked without attack vs the parent baseline sample",
        "# share of violations on the checked samples; unpaired = variables only in rewritten "
        "subtrees (not checked); fail_vs_base = violations whose tests fail while the parent "
        "passes",
        "",
        "  " + f"{'config':<34}{'model':<24}{'lvl':<4}{'samples':>8}{'checked':>9}{'skipped':>9}"
        f"{'viol':>6} {'viol %':>8}{'unpaired':>10}{'smp_unp':>8}{'fail_vs_base':>14}",
    ]
    rates: dict[str, list[tuple[str, float]]] = {c.name: [] for c in args.config}
    for c in args.config:
        for model in args.models or list(cfg.models):
            for level in args.levels:
                where = f"{model} {level}"
                try:
                    data = read_cell(store, c, model, level, args.language, args.split)
                except Exception as exc:  # artefatto mancante: si segnala
                    lines.append(f"  {c.name:<34}{model:<24}{level:<4}missing "
                                 f"({type(exc).__name__}: {exc})")  # fmt: skip
                    rates[c.name].append((where, math.nan))
                    continue
                stats = cell_stats(*data, check, args.cases)
                lines += cell_lines(c.name, model, level, stats)
                if stats["errors"]:
                    lines.append(f"      ({stats['errors']} samples not analysable by the check)")
                rates[c.name].append((where, stats["rate"]))
    lines += ["", *summary_lines(rates)]
    text = "\n".join(lines) + "\n"
    if args.report is None:
        sys.stdout.write(text)
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
