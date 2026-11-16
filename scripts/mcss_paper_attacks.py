"""Attacchi della Tabella 5 di MCSS sulla selezione CodeNet (PIPELINE_metodo.md di multimark §4.4
punto 3; criterio (a) di P2).

Per ogni file della selezione (``mcss_selftest_eel_<c>.jsonl`` di
``scripts/select_mcss_selftest.py``, fascia 32–39 bit) e per ogni chiave (``--keys``, default 1):

1. messaggio di 24 bit e chiave dai semi ``derive_seed(global_seed, "mcss-paper-attacks-msg" |
   "mcss-paper-attacks-key", sample_id, k)``, uguali per i due profili (confronto appaiato);
2. inserimento con il profilo ``mcss`` e con il profilo ``multimark`` del benchmark (``PROFILES``:
   gli stessi campi nativi degli adapter ``McssAdapter`` e ``MultimarkAdapter``, verificato da
   ``tests/unit/test_mcss_paper_attacks.py``);
3. attacco ``--attack`` sul codice marcato (Tabella 5, p. 8). Per ora solo ``none``; la rinomina
   casuale di r variabili e l'inversione dei siti di un canale arrivano in P4;
4. rilevazione con il messaggio atteso (``N_reg = 1``) e decodifica aperta (Teorema 5.2 di MCSS:
   sole cancellazioni).

Riepilogo per profilo, con intervalli di Wilson al 95%:

- stati dell'inserimento (``OK``, ``PARTIAL``, ``FAILED`` con i motivi);
- ``message_accuracy``: decodifica aperta uguale al messaggio, su tutti i casi, come nel paper
  (un inserimento fallito conta come messaggio non recuperato);
- file con **tutte le B posizioni coperte** (almeno un voto per posizione dopo l'attacco) e
  ``message_accuracy`` su quei file;
- **messaggi sbagliati** (decodifica riuscita ma diversa dal messaggio) e **casi ambigui** (più
  soluzioni; anche ``inconsistent``, nessuna soluzione), su tutti i casi e su quelli con N ≥ B;
- ``bit_accuracy`` media (``bits_correct / 24``, decisione L di §7).

Con ``--attack none`` si scrive anche l'esito del criterio (a) di P2 (v1.2.5):

- ``mcss``: ``message_accuracy`` = 1,00 su tutti i file;
- ``multimark``: ``message_accuracy`` = 1,00 sui file con tutte le posizioni coperte e 0 messaggi
  sbagliati su tutti; la frazione di file con N ≥ B e decodifica ambigua si riporta soltanto.

Gira nell'ambiente **posthoc** (Python >= 3.9, libreria standard e ``bench_contracts``), con la
libreria importata da ``build/patched/multimark`` (``--library``); la copia deve venire dal commit
del submodule (``.source_commit``), altrimenti lo script si ferma. ``--report`` e ``--records``
non devono esistere (i log non si sovrascrivono).

Uso (``INV=$WMB/artifacts/_runs/diagnostics/inventory``)::

    <posthoc>/bin/python scripts/mcss_paper_attacks.py --attack none \\
        --selection $INV/mcss_selftest_eel_fb311767f65c.jsonl \\
        --report docs/milestone_logs/P2_mcss_paper_attacks_none.txt
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from bench_contracts import derive_seed

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIBRARY = REPO_ROOT / "build" / "patched" / "multimark"
GLOBAL_SEED = 20261001  # configs/config.yaml (cluster_info §10)
METHODS = ("mcss", "multimark")
MESSAGE_BITS = 24
Z95 = 1.959963984540054  # quantile 0,975 della normale standard
MSG_LABEL, KEY_LABEL = "mcss-paper-attacks-msg", "mcss-paper-attacks-key"


class AttackError(RuntimeError):
    """Ingresso non valido, libreria non allineata o uscita già presente."""


# ---------------------------------------------------------------------------------- libreria
def import_library(library: Path) -> Any:
    """Importa ``multimark`` da ``library`` prima di ogni altro percorso.

    Raises:
        AttackError: se la cartella manca o il pacchetto importato viene da altrove.
    """
    library = library.resolve()
    if not (library / "multimark" / "__init__.py").is_file():
        raise AttackError(
            f"{library}/multimark not found: run bash scripts/apply_patches.sh multimark"
        )
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    import multimark  # type: ignore[import-not-found]

    origin = Path(multimark.__file__).resolve().parent
    if origin != library / "multimark":
        raise AttackError(f"multimark imported from {origin}, expected {library / 'multimark'}")
    return multimark


def library_commit(library: Path) -> str:
    """Commit della copia: ``<library>.source_commit`` (``apply_patches.sh``) o, per un checkout
    git come ``third_party/multimark``, ``git rev-parse HEAD``."""
    marker = library.with_name(library.name + ".source_commit")
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()
    proc = subprocess.run(["git", "-C", str(library), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)  # fmt: skip
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def submodule_commit() -> str | None:
    """Commit registrato per ``third_party/multimark`` nell'indice del benchmark (gitlink)."""
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--stage", "--", "third_party/multimark"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    for line in proc.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "160000":
            return parts[1]
    return None


