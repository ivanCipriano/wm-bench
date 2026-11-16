"""Esempi prima/dopo l'inserimento di ``mcss`` e ``multimark`` (PIPELINE_metodo.md di multimark,
P2, regola 12): ``docs/reports/P2_esempi_bench.md``.

Per ogni esempio e per ogni profilo (gli stessi di ``scripts/mcss_paper_attacks.py``, cioè degli
adapter del benchmark) si scrivono in Markdown:

- stato dell'inserimento e siti per canale;
- differenza unificata fra il codice d'ingresso e quello marcato;
- tabella dei voti letti sul codice marcato: funzione, canale, sito (rango o nome della
  variabile), posizione nella parola di codice, valore letto, voto (dopo la maschera), bit atteso
  della parola di codice;
- rilevazione con il messaggio atteso: voti, concordanza ``A``, punteggio principale
  ``-log10 P[Bin(N, 1/2) >= A]``, punteggi con il registro (16 e 1024), decodifica aperta.

Esempi:

- ``--input`` JSONL e ``--ids``: righe con ``sample_id`` e ``code``, oppure righe di HumanEval(+)
  con ``task_id``, ``prompt`` e ``canonical_solution`` (codice = prompt + soluzione canonica, come
  le canoniche di L1);
- ``--synthetic SEED:STATEMENTS:FUNCTIONS``: frammento sintetico della libreria
  (``multimark.synthetic.generate``), per mostrare un caso con N >= B quando non c'è codice lungo
  del benchmark a portata di mano.

Messaggio e chiave: ``derive_seed(global_seed, "posthoc-examples-msg" | "posthoc-examples-key",
sample_id)``. ``--out`` non deve esistere.

Uso (dati locali, come per il report di P2)::

    python scripts/posthoc_examples.py --input tests/fixtures/tiny/humanevalplus.jsonl \\
        --ids HumanEval/1 HumanEval/2 --synthetic 4:30:2 --out docs/reports/P2_esempi_bench.md
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bench_contracts import derive_seed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcss_paper_attacks import (
    DEFAULT_LIBRARY,
    GLOBAL_SEED,
    MESSAGE_BITS,
    METHODS,
    AttackError,
    import_library,
    library_commit,
    profiles,
    write_new,
)

MSG_LABEL, KEY_LABEL = "posthoc-examples-msg", "posthoc-examples-key"


def read_examples(path: Path, ids: Sequence[str]) -> list[tuple[str, str, str]]:
    """``(sample_id, origine, codice)`` per gli ``ids`` richiesti, nell'ordine dato."""
    found: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            sid = rec.get("sample_id") or rec.get("task_id")
            if sid in ids:
                if "code" in rec:
                    found[sid] = (f"`{path.as_posix()}`", rec["code"])
                else:
                    origin = f"`{path.as_posix()}` (prompt + canonical_solution)"
                    found[sid] = (origin, rec["prompt"] + rec["canonical_solution"])
    missing = [i for i in ids if i not in found]
    if missing:
        raise AttackError(f"{path}: ids not found {missing}")
    return [(i, *found[i]) for i in ids]


def synthetic(spec: str) -> tuple[str, str, str]:
    from multimark.synthetic import generate  # type: ignore[import-not-found]

    seed, statements, functions = (int(x) for x in spec.split(":"))
    origin = f"`multimark.synthetic.generate({seed}, {statements}, {functions})`"
    return f"synthetic-{seed}", origin, str(generate(seed, statements, functions))


def seeds(global_seed: int, sample_id: str) -> tuple[list[int], int]:
    value = derive_seed(global_seed, MSG_LABEL, sample_id) % (2**MESSAGE_BITS)
    return [int(b) for b in format(value, f"0{MESSAGE_BITS}b")], derive_seed(
        global_seed, KEY_LABEL, sample_id
    )


def _fence(code: str, lang: str = "python") -> list[str]:
    return [f"```{lang}", code.rstrip("\n"), "```"]


def vote_rows(code: str, cfg: Any, key: int, message: list[int]) -> list[str]:
    """Tabella Markdown dei voti letti su ``code``."""
    from multimark._astindex import AstIndex  # type: ignore[import-not-found]
    from multimark.codec import encode  # type: ignore[import-not-found]
    from multimark.layout import build_layout, read_votes  # type: ignore[import-not-found]

    cw = encode(message, cfg.t, cfg.code_matrix)
    layout = build_layout(AstIndex.from_code(code), cfg, key)
    out = [
        "| riga | funzione | canale | sito | posizione | letto | voto | cw[pos] | concorda |",
        "|---:|---|---|---|---:|---:|---:|---:|:---:|",
    ]
    for v in read_votes(layout, key, cfg):
        slot = v.slot
        if slot.name is not None:
            unit, site, line = slot.name.unit, f"`{slot.name.name}`", slot.name.lineno
        else:
            unit, site, line = slot.site.unit, f"rango {slot.site.rank}", slot.site.lineno
        agree = "sì" if v.value == cw[v.index] else "**no**"
        out.append(
            f"| {line} | `{unit.name}` | {slot.channel} | {site} | {v.index} | "
            f"{v.value_read} | {v.value} | {cw[v.index]} | {agree} |"
        )
    return out


