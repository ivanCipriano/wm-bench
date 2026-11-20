"""Attaccati T1.4 già prodotti colpiti dallo stesso difetto di LibCST del bug di MultiMark.

T1.4 (``bench.attacks.t1.rename``) usa ``Assignment.references`` di LibCST come la rinomina di
MultiMark prima di 3c47a1f: con funzioni annidate che legano lo stesso nome, o con letture che
precedono un binding locale, la rinomina può toccare letture di un'altra variabile. Lo script
conta, in sola lettura, i campioni colpiti fra gli attaccati T1.4 già prodotti. T1.4 non si
corregge qui.

``check_renaming`` viene da ``multimark.rename_check`` della copia separata (``--multimark``,
default ``/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_multimark``, commit ``3c47a1f``), importato
isolato come in ``scripts/check_rename_dev.py``; il commit della copia va in testa al log.

Celle: ogni ``attacked/T1.4/<params>/<metodo>/<modello>/<hash>/<livello>_python_<split>.parquet``
dello store (solo Python: ``check_renaming`` analizza Python), escluse quelle segnate come
superate (contate a parte). Ogni attaccato si confronta con il suo genitore, il marcato senza
attacco (``parent_id`` nei marcati della stessa cella). Per metodo, split, modello, livello e punto
di T1.4 (etichetta dalla griglia attuale di ``configs/attack/t1_4_rename.yaml``):

- ``samples``: righe degli attaccati; ``applied``: attaccati con stato ``APPLIED`` (gli altri hanno
  il codice del genitore e non possono avere violazioni);
- ``checked``: applicati con codice e genitore non vuoti e analizzabili;
- ``violations``: numero e percentuale **sui controllati**;
- ``fail_vs_marked``: fra quelli con violazioni, quanti falliscono i test mentre il marcato senza
  attacco li passa;
- i primi ``--cases`` casi (default 3) con le violazioni.

In fondo, per metodo e split, i totali. Gira in **bench-core**, CPU::

    python scripts/check_rename_t1_4.py [--methods M ...] [--multimark DIR] [--report FILE]

``--report`` non deve esistere (i log non si sovrascrivono).
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ATTACK = "T1.4"
LANGUAGE = "python"


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


crd = _load("check_rename_dev")


@dataclass(frozen=True)
class AttackedCell:
    """Una cella attaccata di T1.4 (dal percorso dello store)."""

    params_hash: str
    method: str
    model_id: str
    config_hash: str
    level: str
    split: str


def find_cells(root: Path, methods: Sequence[str] | None = None) -> list[AttackedCell]:
    """Celle ``attacked/T1.4/...`` di Python sotto ``root`` (artefatti), in ordine."""
    out = []
    for path in sorted((root / "attacked" / ATTACK).glob("*/*/*/*/*.parquet")):
        params, method, model, cfg_hash = path.parts[-5:-1]
        stem = path.stem.split("_")
        if len(stem) != 3 or stem[1] != LANGUAGE:  # noqa: PLR2004  <livello>_<ling>_<split>
            continue
        if methods and method not in methods:
            continue
        out.append(AttackedCell(params, method, model, cfg_hash, stem[0], stem[2]))
    return sorted(out, key=lambda c: (c.method, c.split, c.model_id, c.level, c.params_hash,
                                      c.config_hash))  # fmt: skip


def point_labels() -> dict[str, str]:
    """``attack_params_hash`` → etichetta dei punti della griglia attuale di T1.4."""
    from bench.attacks.base import grid_attacks

    return {
        h: f"ratio={a.params['ratio']} name_kind={a.params['name_kind']}"
        for h, a in grid_attacks(ATTACK, language=LANGUAGE).items()
    }


def read_cell(store: Any, c: AttackedCell) -> tuple[Any, Any, dict[str, str], dict[str, str]]:
    """Attaccati applicati, marcati (genitori) ed esiti dell'esecuzione dei due."""
    from bench.pipeline.stage import Cell
    from bench.pipeline.stages.attack import attacked_ref
    from bench.pipeline.stages.execute import execution_ref
    from bench.pipeline.stages.watermark import watermarked_ref

    aref = attacked_ref(ATTACK, c.params_hash, c.method, c.model_id, c.config_hash, c.level,
                        LANGUAGE, c.split)  # fmt: skip
    attacked = store.read_table(aref)
    marked = store.read_table(watermarked_ref(c.method, c.model_id, c.config_hash, c.level,
                                              LANGUAGE, c.split))  # fmt: skip

    def statuses(ref: Any) -> dict[str, str]:
        if not store.exists(ref):
            return {}
        t = store.read_table(ref)
        return {str(k): str(v) for k, v in zip(t["sample_id"], t["status"], strict=True)}

    common = {"method": c.method, "model_id": c.model_id, "level": c.level,
              "language": LANGUAGE, "split": c.split}  # fmt: skip
    att_cell = Cell(source="attacked", attack_id=ATTACK, attack_params_hash=c.params_hash,
                    **common)  # fmt: skip
    wm_cell = Cell(source="llm_watermarked", **common)
    return (attacked, marked, statuses(execution_ref(att_cell, c.config_hash)),
            statuses(execution_ref(wm_cell, c.config_hash)))  # fmt: skip