def profiles() -> dict[str, Any]:
    """Configurazioni della libreria uguali ai profili degli adapter del benchmark (§3.11):
    ``mcss`` = ``mcss_profile()``; ``multimark`` = ``multimark_base()`` con
    ``names_module_level = True`` (decisione O, l'unica differenza dal default della libreria)."""
    from multimark.config import mcss_profile, multimark_base  # type: ignore[import-not-found]

    return {
        "mcss": mcss_profile(),
        "multimark": dataclasses.replace(multimark_base(), names_module_level=True),
    }


# ---------------------------------------------------------------------------------- attacchi
def attack_none(code: str, cfg: Any, key: int, seed: int) -> str:
    """Nessun attacco (colonna «no attack» della Tabella 5)."""
    return code


# Tabella 5 di MCSS (p. 8): ``rename r`` (r variabili rinominate a caso) e inversione di tutti i
# siti di un canale si aggiungono in P4 con la stessa firma (codice, config, chiave, seme).
ATTACKS: dict[str, Callable[[str, Any, int, int], str]] = {"none": attack_none}


# ---------------------------------------------------------------------------------- casi
def case_seeds(global_seed: int, sample_id: str, k: int) -> tuple[list[int], int]:
    """Messaggio (24 bit) e chiave del caso ``(sample_id, k)``, uguali per i due profili."""
    value = derive_seed(global_seed, MSG_LABEL, sample_id, k) % (2**MESSAGE_BITS)
    message = [int(b) for b in format(value, f"0{MESSAGE_BITS}b")]
    return message, derive_seed(global_seed, KEY_LABEL, sample_id, k)


def run_case(task: tuple[str, dict[str, Any], int, str, int]) -> dict[str, Any]:
    """Inserimento, attacco e rilevazione di un file con un profilo e una chiave."""
    library, rec, k, attack, global_seed = task
    import_library(Path(library))
    from multimark._astindex import AstIndex  # type: ignore[import-not-found]
    from multimark.detect import detect  # type: ignore[import-not-found]
    from multimark.embed import embed  # type: ignore[import-not-found]
    from multimark.layout import build_layout, read_votes  # type: ignore[import-not-found]

    method = rec["_method"]
    cfg = profiles()[method]
    message, key = case_seeds(global_seed, rec["sample_id"], k)
    expected = "".join(map(str, message))
    out: dict[str, Any] = {
        "sample_id": rec["sample_id"], "problem_key": rec.get("problem_key"), "method": method,
        "attack": attack, "key_index": k, "expected_message": expected,
    }  # fmt: skip
    emb = embed(rec["code"], message, key, cfg)
    out.update(embed_status=emb.status, embed_reason=emb.reason,
               n_sites=emb.extra.get("n_sites"), skipped_sites=emb.extra.get("skipped_sites"),
               n_names_no_candidate=emb.extra.get("n_names_no_candidate"))  # fmt: skip
    if emb.status == "FAILED":
        out.update(detect_status=None, decode_status=None, decoded_ok=False, decoded_wrong=False,
                   bits_correct=0, covered=False, n_covered=0)  # fmt: skip
        return out
    attack_seed = derive_seed(global_seed, attack, rec["sample_id"], k)
    attacked = ATTACKS[attack](emb.code, cfg, key, attack_seed)
    det = detect(attacked, message, key, cfg, registry_sizes=(1,), method=method)
    n_covered = 0
    try:
        layout = build_layout(AstIndex.from_code(attacked), cfg, key)
        n_covered = len({v.index for v in read_votes(layout, key, cfg)})
    except (SyntaxError, ValueError):
        pass
    decoded = det.decoded_message
    out.update(
        detect_status=det.status,
        detect_reason=det.reason,
        score=det.score,
        n_votes=det.extra.get("n_votes"),
        decode_status=det.extra.get("decode_status"),
        n_erasures=det.extra.get("n_erasures"),
        bits_correct=int(det.extra.get("bits_correct") or 0),
        decoded_message=decoded,
        decoded_ok=decoded == expected,
        decoded_wrong=decoded is not None and decoded != expected,
        n_covered=n_covered,
        covered=n_covered == cfg.codeword_length,
        native_decision=det.native_decision,
    )
    return out


