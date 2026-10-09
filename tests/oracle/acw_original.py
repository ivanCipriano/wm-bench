"""Percorso diretto di ACW per l'oracle (SPEC §21.4). Ambiente ``acw`` (Python 3.10, solo CPU).

    PYTHONPATH=build/patched/acw/source <python di acw> tests/oracle/acw_original.py \\
        --inputs tests/fixtures/oracle/acw/inputs.json --out tests/fixtures/oracle/acw/original.json

Usa ``refactor.py`` senza lo shim, come ``WatermarkExperimentRunner.run_single_folder``:
1. **inserimento**: ``WatermarkInjector(num_transforms, seed=chiave, random_rules=True).apply``
   sulla cartella dei codici della baseline (un file per codice);
2. **rilevazione nativa**: riapplicazione congiunta a una copia e confronto per hash (codice
   marcato e soluzioni canoniche, cioè codice umano);
3. **riapplicazione per regola** (punteggio del framework, audit §3) sugli stessi codici;
4. **ordine e chiave** (richiesta dell'utente): stesse regole con il seme della seconda chiave;
   con tutte le 43 regole cambia solo l'ordine delle regole proprie (Sourcery le applica in una
   chiamata);
5. **prova di Sourcery**: chiamate consecutive cronometrate, con codice di uscita e messaggi (il
   codice del metodo li scarta), per cercare limiti di frequenza o di volume.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List

SOURCERY = range(1, 36)
PROBE_CALLS = 30


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:  # noqa: PLR0915 - script lineare dell'oracle
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    ns = parser.parse_args()
    inputs_path, out_path = os.path.abspath(ns.inputs), os.path.abspath(ns.out)
    with open(inputs_path, encoding="utf-8") as handle:
        data = json.load(handle)
    hp = data["native_hparams"]
    work = tempfile.mkdtemp(prefix="acw_oracle_")
    os.chdir(work)
    with contextlib.redirect_stdout(io.StringIO()):
        import refactor

    def injector(seed: int) -> Any:
        return refactor.WatermarkInjector(
            num_transforms=int(hp["num_transforms"]), seed=seed, random_rules=True, batch=5
        )

    timings: List[float] = []

    def apply(inj: Any, folder: str, rules: List[int]) -> None:
        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            inj.apply_specific_rules(folder, rules)
        if any(r in SOURCERY for r in rules):
            timings.append(time.perf_counter() - start)

    def write(codes: List[str]) -> str:
        folder = tempfile.mkdtemp(prefix="codes_", dir=work)
        for i, code in enumerate(codes):
            with open(os.path.join(folder, f"{i:05d}.py"), "w", encoding="utf-8", newline="") as h:
                h.write(code)
        return folder

    def read(folder: str, n: int) -> List[str]:
        out = []
        for i in range(n):
            with open(os.path.join(folder, f"{i:05d}.py"), encoding="utf-8", newline="") as h:
                out.append(h.read())
        return out

    def copy(folder: str) -> str:
        target = tempfile.mkdtemp(prefix="copy_", dir=work)
        shutil.rmtree(target)
        shutil.copytree(folder, target)
        return target

    def changed(a: str, b: str, n: int) -> List[bool]:
        return [
            file_hash(os.path.join(a, f"{i:05d}.py")) != file_hash(os.path.join(b, f"{i:05d}.py"))
            for i in range(n)
        ]

    inj = injector(int(data["key"]))
    rules = list(inj.selected_rules)
    baseline = [c for c in data["codes"] if c["id"].startswith("baseline:")]
    human = [c for c in data["codes"] if c["id"].startswith("canonical:")]

    # 1. inserimento (come apply del metodo)
    base_dir = write([c["code"] for c in baseline])
    marked_dir = copy(base_dir)
    apply(inj, marked_dir, rules)
    marked = read(marked_dir, len(baseline))
    embed_changed = changed(base_dir, marked_dir, len(baseline))

    # 2-3. rilevazione nativa e per regola
    def detect(codes: List[str]) -> List[Dict[str, Any]]:
        folder = write(codes)
        joint = copy(folder)
        apply(inj, joint, rules)
        joint_changed = changed(folder, joint, len(codes))
        per_rule: Dict[int, List[bool]] = {}
        for rule in rules:
            c = copy(folder)
            apply(inj, c, [rule])
            per_rule[rule] = changed(folder, c, len(codes))
        return [
            {
                "joint_unchanged": not joint_changed[i],
                "rule_unchanged": {str(r): not per_rule[r][i] for r in rules},
            }
            for i in range(len(codes))
        ]

    detections = []
    sets = [
        ("marked", [f"marked:{c['id'].split(':', 1)[1]}" for c in baseline], marked),
        ("baseline", [c["id"] for c in baseline], [c["code"] for c in baseline]),
        ("human", [c["id"] for c in human], [c["code"] for c in human]),
    ]
    for kind, ids, codes in sets:
        for item_id, code, res in zip(ids, codes, detect(codes)):
            detections.append({"id": item_id, "kind": kind, "code": code, **res})

    # 4. ordine e chiave
    inj2 = injector(int(data["key_k2"]))
    rules2 = list(inj2.selected_rules)
    other_dir = copy(base_dir)
    apply(inj2, other_dir, rules2)
    other = read(other_dir, len(baseline))

    # 5. prova di Sourcery: chiamate consecutive su una cartella di 10 file
    probe_dir = write([c["code"] for c in (baseline + human)][:10])
    config = os.path.join(work, "probe.yaml")
    with open(config, "w", encoding="utf-8") as h:
        h.write("version: '1'\nrule_settings:\n  enable:\n  - remove-unnecessary-else\n")
    probe = []
    for _ in range(PROBE_CALLS):
        start = time.perf_counter()
        proc = subprocess.run(
            ["sourcery", "review", "--config", config, "--fix", copy(probe_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        probe.append(
            {
                "seconds": round(time.perf_counter() - start, 3),
                "returncode": proc.returncode,
                "output_tail": (proc.stdout + proc.stderr)[-300:],
            }
        )
    version = subprocess.run(
        ["sourcery", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()

    out = {
        "variant": "original",
        "sourcery_version": version,
        "rules": rules,
        "rules_k2": rules2,
        "embed": [
            {"id": c["id"], "code": c["code"], "marked": m, "changed": ch}
            for c, m, ch in zip(baseline, marked, embed_changed)
        ],
        "order_check": {
            "same_rule_set": sorted(rules) == sorted(rules2),
            "same_order": rules == rules2,
            "differing_outputs": sum(a != b for a, b in zip(marked, other)),
            "n": len(marked),
        },
        "detections": detections,
        "sourcery_timings": [round(t, 3) for t in timings],
        "probe": probe,
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    sys.stdout.write(
        f"wrote {out_path}: {len(baseline)} embeddings, {len(detections)} detections, "
        f"sourcery {version}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
