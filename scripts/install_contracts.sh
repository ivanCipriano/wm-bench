#!/usr/bin/env bash
# Installa bench-contracts (editable, senza dipendenze) negli ambienti elencati in
# configs/envs/envs.yaml e verifica l'import (SPEC §5.1, §20.3).
# È l'UNICA modifica consentita agli ambienti dei metodi.
#
# Uso:
#   bash scripts/install_contracts.sh                 # tutti e 6 gli ambienti
#   bash scripts/install_contracts.sh stone sweet     # solo alcuni
#
# Richiede accesso in uscita a PyPI (pip scarica setuptools per la build isolata):
# lanciarlo dal nodo di login.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVS_FILE="$REPO_ROOT/configs/envs/envs.yaml"
PKG="$REPO_ROOT/packages/bench-contracts"

# Righe della forma "  <nome>: {python: <percorso>}" (unica fonte dei percorsi).
mapfile -t entries < <(
    sed -n 's/^  \([A-Za-z0-9_-]*\): *{python: *\([^}]*\)}.*$/\1 \2/p' "$ENVS_FILE"
)
if [ "${#entries[@]}" -eq 0 ]; then
    echo "[ERROR] no environments parsed from $ENVS_FILE" >&2
    exit 1
fi

selected=" $* "
errors=0
for entry in "${entries[@]}"; do
    name="${entry%% *}"
    python="${entry#* }"
    python="${python%"${python##*[![:space:]]}"}"
    if [ "$#" -gt 0 ] && [[ "$selected" != *" $name "* ]]; then
        continue
    fi
    echo "=== $name ($python)"
    if [ ! -x "$python" ]; then
        echo "[ERROR] $name: interpreter not found" >&2
        errors=$((errors + 1))
        continue
    fi
    "$python" -m pip --version
    if ! "$python" -m pip install --no-deps -e "$PKG"; then
        echo "[ERROR] $name: pip install failed" >&2
        errors=$((errors + 1))
        continue
    fi
    # Import da / e senza PYTHONPATH: si verifica l'installazione, non la cartella corrente.
    if ! (cd / && env -u PYTHONPATH "$python" -c \
        'import sys, bench_contracts as b; print("[OK]", sys.version.split()[0], b.__version__, b.__file__)'); then
        echo "[ERROR] $name: import bench_contracts failed" >&2
        errors=$((errors + 1))
    fi
done

if [ "$errors" -ne 0 ]; then
    echo "[install_contracts] $errors error(s)" >&2
    exit 1
fi
echo "[install_contracts] done"
