"""Attacchi della Tabella 5 di MCSS sulla selezione CodeNet (PIPELINE_metodo.md di multimark §4.4
punto 3; criterio (a) di P2).

Per ogni file della selezione (``mcss_selftest_eel_<c>.jsonl`` di
``scripts/select_mcss_selftest.py``, fascia 32–39 bit) e per ogni chiave (``--keys``, default 1):

1. messaggio di 24 bit e chiave dai semi ``derive_seed(global_seed, "mcss-paper-attacks-msg" |
   "mcss-paper-attacks-key", sample_id, k)``, uguali per i due profili (confronto appaiato);
2. inserimento con il profilo ``mcss`` e con il profilo ``multimark`` del benchmark (``PROFILES``:
   gli stessi campi nativi degli adapter ``McssAdapter`` e ``MultimarkAdapter``, verificato da
   ``tests/unit/test_mcss_paper_attacks.py``);
3. attacco ``--attack`` sul codice marcato (Tabella 5, p. 8), con il seme
   ``derive_seed(global_seed, <attacco>, sample_id, k)``:

   - ``none``;
   - ``rename<r>``, r ∈ {2, 4, 8, 16}: r variabili sito del canale dei nomi (tutte se sono meno),
     scelte a caso, rinominate in modo coerente con un nome casuale della raccolta di T1.4
     (``--names-pool``, ``data/attack_pools/python.json``: attaccante realistico, lo stile è
     quello che capita) che non sia già una parola del file, una parola riservata o un builtin;
   - ``rename<r>_lower``: come sopra con una parola minuscola casuale di 5–8 lettere (un solo
     segmento: per V1 ogni rinomina diventa una cancellazione). Le due letture sono una decisione
     dell'utente del 23 novembre 2026 e si riportano affiancate;
   - ``invert_<canale>`` per gli 8 canali di MCSS (Tabella 2): tutti i siti del canale nella forma
     opposta (i siti non scrivibili restano);
   - ``table5``: tutti gli attacchi qui sopra. Il riepilogo è la Tabella 5 per profilo, con gli
     ordinamenti attesi su ``mcss`` (§4.4 punto 3: no-attack 100%; ``rename 2`` e ``rename 4``
     ≥ 95%; ``rename 8`` < 10%; ``rename 16`` ≈ 0, cioè ≤ 2%; recupero non crescente in r;
     ``operand_order`` il canale peggiore e ``none_check`` il migliore, pareggi ammessi) per le
     due letture della rinomina, e quale delle due si avvicina di più all'andamento del paper
     (più ordinamenti rispettati, poi scarto totale minore dai limiti);
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
    <posthoc>/bin/python scripts/mcss_paper_attacks.py --attack table5 --workers 8 \\
        --selection $INV/mcss_selftest_eel_fb311767f65c.jsonl \\
        --names-pool $WMB/artifacts/data/attack_pools/python.json \\
        --report docs/milestone_logs/P4_mcss_table5.txt
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
# Firma comune: (codice marcato, config, chiave, seme dell'attacco, raccolta di nomi di T1.4).
AttackFn = Callable[[str, Any, int, int, Sequence[str]], str]
RENAME_R = (2, 4, 8, 16)
# Gli 8 canali strutturali di MCSS (Tabella 2, p. 5; ``multimark.channels.CHANNELS[:8]``).
MCSS_CHANNELS = ("aug_assign", "compare_dir", "equality_dir", "list_comp", "ternary",
                 "operand_order", "empty_coll", "none_check")  # fmt: skip
LOWER = "abcdefghijklmnopqrstuvwxyz"


def attack_none(code: str, cfg: Any, key: int, seed: int, names: Sequence[str]) -> str:
    """Nessun attacco (colonna «no attack» della Tabella 5)."""
    return code


def _taken(code: str) -> set[str]:
    import builtins
    import keyword
    import re

    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code))
    return words | set(keyword.kwlist) | set(keyword.softkwlist) | set(dir(builtins))


def _pool_name(rng: Any, taken: set[str], names: Sequence[str]) -> str:
    """Nome casuale della raccolta di T1.4 (``data/attack_pools/python.json``) non già usato."""
    free = [n for n in names if n not in taken]
    if not free:
        raise AttackError("rename: the name pool has no free name (pass --names-pool)")
    return str(rng.choice(free))


def _lower_name(rng: Any, taken: set[str], names: Sequence[str]) -> str:
    """Parola minuscola casuale di 5–8 lettere: un solo segmento, stile illeggibile per V1."""
    while True:
        word = "".join(rng.choice(LOWER) for _ in range(rng.randint(5, 8)))
        if word not in taken:
            return word


def rename_random(code: str, cfg: Any, key: int, seed: int, names: Sequence[str], r: int,
                  pick: Callable[[Any, set[str], Sequence[str]], str]) -> str:  # fmt: skip
    """Rinomina ``r`` variabili sito del canale dei nomi (tutte se sono meno di ``r``), scelte a
    caso con il seme dell'attacco; ``pick`` dà il nome nuovo. La rinomina è coerente (tutte le
    occorrenze, ``multimark.rewrite.rename_variables``)."""
    import random

    from multimark._astindex import AstIndex  # type: ignore[import-not-found]
    from multimark.layout import build_layout  # type: ignore[import-not-found]
    from multimark.rewrite import rename_variables  # type: ignore[import-not-found]

    index = AstIndex.from_code(code)
    sites = sorted({s.name.uid: s.name for s in build_layout(index, cfg, key).slots
                    if s.name is not None}.items())  # fmt: skip
    rng = random.Random(seed)
    chosen = [ns for _, ns in rng.sample(sites, min(r, len(sites)))]
    taken = _taken(code)
    renames = []
    for ns in chosen:
        new = pick(rng, taken, names)
        taken.add(new)
        renames.append((ns, new))
    out, _failed = rename_variables(code, index, renames)
    return str(out)


def invert_channel(code: str, cfg: Any, key: int, seed: int, names: Sequence[str],
                   channel: str) -> str:  # fmt: skip
    """Inverte la forma di tutti i siti di ``channel`` (canonica ↔ alternativa); i siti non
    scrivibili restano come sono."""
    from multimark._astindex import AstIndex  # type: ignore[import-not-found]
    from multimark.layout import read_options  # type: ignore[import-not-found]
    from multimark.rewrite import WriteRequest, write_structural  # type: ignore[import-not-found]
    from multimark.sites import find_sites  # type: ignore[import-not-found]

    index = AstIndex.from_code(code)
    sites = [s for s in find_sites(index, read_options(cfg)) if s.channel == channel]
    out, _unwritable = write_structural(code, index, [WriteRequest(s, 1 - s.value) for s in sites])
    return str(out)


def _rename(r: int, pick: Callable[[Any, set[str], Sequence[str]], str]) -> AttackFn:
    def fn(code: str, cfg: Any, key: int, seed: int, names: Sequence[str]) -> str:
        return rename_random(code, cfg, key, seed, names, r, pick)

    return fn


def _invert(channel: str) -> AttackFn:
    def fn(code: str, cfg: Any, key: int, seed: int, names: Sequence[str]) -> str:
        return invert_channel(code, cfg, key, seed, names, channel)

    return fn


# Tabella 5 di MCSS (p. 8): ``rename r`` (r variabili rinominate a caso; lettura principale con i
# nomi della raccolta di T1.4, seconda lettura con parole minuscole casuali, decisione dell'utente
# del 23 novembre 2026) e inversione di tutti i siti di un canale.
ATTACKS: dict[str, AttackFn] = {"none": attack_none}
ATTACKS.update({f"rename{r}": _rename(r, _pool_name) for r in RENAME_R})
ATTACKS.update({f"rename{r}_lower": _rename(r, _lower_name) for r in RENAME_R})
ATTACKS.update({f"invert_{c}": _invert(c) for c in MCSS_CHANNELS})
TABLE5 = ("none", *(f"rename{r}" for r in RENAME_R), *(f"rename{r}_lower" for r in RENAME_R),
          *(f"invert_{c}" for c in MCSS_CHANNELS))  # fmt: skip
NEEDS_NAMES = tuple(f"rename{r}" for r in RENAME_R)


# ---------------------------------------------------------------------------------- casi
def case_seeds(global_seed: int, sample_id: str, k: int) -> tuple[list[int], int]:
    """Messaggio (24 bit) e chiave del caso ``(sample_id, k)``, uguali per i due profili."""
    value = derive_seed(global_seed, MSG_LABEL, sample_id, k) % (2**MESSAGE_BITS)
    message = [int(b) for b in format(value, f"0{MESSAGE_BITS}b")]
    return message, derive_seed(global_seed, KEY_LABEL, sample_id, k)


def run_case(task: tuple[str, dict[str, Any], int, str, int, tuple[str, ...]]) -> dict[str, Any]:
    """Inserimento, attacco e rilevazione di un file con un profilo e una chiave."""
    library, rec, k, attack, global_seed, names = task
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
                   bits_correct=0, covered=False, n_covered=0, changed=False)  # fmt: skip
        return out
    attack_seed = derive_seed(global_seed, attack, rec["sample_id"], k)
    attacked = ATTACKS[attack](emb.code, cfg, key, attack_seed, names)
    out["changed"] = attacked != emb.code
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


# ---------------------------------------------------------------------------------- tabella 5
RENAME_HIGH, RENAME_LOW, RENAME_ZERO = 0.95, 0.10, 0.02


def accuracy(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """(messaggi recuperati, casi)."""
    return sum(bool(r["decoded_ok"]) for r in rows), len(rows)


def _rate(rows: Sequence[dict[str, Any]]) -> float:
    ok, n = accuracy(rows)
    return ok / n if n else math.nan


def _cell(rows: Sequence[dict[str, Any]]) -> str:
    ok, n = accuracy(rows)
    if not n:
        return "n/a"
    lo, hi = wilson(ok, n)
    return f"{ok}/{n} = {ok / n:.3f} [{lo:.3f}, {hi:.3f}]"


def rename_checks(rate: Callable[[str], float], suffix: str) -> list[tuple[str, bool, float]]:
    """Ordinamenti attesi sulle rinomine (§4.4 punto 3) per una lettura: (regola, esito, scarto
    dal limite, 0 se rispettato)."""
    r2, r4, r8, r16 = (rate(f"rename{r}{suffix}") for r in RENAME_R)
    rules = [
        (f"rename 2 >= {RENAME_HIGH:.2f}", r2 >= RENAME_HIGH, max(0.0, RENAME_HIGH - r2)),
        (f"rename 4 >= {RENAME_HIGH:.2f}", r4 >= RENAME_HIGH, max(0.0, RENAME_HIGH - r4)),
        (f"rename 8 < {RENAME_LOW:.2f}", r8 < RENAME_LOW, max(0.0, r8 - RENAME_LOW)),
        (f"rename 16 ~ 0 (<= {RENAME_ZERO:.2f})", r16 <= RENAME_ZERO, max(0.0, r16 - RENAME_ZERO)),
        ("rename 2 >= 4 >= 8 >= 16", r2 >= r4 >= r8 >= r16, 0.0),
    ]
    return [(name, bool(ok) and not math.isnan(gap), gap) for name, ok, gap in rules]


def table5_lines(rows: list[dict[str, Any]], methods: Sequence[str]) -> list[str]:
    """Tabella 5 per profilo, le due letture della rinomina affiancate e gli ordinamenti attesi
    (solo per ``mcss``: sono quelli del paper)."""
    by: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault((r["method"], r["attack"]), []).append(r)
    lines = ["## MCSS Table 5 (message_accuracy = open decoding equal to the message; Wilson 95%)"]
    for method in methods:
        lines += ["", f"### {method}",
                  f"  {'attack':<24}{'recovered':<34}{'changed':>9}{'bit_acc':>9}"]  # fmt: skip
        for attack in TABLE5:
            mine = by.get((method, attack), [])
            if not mine:
                continue
            changed = sum(bool(r.get("changed")) for r in mine) / len(mine)
            bits = sum(r["bits_correct"] for r in mine) / (MESSAGE_BITS * len(mine))
            lines.append(f"  {attack:<24}{_cell(mine):<34}{changed:>9.3f}{bits:>9.3f}")
        lines += ["", f"  rename r, the two readings side by side ({method}):",
                  f"  {'r':<6}{'T1.4 pool names (main)':<36}"
                  f"{'random lowercase word':<36}"]  # fmt: skip
        for r in RENAME_R:
            pool = by.get((method, f"rename{r}"), [])
            low = by.get((method, f"rename{r}_lower"), [])
            lines.append(f"  {r:<6}{_cell(pool):<36}{_cell(low):<36}")
    if "mcss" not in methods:
        return lines

    def rate(attack: str) -> float:
        return _rate(by.get(("mcss", attack), []))

    lines += ["", "## expected orderings of Table 5 on mcss (PIPELINE §4.4 point 3; orderings, "
              "not exact values)"]  # fmt: skip
    none = rate("none")
    lines.append(f"  no attack = 1.00: {'PASS' if none == 1.0 else 'FAIL'} ({none:.3f})")
    channels = {c: rate(f"invert_{c}") for c in MCSS_CHANNELS}
    known = {c: v for c, v in channels.items() if not math.isnan(v)}
    worst = min(known.values()) if known else math.nan
    best = max(known.values()) if known else math.nan
    order = ", ".join(f"{c} {v:.3f}" for c, v in sorted(known.items(), key=lambda x: x[1]))
    lines.append(f"  channel inversions, worst to best: {order}")
    worst_ok = channels["operand_order"] == worst
    best_ok = channels["none_check"] == best
    lines.append(f"  operand_order is the worst (ties allowed): {'PASS' if worst_ok else 'FAIL'}")
    lines.append(f"  none_check is the best (ties allowed): {'PASS' if best_ok else 'FAIL'}")
    score: dict[str, tuple[int, float]] = {}
    for label, suffix in (("T1.4 pool names (main)", ""), ("random lowercase word", "_lower")):
        checks = rename_checks(rate, suffix)
        lines.append(f"  rename, {label}:")
        for name, ok, gap in checks:
            extra = f" (off by {gap:.3f})" if not ok and gap > 0 else ""
            lines.append(f"    {name}: {'PASS' if ok else 'FAIL'}{extra}")
        score[label] = (sum(ok for _, ok, _ in checks), sum(g for _, _, g in checks))
    (a, (pa, ga)), (b, (pb, gb)) = score.items()
    if (pa, -ga) == (pb, -gb):
        verdict = "the two readings are equally close to the paper"
    else:
        closer = a if (pa, -ga) > (pb, -gb) else b
        verdict = f"closer to the pattern of the paper: {closer}"
    lines.append(
        f"  -> {verdict} (rename checks passed: {a} {pa}/5, {b} {pb}/5; total distance "
        f"from the limits {ga:.3f} and {gb:.3f})"
    )
    return lines


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
    p.add_argument(
        "--attack",
        choices=[*sorted(ATTACKS), "table5"],
        default="none",
        help="one attack, or table5 = all the attacks of MCSS Table 5",
    )
    p.add_argument(
        "--names-pool",
        type=Path,
        help="data/attack_pools/python.json (names of T1.4, for rename<r>)",
    )
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
    attacks = TABLE5 if args.attack == "table5" else (args.attack,)
    names: tuple[str, ...] = ()
    if any(a in NEEDS_NAMES for a in attacks):
        if args.names_pool is None or not args.names_pool.is_file():
            raise AttackError("rename<r> needs --names-pool data/attack_pools/python.json")
        names = tuple(sorted(json.loads(args.names_pool.read_text(encoding="utf-8"))["names"]))
        if not names:
            raise AttackError(f"{args.names_pool}: empty name pool")
    tasks = [(str(args.library), {**rec, "_method": m}, k, a, args.global_seed, names)
             for a in attacks for m in args.methods for rec in selection
             for k in range(args.keys)]  # fmt: skip
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
        *(
            [
                f"names pool: {args.names_pool} ({len(names)} names, sha256 "
                f"{sha256(args.names_pool)})"
            ]
            if names
            else []
        ),  # fmt: skip
        "message_accuracy = open decoding equal to the message (failed embeddings count as not "
        "recovered); covered = every codeword position has at least one vote after the attack; "
        "intervals: Wilson 95%",
        "",
    ]
    return rows, header


def report_lines(args: argparse.Namespace, rows: list[dict[str, Any]]) -> list[str]:
    """Riepilogo per profilo, criterio (a) senza attacchi ed esito appaiato dei due profili; con
    ``--attack table5`` la Tabella 5 e i suoi ordinamenti."""
    cfgs = profiles()
    if args.attack == "table5":
        head = [f"profile {m}: {json.dumps(native_fields(cfgs[m]), sort_keys=True)}"
                for m in args.methods]  # fmt: skip
        return [*head, "", *table5_lines(rows, args.methods)]
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