# ---------------------------------------------------------------------------------- statistiche
def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Intervallo di Wilson (come ``bench.metrics.stats.wilson`` con alpha = 0,05)."""
    if n == 0:
        return (math.nan, math.nan)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def proportion(label: str, successes: int, n: int) -> str:
    if n == 0:
        return f"  {label}: 0/0 (n/a)"
    lo, hi = wilson(successes, n)
    return f"  {label}: {successes}/{n} = {successes / n:.4f} [Wilson 95%: {lo:.4f}, {hi:.4f}]"


def summarise(rows: list[dict[str, Any]], method: str, b: int) -> tuple[list[str], dict[str, Any]]:
    """Righe del riepilogo di un profilo e numeri per il criterio (a)."""
    n = len(rows)
    statuses = Counter(r["embed_status"] for r in rows)
    reasons = Counter(r["embed_reason"] for r in rows if r["embed_status"] == "FAILED")
    big = [r for r in rows if (r["n_sites"] or 0) >= b]
    covered = [r for r in rows if r["covered"]]
    count = {
        "ok": sum(r["decoded_ok"] for r in rows),
        "wrong": sum(r["decoded_wrong"] for r in rows),
        "ambiguous": sum(r["decode_status"] == "ambiguous" for r in rows),
        "inconsistent": sum(r["decode_status"] == "inconsistent" for r in rows),
        "covered_ok": sum(r["decoded_ok"] for r in covered),
        "big_ambiguous": sum(r["decode_status"] == "ambiguous" for r in big),
        "big_wrong": sum(r["decoded_wrong"] for r in big),
    }
    bit_acc = sum(r["bits_correct"] for r in rows) / (MESSAGE_BITS * n) if n else math.nan
    files = len({r["sample_id"] for r in rows})
    lines = [
        f"method {method}: {n} cases ({files} files), B = {b}",
        "  embed status: "
        + ", ".join(f"{s} {statuses.get(s, 0)}" for s in ("OK", "PARTIAL", "FAILED"))
        + (f" (FAILED reasons: {dict(sorted(reasons.items()))})" if reasons else ""),
        f"  n_sites at embedding: mean {sum((r['n_sites'] or 0) for r in rows) / max(n, 1):.2f}; "
        f"N >= B: {len(big)}; skipped sites {sum((r['skipped_sites'] or 0) for r in rows)}; "
        f"names without candidate {sum((r['n_names_no_candidate'] or 0) for r in rows)}",
        proportion("message_accuracy (decoded == m, all cases)", count["ok"], n),
        f"  bit_accuracy (bits_correct / {MESSAGE_BITS}, mean): {bit_acc:.4f}",
        proportion("files with all B positions covered", len(covered), n),
        proportion("message_accuracy on covered files", count["covered_ok"], len(covered)),
        proportion("wrong decoded messages (all cases)", count["wrong"], n),
        proportion("wrong decoded messages (N >= B)", count["big_wrong"], len(big)),
        proportion("ambiguous decoding (all cases)", count["ambiguous"], n),
        proportion("ambiguous decoding (N >= B)", count["big_ambiguous"], len(big)),
        proportion("inconsistent decoding (all cases)", count["inconsistent"], n),
    ]
    count.update(n=n, n_covered=len(covered), n_big=len(big))
    return lines, count


def criterion(method: str, c: dict[str, Any]) -> str:
    """Esito del criterio (a) di P2 (solo senza attacchi)."""
    if method == "mcss":
        ok = c["n"] > 0 and c["ok"] == c["n"]
        rule = "message_accuracy = 1.00 on all files"
    else:
        ok = c["n_covered"] > 0 and c["covered_ok"] == c["n_covered"] and c["wrong"] == 0
        rule = "message_accuracy = 1.00 on covered files and 0 wrong messages"
    return f"  criterion (a) of P2 ({rule}): {'PASS' if ok else 'FAIL'}"


# ---------------------------------------------------------------------------------- ingresso/uscita
def read_selection(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if line.strip():
                rec = json.loads(line)
                if "sample_id" not in rec or "code" not in rec:
                    raise AttackError(f"{path}:{i}: sample_id and code are required")
                rows.append(rec)
    if not rows:
        raise AttackError(f"{path} is empty")
    if len({r["sample_id"] for r in rows}) != len(rows):
        raise AttackError(f"{path}: duplicate sample_id")
    return rows


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> None:
    """Scrittura atomica di un file che non deve esistere."""
    if path.exists():
        raise AttackError(f"{path} already exists: choose a new name (logs are never overwritten)")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def native_fields(cfg: Any) -> dict[str, Any]:
    return {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}


EXISTS = "already exists: choose a new name (logs are never overwritten)"
PAIRS = ((True, True), (True, False), (False, True), (False, False))


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--selection", required=True, type=Path, help="mcss_selftest_eel_<c>.jsonl")
    p.add_argument("--attack", choices=sorted(ATTACKS), default="none")
    p.add_argument("--keys", type=int, default=1, help="keys (and messages) per file")
    p.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    p.add_argument("--report", required=True, type=Path, help="text report (must not exist)")
    p.add_argument("--records", type=Path, help="per-case JSONL (optional, must not exist)")
    p.add_argument("--library", type=Path, default=DEFAULT_LIBRARY, help="multimark copy")
    p.add_argument("--global-seed", type=int, default=GLOBAL_SEED)
    p.add_argument("--workers", type=int, default=1)
    return p.parse_args(argv)


def execute(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    """Controlli, casi e intestazione del riepilogo.

    Raises:
        AttackError: uscita già presente, libreria non allineata o selezione non valida.
    """
    for out in (args.report, args.records):
        if out is not None and out.exists():
            raise AttackError(f"{out} {EXISTS}")
    if args.keys < 1:
        raise AttackError("--keys must be >= 1")
    multimark = import_library(args.library)
    commit, link = library_commit(args.library), submodule_commit()
    if link is not None and commit != link:
        raise AttackError(
            f"library at {commit} but submodule at {link}: "
            "run bash scripts/apply_patches.sh multimark"
        )
    selection = read_selection(args.selection)
    tasks = [(str(args.library), {**rec, "_method": m}, k, args.attack, args.global_seed)
             for m in args.methods for rec in selection for k in range(args.keys)]  # fmt: skip
    t0 = time.perf_counter()
    if args.workers > 1:
        with Pool(args.workers) as pool:
            rows = pool.map(run_case, tasks, chunksize=8)
    else:
        rows = [run_case(t) for t in tasks]
    elapsed = time.perf_counter() - t0
    header = [
        f"mcss_paper_attacks.py: attack = {args.attack} (MCSS Table 5, PIPELINE_metodo.md §4.4 "
        "point 3; criterion (a) of P2 with attack none)",
        f"selection: {args.selection} ({len(selection)} files, sha256 {sha256(args.selection)})",
        f"library: {args.library} at {commit} (multimark {multimark.__version__}); "
        f"submodule {link}",
        f"global_seed {args.global_seed}; keys per file {args.keys}; seeds "
        f"derive_seed(global_seed, '{MSG_LABEL}' | '{KEY_LABEL}', sample_id, k)",
        f"python {platform.python_version()}; workers {args.workers}; elapsed {elapsed:.1f} s",
        "message_accuracy = open decoding equal to the message (failed embeddings count as not "
        "recovered); covered = every codeword position has at least one vote after the attack; "
        "intervals: Wilson 95%",
        "",
    ]
    return rows, header


def report_lines(args: argparse.Namespace, rows: list[dict[str, Any]]) -> list[str]:
    """Riepilogo per profilo, criterio (a) senza attacchi ed esito appaiato dei due profili."""
    cfgs = profiles()
    lines: list[str] = []
    for method in args.methods:
        cfg = cfgs[method]
        lines.append(f"profile {method}: {json.dumps(native_fields(cfg), sort_keys=True)}")
        mine = [r for r in rows if r["method"] == method]
        body, count = summarise(mine, method, cfg.codeword_length)
        lines += body
        if args.attack == "none":
            lines.append(criterion(method, count))
        lines.append("")
    if set(args.methods) == set(METHODS):
        pairs: dict[str, dict[str, bool]] = {}
        for r in rows:
            case = f"{r['sample_id']}#{r['key_index']}"
            pairs.setdefault(case, {})[r["method"]] = r["decoded_ok"]
        both = Counter((v["mcss"], v["multimark"]) for v in pairs.values())
        outcome = ", ".join(f"{k}: {both.get(k, 0)}" for k in PAIRS)
        lines.append(f"paired outcome (mcss ok, multimark ok): {outcome}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, header = execute(args)
        text = "\n".join(header + report_lines(args, rows)).rstrip("\n") + "\n"
        write_new(args.report, text)
        if args.records is not None:
            write_new(args.records, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    except AttackError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] {len(rows)} cases -> {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
