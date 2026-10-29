#!/usr/bin/env bash
# Build dell'immagine della sandbox (ADR-001, cluster_info §5). Da lanciare UNA volta sul nodo
# di login (serve internet per scaricare l'immagine base e i pacchetti; --fakeroot funziona lì).
#
#   bash scripts/build_sandbox.sh        # immagine di L1/L2 (containers/sandbox.def)
#   bash scripts/build_sandbox.sh l3     # immagine di L3 (containers/sandbox_l3.def, ADR-016)
#
# Produce in $WMB/containers/ (con "l3" il nome è sandbox_l3 al posto di sandbox):
#   sandbox.sif          immagine (il suo SHA-256 va in ogni ExecutionRecord)
#   sandbox.sif.sha256   hash del .sif
#   sandbox_dir/         stessa immagine in formato directory, usata per eseguire i test
#                        (senza squashfuse ogni exec su un .sif lo riconvertirebbe)
# Una ricostruzione sostituisce la directory solo a build riuscita.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
OUT="$WMB/containers"
case "${1:-}" in
    "") NAME=sandbox ;;
    l3) NAME=sandbox_l3 ;;
    *) echo "usage: bash scripts/build_sandbox.sh [l3]" >&2; exit 2 ;;
esac
cd "$REPO_ROOT"
umask 002

module load apptainer/apptainer.module
apptainer --version

# Cache e file temporanei di Apptainer fuori dalla home (quota).
export APPTAINER_CACHEDIR="$OUT/cache"
export APPTAINER_TMPDIR="$OUT/tmp"
mkdir -p "$OUT" "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

echo "##### build del .sif (alcuni minuti)"
apptainer build --fakeroot --force "$OUT/$NAME.sif.new" containers/$NAME.def
mv -f "$OUT/$NAME.sif.new" "$OUT/$NAME.sif"
sha256sum "$OUT/$NAME.sif" | awk '{print $1}' > "$OUT/$NAME.sif.sha256"
echo "sha256: $(cat "$OUT/$NAME.sif.sha256")"

echo "##### conversione in directory"
rm -rf "$OUT/${NAME}_dir.new"
apptainer build --sandbox "$OUT/${NAME}_dir.new" "$OUT/$NAME.sif"
rm -rf "$OUT/${NAME}_dir.old"
if [ -d "$OUT/${NAME}_dir" ]; then mv "$OUT/${NAME}_dir" "$OUT/${NAME}_dir.old"; fi
mv "$OUT/${NAME}_dir.new" "$OUT/${NAME}_dir"
rm -rf "$OUT/${NAME}_dir.old" "$APPTAINER_TMPDIR"
# Leggibile ed eseguibile anche dall'altro utente del gruppo.
chmod -R g+rX "$OUT/${NAME}_dir" "$OUT/$NAME.sif" "$OUT/$NAME.sif.sha256"

echo "##### versioni nell'immagine"
apptainer exec --containall --cleanenv --no-home "$OUT/${NAME}_dir" cat /opt/wmb/versions.json
du -sh "$OUT/$NAME.sif" "$OUT/${NAME}_dir"