def method_section(
    method: str, cfg: Any, code: str, message: list[int], key: int, global_seed: int
) -> list[str]:
    from multimark.detect import detect  # type: ignore[import-not-found]
    from multimark.embed import embed  # type: ignore[import-not-found]

    emb = embed(code, message, key, cfg)
    by_channel = {c: n for c, n in (emb.extra.get("n_sites_by_channel") or {}).items() if n}
    lines = [
        f"#### `{method}`",
        "",
        f"- **Inserimento:** `{emb.status}`" + (f" (`{emb.reason}`)" if emb.reason else "")
        + f"; N = {emb.extra.get('n_sites')} (B = {cfg.codeword_length}); siti per canale: "
        + (", ".join(f"{c} {n}" for c, n in by_channel.items()) or "nessuno")
        + f"; variabili rinominate {emb.extra.get('n_renamed', 0)}.",
    ]  # fmt: skip
    if emb.status == "FAILED":
        lines += ["- Il codice marcato resta quello d'ingresso (negativo per costruzione).", ""]
        return lines
    diff = difflib.unified_diff(code.splitlines(), emb.code.splitlines(), "prima", "dopo",
                                lineterm="", n=1)  # fmt: skip
    det = detect(emb.code, message, key, cfg, registry_seed=global_seed, method=method)
    x = det.extra
    expected = "".join(map(str, message))
    lines += ["", "Differenza:", "", *_fence("\n".join(diff) or "(nessuna)", "diff"), ""]
    lines += ["Voti letti sul codice marcato:", "", *vote_rows(emb.code, cfg, key, message), ""]
    decoded = det.decoded_message
    outcome = "uguale al messaggio" if decoded == expected else (
        "nessun messaggio" if decoded is None else "**messaggio sbagliato**")  # fmt: skip
    lines += [
        f"- **Rilevazione:** {x['n_votes']} voti, concordanza A = {x['agreement']:g}; punteggio "
        f"principale (N_reg = 1) {det.score:.3f}; con il registro: N_reg = 16 "
        f"{x['score_nreg16']:.3f}, N_reg = 1024 {x['score_nreg1024']:.3f} (messaggio scelto "
        f"{'giusto' if x['best_nreg1024'] == expected else 'sbagliato'}).",
        f"- **Decodifica aperta:** `{x['decode_status']}`, {x['n_erasures']} posizioni cancellate, "
        f"{outcome}; bit giusti {x['bits_correct']}/{MESSAGE_BITS}; "
        f"`native_decision` = {det.native_decision}.",
        "",
    ]
    return lines


def render(examples: list[tuple[str, str, str]], global_seed: int, commit: str) -> str:
    cfgs = profiles()
    out = [
        "# Esempi di inserimento di `mcss` e `multimark` nel benchmark (P2)",
        "",
        f"Generato da `scripts/posthoc_examples.py`: libreria `multimark` al commit `{commit}`, "
        f"`global_seed` {global_seed}, profili degli adapter del benchmark (`mcss`, `multimark` "
        "con proponente statico). Messaggio e chiave per esempio da "
        f"`derive_seed(global_seed, '{MSG_LABEL}' | '{KEY_LABEL}', sample_id)`.",
        "",
        "Colonne della tabella dei voti: *letto* è il valore del sito sul codice (forma del "
        "canale; per i nomi il bit della chiave con V2, il bit dello stile con V1), *voto* è il "
        "valore dopo la maschera (con `mcss` la maschera è spenta), *cw[pos]* è il bit della "
        "parola di codice del messaggio in quella posizione. Con V1 (`mcss`) una variabile porta "
        "due voti.",
        "",
    ]
    for n, (sid, origin, code) in enumerate(examples, 1):
        message, key = seeds(global_seed, sid)
        out += [f"## Esempio {n}: `{sid}`", "", f"Origine: {origin}. Messaggio "
                f"`{''.join(map(str, message))}`.", "", *_fence(code), ""]  # fmt: skip
        for method in METHODS:
            out += method_section(method, cfgs[method], code, message, key, global_seed)
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--input", type=Path, help="JSONL with sample_id+code or HumanEval rows")
    p.add_argument("--ids", nargs="*", default=[], help="sample_id / task_id to show")
    p.add_argument("--synthetic", nargs="*", default=[], help="SEED:STATEMENTS:FUNCTIONS")
    p.add_argument("--out", required=True, type=Path, help="Markdown report (must not exist)")
    p.add_argument("--library", type=Path, default=DEFAULT_LIBRARY, help="multimark copy")
    p.add_argument("--global-seed", type=int, default=GLOBAL_SEED)
    args = p.parse_args(argv)
    try:
        if args.out.exists():
            raise AttackError(f"{args.out} already exists: choose a new name")
        if args.ids and args.input is None:
            raise AttackError("--ids needs --input")
        import_library(args.library)
        examples = read_examples(args.input, args.ids) if args.ids else []
        examples += [synthetic(s) for s in args.synthetic]
        if not examples:
            raise AttackError("no examples: give --ids or --synthetic")
        write_new(args.out, render(examples, args.global_seed, library_commit(args.library)))
    except AttackError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] {len(examples)} examples -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
