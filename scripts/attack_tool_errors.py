"""Errori più frequenti dei tool di un attacco su L1 test (diagnostica della Milestone 10).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/attack_tool_errors.py METHOD ATTACK LANGUAGE [--top 8] [--rerun N]

Legge gli artefatti ``attacked/<ATTACK>/*/<METHOD>/*/*/L1_<LANGUAGE>_test.parquet`` e per ogni
cella (configurazione dell'attacco, modello) conta i ``TOOL_ERROR`` raggruppati per la prima riga
significativa dell'errore. Per ogni gruppo stampa anche quanti campioni marcati erano già
``PASSED`` all'esecuzione senza attacco: se sono pochi, il tool fallisce su codice già rotto.

Con ``--rerun N`` riapplica il tool (T1.1 e T1.2) a N campioni in ``TOOL_ERROR`` che passavano i
test e stampa il messaggio completo, diagnosi su stdout comprese (gli artefatti scritti prima di
questa versione hanno solo la fine di stderr: per clang-tidy il solo riepilogo). Non scrive nulla.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from bench.attacks.tools import ToolRunner
from bench.config.builder import load_experiment, repo_root
from bench.pipeline.stage import Cell
from bench.pipeline.stages.execute import execution_ref
from bench.store.artifact_store import ArtifactStore

NOISE = re.compile(r"^(exit \d+: )?(\d+ warnings?|Suppressed|Use -header-filter|Error while)")
# Messaggi veri dei compilatori: clang (``error: ...``) e javac via Maven (``.java:[r,c] ...``).
MESSAGE = re.compile(r"(?:\berror: |\.java:\[\d+,\d+\] )(.+)")
# Tool di T1.1 e T1.2 per linguaggio (ADR-012 §2).
TOOLS = {
    "T1.1": {"java": "google-java-format", "cpp": "clang-format", "javascript": "prettier"},
    "T1.2": {"python": "ruff", "java": "openrewrite", "cpp": "clang-tidy", "javascript": "eslint"},
}
MAX_ERROR_CHARS = 1500  # caratteri stampati per errore con --rerun


def _key(error: str) -> str:
    """Primo messaggio d'errore del compilatore se c'è, altrimenti la prima riga utile; senza
    percorsi temporanei e numeri di riga."""
    found = MESSAGE.search(error)
    if found:
        return found.group(1).strip()[:160]
    lines = [raw.strip() for raw in error.splitlines() if raw.strip()]
    for line in lines:
        if not NOISE.match(line):
            return re.sub(r":\d+:\d+:", ":", re.sub(r"(?<!\S)/\S*/", "", line))[:160]
    return (lines[0] if lines else error)[:160]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method")
    parser.add_argument("attack")
    parser.add_argument("language")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--rerun", type=int, default=0)
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    root = cfg.paths.artifacts
    files = sorted(
        root.glob(f"attacked/{args.attack}/*/{args.method}/*/*/L1_{args.language}_test.parquet")
    )
    if not files:
        sys.stdout.write("no attacked artifacts found\n")
        return 1
    tool = TOOLS.get(args.attack, {}).get(args.language)
    if args.rerun and tool is None:
        sys.stdout.write(f"--rerun: no external tool for {args.attack} {args.language}\n")
        return 2
    for path in files:
        # attacked/<attacco>/<hash>/<metodo>/<modello>/<cfg>/<file>
        params_hash, model = path.parts[-5], path.parts[-3]
        df = pd.read_parquet(path)
        errors = df[df["attack_status"] == "TOOL_ERROR"]
        passed = _passed(root, path)
        on_passed = errors[errors["parent_id"].isin(passed)]
        sys.stdout.write(
            f"##### {args.attack} {params_hash} {model} {args.language}: "
            f"{len(errors)}/{len(df)} TOOL_ERROR, di cui {len(on_passed)} su marcati PASSED\n"
        )
        groups: Counter[str] = Counter()
        passed_in: Counter[str] = Counter()
        for _, row in errors.iterrows():
            key = _key(str(json.loads(row["attack_detail"]).get("error", "")))
            groups[key] += 1
            passed_in[key] += int(row["parent_id"] in passed)
        for key, n in groups.most_common(args.top):
            sys.stdout.write(f"  {n:5d}  (marcati PASSED: {passed_in[key]:4d})  {key}\n")
        if args.rerun and tool is not None:
            _rerun(cfg.paths.tmp, tool, on_passed.head(args.rerun))
    return 0


def _rerun(tmp: Path, tool: str, rows: pd.DataFrame) -> None:
    """Riapplica il tool, un campione alla volta, e stampa l'errore completo."""
    runner = ToolRunner(repo_root() / "tools", tmp / "attack_tool_errors")
    for _, row in rows.iterrows():
        (result,) = runner.run(tool, [str(row["code"])])
        message = result.error if result.code is None else "nessun errore alla riapplicazione"
        sys.stdout.write(
            f"  --- rerun {tool} su {row['parent_id']}:\n"
            + "\n".join(f"      {line}" for line in str(message)[:MAX_ERROR_CHARS].splitlines())
            + "\n"
        )


def _passed(root: Path, path: Path) -> set[str]:
    """``sample_id`` dei marcati che passano i test senza attacco (esecuzione della fase 0)."""
    method, model, cfg_hash = path.parts[-4], path.parts[-3], path.parts[-2]
    level, language, split = path.stem.split("_")
    cell = Cell(
        source="llm_watermarked",
        method=method,
        model_id=model,
        level=level,
        language=language,
        split=split,
    )
    marked = ArtifactStore(root).path_of(execution_ref(cell, cfg_hash))
    if not marked.is_file():
        raise FileNotFoundError(f"marked execution not found: {marked}")
    df = pd.read_parquet(marked, columns=["sample_id", "status"])
    return set(df.loc[df["status"] == "PASSED", "sample_id"])


if __name__ == "__main__":
    raise SystemExit(main())
