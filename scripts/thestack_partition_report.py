"""Ripartizione dei repository C++ di The Stack fra gli usi e raccolte degli attacchi (M10).

Uso (con bench-core, nodo di login, dopo la fase ``attack_pools``):
    python scripts/thestack_partition_report.py [--artifacts DIR]

Dal manifest di ``data/attack_pools/cpp.json`` stampa, per ogni bucket della partizione fissa per
repository (``negatives.thestack_partition``, cluster_info §8.5): frazione, repository, file e
funzioni estratte, con il fabbisogno di ogni uso. Poi, per i quattro linguaggi, le voci rimaste
dopo ogni filtro delle raccolte di commenti e nomi (ADR-012), così che si possano confrontare.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
LANGUAGES = ("python", "java", "cpp", "javascript")
# Fabbisogno in funzioni per bucket (SPEC §10.2, §10.4; configs/negatives/default.yaml).
NEEDS = {
    "l3_test": ("prompt e negativi del Livello 3", 500 + 10_000),
    "l1_test": ("integrazione dei negativi di test L1", 928 - 140),
    "dev": ("integrazione dei negativi di sviluppo L1 e L1+L2", 2000 - 24),
    "attack_pools": ("raccolte di commenti e nomi degli attacchi", None),
}
# Voci minime per lista perché la raccolta C++ sia comparabile con le altre (ordine di 10^4).
MIN_POOL = 5_000


def _manifest(root: Path, language: str) -> dict:  # type: ignore[type-arg]
    path = root / "data" / "attack_pools" / f"{language}.json.manifest.json"
    if not path.is_file():
        sys.stdout.write(f"missing {path}: run stage attack_pools first\n")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    root = Path(parser.parse_args().artifacts)
    status = 0
    cpp = _manifest(root, "cpp").get("extra", {})
    partition = cpp.get("thestack_partition", {})
    fractions = cpp.get("partition_fractions", {})
    sys.stdout.write("##### The Stack C++: ripartizione per repository (disgiunta fra usi)\n")
    sys.stdout.write(
        f"{'bucket':<14}{'quota':>7}{'repo':>9}{'file':>9}{'funzioni':>11}  uso (fabbisogno)\n"
    )
    for bucket, info in partition.items():
        use, need = NEEDS.get(bucket, ("?", None))
        flag = ""
        if need is not None:
            flag = "OK" if info["functions"] >= need else "INSUFFICIENTE"
            if flag != "OK":
                status = 1
        sys.stdout.write(
            f"{bucket:<14}{fractions.get(bucket, 0):>7.2f}{info['repos']:>9}{info['files']:>9}"
            f"{info['functions']:>11}  {use} ({need if need is not None else '-'}) {flag}\n"
        )
    sys.stdout.write("\n##### raccolte degli attacchi: voci rimaste dopo ogni filtro\n")
    for language in LANGUAGES:
        extra = _manifest(root, language).get("extra", {})
        stats = extra.get("stats")
        if not stats:
            continue
        sys.stdout.write(f"\n--- {language}: {json.dumps(stats['functions'])}\n")
        for kind in ("comments", "names"):
            table = stats[kind]
            steps = " -> ".join(f"{k} {v}" for k, v in table["left_after"].items())
            sys.stdout.write(
                f"{kind:<9} raw {table['raw']} -> {steps}; disponibili {table['available']}, "
                f"tenuti {table['kept']}\n"
            )
            if language == "cpp" and table["available"] < MIN_POOL:
                sys.stdout.write(f"  C++ {kind}: meno di {MIN_POOL} voci, raccolta insufficiente\n")
                status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
