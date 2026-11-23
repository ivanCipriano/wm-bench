"""Casi falliti, incoerenti o non coperti di ``scripts/mcss_paper_attacks.py``, con i siti
(PIPELINE_metodo.md di multimark, P2: criterio (a)).

Legge i record per caso di ``mcss_paper_attacks.py`` (``--records``) e la selezione con il codice
(``--selection``). Esporta ogni caso dei due metodi con almeno uno di questi motivi:

- ``embed_<motivo>``: inserimento ``FAILED`` (``rewrite_mismatch``, ``no_sites``, ...);
- ``decode_<stato>``: decodifica aperta ``inconsistent`` o ``ambiguous``;
- ``not_covered``: non tutte le B posizioni hanno un voto;
- ``wrong_message``: decodifica riuscita ma diversa dal messaggio.

Per ogni caso l'inserimento si rifà con gli stessi semi (``case_seeds`` di
``mcss_paper_attacks.py``), lo stesso profilo e lo stesso attacco. ``reproduced`` dice se stato e
decodifica coincidono con il record. ``rename_variables`` e ``write_structural`` della libreria
si avvolgono **in questo processo** (``third_party`` non si tocca), così si conservano anche i
tentativi di riscrittura che la libreria scarta con ``rewrite_mismatch``.

Righe JSONL dell'uscita, una per caso:

- ``original_code`` e ``marked_code``; ``marked_code_kind`` vale ``final`` (codice dello shim),
  ``last_attempt`` (riscrittura scartata, ``FAILED``) o ``none``; ``diff`` unificata fra i due;
- ``embedding_sites``: siti dell'ultimo tentativo con funzione, canale, rango o nome, riga,
  posizioni, valore originale, **valore scritto** (strutturali: forma richiesta; nomi: nome nuovo
  e bit attesi della parola di codice) ed eventuale ``unwritable``;
- ``detection_sites``: siti letti sul codice marcato (dopo l'attacco) con posizioni, valore
  letto, voti e bit attesi;
- ``differences``: siti persi o nuovi fra inserimento e rilevazione, posizioni cambiate, valori
  letti diversi da quelli scritti, nomi non leggibili, voti in disaccordo, posizioni senza voto,
  posizioni con maggioranza sbagliata o in parità;
- ``decode``: stato, conteggio dei voti per posizione e parola di codice attesa.

Gira nell'ambiente **posthoc**. ``--out`` non deve esistere.

Uso::

    <posthoc>/bin/python scripts/export_posthoc_failures.py --records R.jsonl \\
        --selection SEL.jsonl --out FAILURES.jsonl
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_contracts import derive_seed
from mcss_paper_attacks import (
    ATTACKS,
    DEFAULT_LIBRARY,
    GLOBAL_SEED,
    METHODS,
    AttackError,
    case_seeds,
    import_library,
    profiles,
    read_selection,
    write_new,
)


def reasons_of(rec: dict[str, Any]) -> list[str]:
    """Motivi per cui un record va esportato (lista vuota: caso riuscito)."""
    out = []
    if rec.get("embed_status") == "FAILED":
        out.append(f"embed_{rec.get('embed_reason')}")
    if rec.get("decode_status") not in (None, "ok"):
        out.append(f"decode_{rec['decode_status']}")
    if rec.get("embed_status") != "FAILED" and not rec.get("covered"):
        out.append("not_covered")
    if rec.get("decoded_wrong"):
        out.append("wrong_message")
    return out


def read_records(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    if not rows:
        raise AttackError(f"{path} is empty")
    return rows


def _key(k: Any) -> str:
    return repr(k)


def _slot_info(slot: Any) -> dict[str, Any]:
    if slot.site is not None:
        s = slot.site
        return {"kind": "structural", "function": s.unit.name, "channel": slot.channel,
                "rank": s.rank, "line": s.lineno, "positions": list(slot.indices),
                "mask": slot.mask}  # fmt: skip
    n = slot.name
    return {"kind": "name", "function": n.unit.name, "channel": slot.channel, "name": n.name,
            "rank": n.rank, "line": n.lineno, "positions": list(slot.indices)}  # fmt: skip


class Tracer:
    """Avvolge ``rename_variables`` e ``write_structural`` di ``multimark.embed`` e conserva i
    tentativi di un inserimento (ingressi, uscite, siti non scrivibili)."""

    def __init__(self) -> None:
        import multimark.embed as emb  # type: ignore[import-not-found]

        self.emb = emb
        self.attempts: list[dict[str, Any]] = []

    def __enter__(self) -> Tracer:
        emb, attempts = self.emb, self.attempts
        self._rename, self._write = emb.rename_variables, emb.write_structural
        rename, write = self._rename, self._write

        def traced_rename(code: str, index: Any, renames: list[Any]) -> Any:
            attempt: dict[str, Any] = {"renames": {_key(ns.key): (ns.name, new)
                                                   for ns, new in renames}}  # fmt: skip
            attempts.append(attempt)
            out = rename(code, index, renames)
            attempt.update(code1=out[0], failed_renames=sorted(map(_key, out[1])))
            return out

        def traced_write(code: str, index: Any, requests: list[Any]) -> Any:
            attempt = attempts[-1] if attempts else {}
            attempt["requests"] = {_key(r.site.key): (r.site.value, r.target) for r in requests}
            out = write(code, index, requests)
            attempt.update(code2=out[0], unwritable=sorted(map(_key, out[1])))
            return out

        emb.rename_variables, emb.write_structural = traced_rename, traced_write
        return self

    def __exit__(self, *exc: object) -> None:
        self.emb.rename_variables, self.emb.write_structural = self._rename, self._write


def embedding_sites(
    code: str, cfg: Any, key: int, cw: list[int], attempt: dict[str, Any]
) -> list[dict[str, Any]]:
    """Siti dell'inserimento: layout del codice dopo le rinomine dell'ultimo tentativo (o
    dell'originale) con il valore scritto."""
    from multimark._astindex import AstIndex  # type: ignore[import-not-found]
    from multimark.layout import build_layout  # type: ignore[import-not-found]

    base = attempt.get("code1") or code
    try:
        layout = build_layout(AstIndex.from_code(base), cfg, key)
    except (SyntaxError, ValueError):
        return []
    renames = attempt.get("renames", {})
    failed = set(attempt.get("failed_renames", []))
    requests = attempt.get("requests", {})
    unwritable = set(attempt.get("unwritable", []))
    out = []
    for slot in layout.slots:
        info = _slot_info(slot)
        k = _key(slot.key)
        info["site_key"] = k
        info["codeword_bits"] = [cw[i] for i in slot.indices]
        if slot.site is not None:
            original, target = requests.get(k, (slot.site.value, None))
            info.update(original_value=original, written_value=target,
                        unwritable=k in unwritable)  # fmt: skip
        else:
            old, new = renames.get(k, (slot.name.name, None))
            info.update(original_name=old, written_name=new, rename_failed=k in failed)
        out.append(info)
    return out


def detection_sites(
    code: str, cfg: Any, key: int, cw: list[int]
) -> tuple[list[dict[str, Any]], Any]:
    """Siti letti sul codice marcato, con voti e bit attesi; anche i voti (per le posizioni)."""
    from multimark._astindex import AstIndex
    from multimark.layout import build_layout, name_value, read_votes

    try:
        layout = build_layout(AstIndex.from_code(code), cfg, key)
    except (SyntaxError, ValueError):
        return [], None
    votes = read_votes(layout, key, cfg)
    by_slot: dict[int, list[Any]] = {}
    for v in votes:
        by_slot.setdefault(id(v.slot), []).append(v)
    out = []
    for slot in layout.slots:
        info = _slot_info(slot)
        info["site_key"] = _key(slot.key)
        vs = by_slot.get(id(slot), [])
        info["value_read"] = (slot.site.value if slot.site is not None
                              else name_value(slot, key, cfg))  # fmt: skip
        info["votes"] = [{"position": v.index, "vote": v.value, "expected": cw[v.index],
                          "agrees": v.value == cw[v.index]} for v in vs]  # fmt: skip
        out.append(info)
    return out, votes


def differences(emb: list[dict[str, Any]], det: list[dict[str, Any]], votes: Any, b: int,
                cw: list[int]) -> dict[str, Any]:  # fmt: skip
    """Confronto fra siti scritti e letti e stato delle posizioni della parola di codice."""
    e = {s["site_key"]: s for s in emb}
    d = {s["site_key"]: s for s in det}
    out: dict[str, Any] = {
        "lost_sites": [e[k] for k in e if k not in d],
        "new_sites": [d[k] for k in d if k not in e],
        "moved_positions": [
            {"site_key": k, "embedding": e[k]["positions"], "detection": d[k]["positions"]}
            for k in e if k in d and e[k]["positions"] != d[k]["positions"]
        ],
        "value_differs": [],
        "names_unread": [d[k] for k in d if d[k]["kind"] == "name" and d[k]["value_read"] is None],
        "disagreeing_votes": [
            {"site_key": k, "channel": d[k]["channel"], "line": d[k]["line"], **v}
            for k in d for v in d[k]["votes"] if not v["agrees"]
        ],
    }  # fmt: skip
    for k in e:
        if k not in d:
            continue
        if e[k]["kind"] == "structural":
            want = e[k].get("written_value")
            if want is not None and d[k]["value_read"] != want:
                out["value_differs"].append({"site_key": k, "channel": e[k]["channel"],
                                             "line": d[k]["line"], "written": want,
                                             "read": d[k]["value_read"]})  # fmt: skip
        elif e[k].get("written_name") and d[k].get("name") != e[k]["written_name"]:
            out["value_differs"].append({"site_key": k, "channel": "names",
                                         "written": e[k]["written_name"],
                                         "read": d[k].get("name")})  # fmt: skip
    count: dict[int, Counter[int]] = {i: Counter() for i in range(b)}
    for v in votes or []:
        count[v.index][v.value] += 1
    out["uncovered_positions"] = [i for i in range(b) if not count[i]]
    out["tied_positions"] = [i for i in range(b) if count[i] and count[i][0] == count[i][1]]
    out["wrong_majority_positions"] = [
        i for i in range(b)
        if count[i] and count[i][0] != count[i][1] and count[i].most_common(1)[0][0] != cw[i]
    ]  # fmt: skip
    out["tally"] = [[count[i][0], count[i][1]] for i in range(b)]
    return out


def export_case(rec: dict[str, Any], code: str, global_seed: int) -> dict[str, Any]:
    """Rifà inserimento, attacco e rilevazione del caso con i siti e le differenze."""
    from multimark.codec import encode  # type: ignore[import-not-found]
    from multimark.detect import detect  # type: ignore[import-not-found]
    from multimark.embed import embed

    method, sid, k = rec["method"], rec["sample_id"], int(rec["key_index"])
    cfg = profiles()[method]
    message, key = case_seeds(global_seed, sid, k)
    expected = "".join(map(str, message))
    cw = encode(message, cfg.t, cfg.code_matrix)
    with Tracer() as tracer:
        res = embed(code, message, key, cfg)
    attempt = tracer.attempts[-1] if tracer.attempts else {}
    if res.status != "FAILED":
        marked, kind = res.code, "final"
    elif attempt.get("code2") or attempt.get("code1"):
        marked, kind = attempt.get("code2") or attempt["code1"], "last_attempt"
    else:
        marked, kind = None, "none"
    out: dict[str, Any] = {
        "sample_id": sid, "problem_key": rec.get("problem_key"), "method": method,
        "attack": rec["attack"], "key_index": k, "reasons": reasons_of(rec),
        "expected_message": expected, "message_matches_record": expected == rec.get(
            "expected_message"),
        "embed": {"status": res.status, "reason": res.reason, "error": res.extra.get("error"),
                  "n_sites": res.extra.get("n_sites"), "n_attempts": len(tracer.attempts),
                  **{f: res.extra.get(f) for f in ("skipped_sites", "n_renamed",
                     "n_names_no_candidate", "n_names_unwritable", "n_names_wrong",
                     "n_names_unread")}},
        "original_code": code, "marked_code": marked, "marked_code_kind": kind,
    }  # fmt: skip
    out["diff"] = None if marked is None else "\n".join(difflib.unified_diff(
        code.splitlines(), marked.splitlines(), "original", "marked", lineterm=""))  # fmt: skip
    emb_sites = embedding_sites(code, cfg, key, cw, attempt)
    out["embedding_sites"] = emb_sites
    if marked is None:
        out.update(detection_sites=[], differences=None, decode=None, reproduced=None)
        return out
    # nessuna raccolta di nomi: le rinomine della Tabella 5 con la raccolta di T1.4 non si
    # riproducono qui (gli altri attacchi non la usano)
    seed = derive_seed(global_seed, rec["attack"], sid, k)
    attacked = ATTACKS[rec["attack"]](marked, cfg, key, seed, ())
    det_sites, votes = detection_sites(attacked, cfg, key, cw)
    out["detection_sites"] = det_sites
    out["differences"] = differences(emb_sites, det_sites, votes, cfg.codeword_length, cw)
    if kind == "final":
        d = detect(attacked, message, key, cfg, registry_sizes=(1,), method=method)
        out["decode"] = {"status": d.extra.get("decode_status"),
                         "decoded_message": d.decoded_message,
                         "bits_correct": d.extra.get("bits_correct"),
                         "n_erasures": d.extra.get("n_erasures"), "codeword": cw}  # fmt: skip
        same_decode = d.extra.get("decode_status") == rec.get("decode_status")
        out["reproduced"] = res.status == rec["embed_status"] and same_decode
    else:
        out["decode"] = None
        out["reproduced"] = res.status == rec["embed_status"] and res.reason == rec.get(
            "embed_reason")  # fmt: skip
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--records", required=True, type=Path, help="mcss_paper_attacks --records")
    p.add_argument("--selection", required=True, type=Path, help="selection JSONL (code)")
    p.add_argument("--out", required=True, type=Path, help="output JSONL (must not exist)")
    p.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    p.add_argument("--library", type=Path, default=DEFAULT_LIBRARY, help="multimark copy")
    p.add_argument("--global-seed", type=int, default=GLOBAL_SEED)
    args = p.parse_args(argv)
    try:
        if args.out.exists():
            raise AttackError(f"{args.out} already exists: choose a new name")
        import_library(args.library)
        codes = {r["sample_id"]: r["code"] for r in read_selection(args.selection)}
        cases = [r for r in read_records(args.records)
                 if r["method"] in args.methods and reasons_of(r)]  # fmt: skip
        missing = sorted({r["sample_id"] for r in cases} - set(codes))
        if missing:
            raise AttackError(f"{len(missing)} sample_id not in the selection: {missing[:5]}")
        rows = [export_case(r, codes[r["sample_id"]], args.global_seed) for r in cases]
        write_new(args.out, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    except AttackError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    by = Counter((r["method"], reason) for r in rows for reason in r["reasons"])
    for (method, reason), n in sorted(by.items()):
        print(f"  {method}: {reason} {n}", file=sys.stderr)
    bad = sum(r["reproduced"] is False for r in rows)
    print(f"[OK] {len(rows)} cases -> {args.out}; not reproduced: {bad}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
