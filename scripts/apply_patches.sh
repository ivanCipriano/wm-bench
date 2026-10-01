#!/usr/bin/env bash
# Crea le copie patchate dei metodi in build/patched/<metodo>/ (cluster_info §1, ADR-002).
#
# Per ogni metodo:
#   1. verifica che il submodule sia inizializzato e senza modifiche;
#   2. ricrea build/patched/<metodo>/ esportando l'albero del submodule (checkout-index);
#   3. applica in ordine patches/<metodo>/NNNN-*.patch con `git apply`;
#   4. scrive build/patched/<metodo>.source_commit e build/patched/<metodo>.patches.sha256
#      (usati dai manifest).
# I submodule in third_party/ non vengono mai toccati. Le patch di patches/_deps/ sono
# solo documentazione (gli ambienti sono già installati) e NON vengono applicate.
#
# Uso:
#   bash scripts/apply_patches.sh                 # tutti e sei i metodi
#   bash scripts/apply_patches.sh stone mcgmark   # solo alcuni
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ALL_METHODS=(sweet acw stone acrostic promptmark mcgmark)

submodule_of() {
    case "$1" in
        sweet)      echo third_party/sweet-watermark ;;
        acw)        echo third_party/ACW ;;
        stone)      echo third_party/STONE-watermarking ;;
        acrostic)   echo third_party/code_acrostic ;;
        promptmark) echo third_party/PromptMark ;;
        mcgmark)    echo third_party/MCGMT ;;
        *)          return 1 ;;
    esac
}

if [ "$#" -gt 0 ]; then
    methods=("$@")
else
    methods=("${ALL_METHODS[@]}")
fi

OUT_ROOT="build/patched"
mkdir -p "$OUT_ROOT"

current_dest=""
cleanup_on_error() {
    # Una copia patchata a metà non deve mai restare su disco.
    if [ -n "$current_dest" ] && [ -d "$REPO_ROOT/$current_dest" ]; then
        rm -rf -- "${REPO_ROOT:?}/$current_dest"
        echo "[apply_patches] removed incomplete $current_dest" >&2
    fi
}
trap cleanup_on_error ERR

for method in "${methods[@]}"; do
    if ! sub="$(submodule_of "$method")"; then
        echo "[ERROR] unknown method '$method' (known: ${ALL_METHODS[*]})" >&2
        exit 2
    fi
    if [ ! -e "$sub/.git" ]; then
        echo "[ERROR] $sub not initialised: run scripts/setup_submodules.sh" >&2
        exit 1
    fi
    if [ -n "$(git -C "$sub" status --porcelain --untracked-files=no)" ]; then
        echo "[ERROR] $sub has local changes: third_party/ must stay untouched" >&2
        exit 1
    fi

    dest="$OUT_ROOT/$method"
    rm -rf -- "${REPO_ROOT:?}/${OUT_ROOT:?}/${method:?}" \
        "$OUT_ROOT/$method.source_commit" "$OUT_ROOT/$method.patches.sha256"
    current_dest="$dest"
    mkdir -p "$dest"
    git -C "$sub" checkout-index -a -f --prefix="$REPO_ROOT/$dest/"

    mapfile -t patch_files < <(
        find "patches/$method" -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9]-*.patch' 2>/dev/null \
            | LC_ALL=C sort
    )
    if [ "${#patch_files[@]}" -eq 0 ]; then
        echo "[WARN] $method: no patches in patches/$method/" >&2
    fi
    for patch in "${patch_files[@]}"; do
        # --directory: i percorsi della patch sono relativi alla radice del submodule.
        # Si lancia dalla radice del repository perché `git apply` eseguito in una
        # sottocartella di un repository ignorerebbe in silenzio i file fuori da essa.
        git apply --check --directory="$dest" "$patch"
        git apply --directory="$dest" "$patch"
        echo "[apply_patches] $method: applied $(basename "$patch")"
    done

    git -C "$sub" rev-parse HEAD > "$OUT_ROOT/$method.source_commit"
    if [ "${#patch_files[@]}" -gt 0 ]; then
        sha256sum "${patch_files[@]}" > "$OUT_ROOT/$method.patches.sha256"
    else
        : > "$OUT_ROOT/$method.patches.sha256"
    fi
    current_dest=""
    echo "[OK] $method -> $dest ($(cat "$OUT_ROOT/$method.source_commit"), ${#patch_files[@]} patch(es))"
done
