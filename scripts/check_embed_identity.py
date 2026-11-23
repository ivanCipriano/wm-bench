"""Inserimento identico byte per byte fra due celle della stessa configurazione (giro P4 su dev).

mcss, B e D alla libreria b2b6c18 hanno hash nuovi (1d3087c73439, e52b040770df, 3fed1f1c1e24)
perché il commit della libreria entra nel ``config_hash``, ma le stesse opzioni delle celle già
misurate (ae63f16de28f a 3c47a1f, a8a296957377 e b4cd11f13a07 a 0664817). La loro
impercettibilità non si rimisura se l'inserimento è identico (decisione dell'utente del 23
novembre 2026): questo script lo verifica.

Per ogni coppia (``--pair metodo:nuovo:vecchio[:etichetta]``), modello e livello, legge i campioni
marcati delle due celle (``watermarked/<metodo>/<modello>/<hash>/<livello>_<ling>_<split>``) e li
appaia per ``parent_id`` (il campione della baseline: ``sample_id`` dipende dal ``config_hash``).
Stampa per cella: campioni, campioni con ``code`` identico, con ``embed_status`` e
``expected_message`` identici, campioni presenti in una sola cella, SHA-256 del codice di tutti i
campioni in ordine di ``parent_id`` per le due celle, ed ``IDENTICAL`` solo se tutto coincide. Alla
fine l'esito per coppia e complessivo.

Gira in **bench-core**, solo lettura degli artefatti::

    python scripts/check_embed_identity.py --pair mcss:1d3087c73439:ae63f16de28f:mcss \\
        --pair multimark:e52b040770df:a8a296957377:B [--levels L1 L2] [--report FILE] [...]

``--report`` non deve esistere (i log non si sovrascrivono). Esce con 1 se una coppia non è
identica o manca una cella.
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

FIELDS = ("code", "embed_status", "expected_message")


@dataclass(frozen=True)
class Pair:
    """Configurazione nuova e cella già misurata con le stesse opzioni."""

    method: str
    new: str
    old: str
    label: str

    @classmethod
    def parse(cls, text: str) -> Pair:
        parts = text.split(":")
        if not 3 <= len(parts) <= 4 or not all(parts):  # noqa: PLR2004
            raise argparse.ArgumentTypeError(f"--pair {text!r}: expected method:new:old[:label]")
        return cls(parts[0], parts[1], parts[2], parts[3] if parts[3:] else parts[0])


def _digest(codes: Sequence[str]) -> str:
    h = hashlib.sha256()
    for code in codes:
        h.update(code.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def compare(new: Any, old: Any) -> dict[str, Any]:
    """Confronto di due tabelle di campioni marcati appaiate per ``parent_id``."""
    a = {str(r["parent_id"]): r for r in new.to_dict(orient="records")}
    b = {str(r["parent_id"]): r for r in old.to_dict(orient="records")}
    common = sorted(set(a) & set(b))
    out: dict[str, Any] = {
        "n_new": len(a),
        "n_old": len(b),
        "common": len(common),
        "only_new": len(set(a) - set(b)),
        "only_old": len(set(b) - set(a)),
    }
    for f in FIELDS:
        out[f"same_{f}"] = sum(a[k][f] == b[k][f] for k in common)
    out["sha_new"] = _digest([str(a[k]["code"]) for k in sorted(a)])
    out["sha_old"] = _digest([str(b[k]["code"]) for k in sorted(b)])
    first = next((k for k in common if a[k]["code"] != b[k]["code"]), None)
    out["first_diff"] = first
    out["identical"] = (
        out["only_new"] == out["only_old"] == 0
        and len(common) > 0
        and all(out[f"same_{f}"] == len(common) for f in FIELDS)
        and out["sha_new"] == out["sha_old"]
    )
    return out


def cell_lines(store: Any, pair: Pair, model: str, level: str, lang: str, split: str
               ) -> tuple[list[str], bool]:  # fmt: skip
    from bench.pipeline.stages.watermark import watermarked_ref

    refs = {h: watermarked_ref(pair.method, model, h, level, lang, split)
            for h in (pair.new, pair.old)}  # fmt: skip
    missing = [str(r.path) for r in refs.values() if not store.exists(r)]
    where = f"{pair.label} {model} {level}"
    if missing:
        return [f"  {where}: MISSING {', '.join(missing)}"], False
    c = compare(store.read_table(refs[pair.new]), store.read_table(refs[pair.old]))
    verdict = "IDENTICAL" if c["identical"] else "DIFFERENT"
    lines = [
        f"  {where}: {verdict}; samples {c['n_new']} (new) / {c['n_old']} (old), paired "
        f"{c['common']}, only new {c['only_new']}, only old {c['only_old']}; same code "
        f"{c['same_code']}, same embed_status {c['same_embed_status']}, same expected_message "
        f"{c['same_expected_message']}",
        f"    sha256 of the code (parent_id order): new {c['sha_new']}",
        f"                                          old {c['sha_old']}",
    ]
    if c["first_diff"] is not None:
        lines.append(f"    first different parent_id: {c['first_diff']}")
    return lines, bool(c["identical"])


def run(store: Any, pairs: Sequence[Pair], models: Sequence[str], levels: Sequence[str],
        lang: str, split: str) -> tuple[list[str], bool]:  # fmt: skip
    """Righe del controllo e se tutte le coppie sono identiche."""
    lines: list[str] = []
    verdicts: dict[str, bool] = {}
    for pair in pairs:
        lines.append(f"## {pair.label}: {pair.method} {pair.new} (new) vs {pair.old} (measured)")
        ok = True
        for model in models:
            for level in levels:
                body, same = cell_lines(store, pair, model, level, lang, split)
                lines += body
                ok = ok and same
        verdicts[pair.label] = ok
        lines.append("")
    every = bool(verdicts) and all(verdicts.values())
    summary = ", ".join(f"{k} {'identical' if v else 'NOT identical'}" for k, v in verdicts.items())
    verdict = (
        "embedding identical byte for byte: the measured imperceptibility holds"
        if every
        else "NOT identical: imperceptibility must be measured again"
    )
    lines.append(f"## result: {summary} -> {verdict}")
    return lines, every


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--pair", type=Pair.parse, action="append", required=True,
                   help="method:new_hash:old_hash[:label], repeatable")  # fmt: skip
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1
    from bench.config.builder import load_experiment
    from bench.store.artifact_store import ArtifactStore

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=watermark", *args.overrides])
    store = ArtifactStore(Path(cfg.paths.artifacts))
    models = args.models or list(cfg.models)
    body, ok = run(store, args.pair, models, args.levels, args.language, args.split)
    lines = [
        "# embedding identity on development (scripts/check_embed_identity.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# models {' '.join(models)}; levels {' '.join(args.levels)}; split {args.split}, "
        f"language {args.language}; fields compared: {', '.join(FIELDS)} (paired by parent_id)",
        "",
        *body,
    ]
    text = "\n".join(lines) + "\n"
    if args.report is None:
        sys.stdout.write(text)
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as fh:
            fh.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