def is_superseded(store: Any, c: AttackedCell) -> bool:
    from bench.pipeline.stages.attack import attacked_ref

    aref = attacked_ref(ATTACK, c.params_hash, c.method, c.model_id, c.config_hash, c.level,
                        LANGUAGE, c.split)  # fmt: skip
    manifest = store.read_manifest(aref)
    return bool(manifest is not None and manifest.extra.get("superseded"))


def cell_line(c: AttackedCell, label: str, applied: int, s: dict[str, Any]) -> list[str]:
    head = (f"  {c.method:<11}{c.split:<5}{c.model_id:<24}{c.level:<4}{label:<30}"
            f"{c.config_hash:<14}{s['samples']:>8}{applied:>8}{s['checked']:>8}"
            f"{s['violations']:>6} {crd._pct(s['rate']):>8}{s['fail_vs_base']:>15}")  # fmt: skip
    out = [head]
    for case in s["cases"]:
        shown = case["violations"][: crd.MAX_VIOLATIONS_SHOWN]
        more = len(case["violations"]) - len(shown)
        out.append(f"      {case['problem_key']} sample {case['sample_id'][:12]} (marked "
                   f"{case['parent'][:12]}), fails tests while the marked passes: "
                   f"{'yes' if case['fails_vs_base'] else 'no'}")  # fmt: skip
        out += [f"        {v}" for v in shown]
        if more > 0:
            out.append(f"        ... {more} more violations")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--methods", nargs="+", help="default: every method with T1.4 cells")
    p.add_argument("--multimark", type=Path, default=crd.DEFAULT_MULTIMARK)
    p.add_argument("--expect-commit", default=crd.EXPECTED_COMMIT)
    p.add_argument("--cases", type=int, default=crd.DEFAULT_CASES)
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1
    try:
        check, commit = crd.load_checker(args.multimark, args.expect_commit)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment
    from bench.store.artifact_store import ArtifactStore

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    root = Path(cfg.paths.artifacts)
    store = ArtifactStore(root)
    labels = point_labels()
    cells = find_cells(root, args.methods)
    lines = [
        "# rename check of the T1.4 attacked samples (scripts/check_rename_t1_4.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# multimark.rename_check from {args.multimark} at commit {commit}; "
        f"wm-bench {crd.git_commit(REPO_ROOT)}; read-only, T1.4 not corrected",
        f"# {len(cells)} T1.4 cells of Python under {root / 'attacked' / ATTACK}; each attacked "
        "sample vs its marked parent; violations on the checked (APPLIED) samples; "
        "fail_vs_marked = violations whose tests fail while the marked sample passes",
        "",
        "  " + f"{'method':<11}{'split':<5}{'model':<24}{'lvl':<4}{'point':<30}{'config':<14}"
        f"{'samples':>8}{'applied':>8}{'checked':>8}{'viol':>6} {'viol %':>8}"
        f"{'fail_vs_marked':>15}",
    ]
    lines += check_cells(store, cells, labels, check, args.cases)
    text = "\n".join(lines) + "\n"
    if args.report is None:
        sys.stdout.write(text)
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as fh:
            fh.write(text)
    return 0


def check_cells(store: Any, cells: Sequence[AttackedCell], labels: dict[str, str], check: Any,
                n_cases: int) -> list[str]:  # fmt: skip
    """Righe delle celle e totali per metodo e split."""
    lines: list[str] = []
    totals: dict[tuple[str, str], list[int]] = {}  # (controllati, violazioni, fallimenti)
    superseded = 0
    for c in cells:
        if is_superseded(store, c):
            superseded += 1
            continue
        label = labels.get(c.params_hash, f"params {c.params_hash}")
        try:
            attacked, marked, exec_att, exec_marked = read_cell(store, c)
        except Exception as exc:  # artefatto mancante o illeggibile: si segnala
            lines.append(f"  {c.method:<11}{c.split:<5}{c.model_id:<24}{c.level:<4}{label:<30}"
                         f"missing ({type(exc).__name__}: {exc})")  # fmt: skip
            continue
        applied = attacked[attacked["attack_status"] == "APPLIED"]
        stats = crd.cell_stats(applied, marked, exec_att, exec_marked, check, n_cases)
        stats["samples"] = len(attacked)
        lines += cell_line(c, label, len(applied), stats)
        if stats["errors"]:
            lines.append(f"      ({stats['errors']} samples not analysable by the check)")
        t = totals.setdefault((c.method, c.split), [0, 0, 0])
        t[0] += stats["checked"]
        t[1] += stats["violations"]
        t[2] += stats["fail_vs_base"]
    lines += ["", "## totals by method and split (checked, with violations, % on checked, "
              "fail_vs_marked)"]  # fmt: skip
    for (method, split), (checked, viol, fails) in sorted(totals.items()):
        rate = viol / checked if checked else math.nan
        lines.append(f"  {method:<11}{split:<5}checked {checked:>7}  violations {viol:>6} "
                     f"({crd._pct(rate)})  fail_vs_marked {fails}")  # fmt: skip
    if superseded:
        lines.append(f"  ({superseded} superseded cells skipped)")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
