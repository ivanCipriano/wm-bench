#!/usr/bin/env bash
# Inizializza i submodule di third_party/ e verifica che ciascuno sia esattamente al
# commit fissato (cluster_info §9, §9.2; ADR-002) e senza modifiche locali.
#
# Uso (dalla radice del repository o da qualunque cartella):
#   bash scripts/setup_submodules.sh
#
# Esce con codice != 0 se un submodule manca, è a un commit diverso o è sporco.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# percorso  commit atteso
EXPECTED="
third_party/sweet-watermark            853b47eb064c180beebd383302d09491fc98a565
third_party/ACW                        2236dc304478a31fe7cc7cc393527375024428db
third_party/STONE-watermarking         bb5d809c0c494a219411e861f2313cca2b9fd7b4
third_party/code_acrostic              83823697f01d6250e7fccd9fa7005bdcfb967ded
third_party/PromptMark                 c04c8f61db1f0ec7ba2f213f4aa1a15787af484e
third_party/MCGMT                      eefa27b68747f5027c3121abcc506f3488eae990
third_party/bigcode-evaluation-harness 8fc5bae6479c4fbbb28c3f8b644f6a15b3f3b5bd
third_party/ClassEval                  eaeac44d0d5dcd8a95feec50726d66fedc73a98f
third_party/lm-watermarking            82922516930c02f8aa322765defdb5863d07a00e
third_party/MarkLLM                    e43009f3d197f8d10e865e2ff731ba1006d1c7d1
"

echo "[setup_submodules] sync + update --init"
git submodule sync --quiet
git submodule update --init

errors=0
while read -r path commit; do
    [ -z "${path:-}" ] && continue
    pinned="$(git ls-files --stage -- "$path" | awk '$1 == "160000" {print $2}')"
    if [ "$pinned" != "$commit" ]; then
        echo "[ERROR] $path: gitlink in the index is '$pinned', expected $commit" >&2
        errors=$((errors + 1))
        continue
    fi
    if [ ! -e "$path/.git" ]; then
        echo "[ERROR] $path: not initialised" >&2
        errors=$((errors + 1))
        continue
    fi
    actual="$(git -C "$path" rev-parse HEAD)"
    if [ "$actual" != "$commit" ]; then
        echo "[ERROR] $path: checked out $actual, expected $commit" >&2
        errors=$((errors + 1))
        continue
    fi
    if [ -n "$(git -C "$path" status --porcelain)" ]; then
        echo "[ERROR] $path: working tree has local changes (third_party/ must stay untouched)" >&2
        errors=$((errors + 1))
        continue
    fi
    echo "[OK] $path @ $commit"
done <<< "$EXPECTED"

if [ "$errors" -ne 0 ]; then
    echo "[setup_submodules] $errors error(s)" >&2
    exit 1
fi
echo "[setup_submodules] all submodules pinned and clean"
