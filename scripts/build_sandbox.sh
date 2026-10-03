#!/usr/bin/env bash
# Build dell'immagine della sandbox (ADR-001, cluster_info §5). Da lanciare UNA volta sul nodo
# di login (serve internet per scaricare l'immagine base e i pacchetti; --fakeroot funziona lì).
#
#   bash scripts/build_sandbox.sh
#
# Produce in $WMB/containers/:
#   sandbox.sif          immagine (il suo SHA-256 va in ogni ExecutionRecord)
#   sandbox.sif.sha256   hash del .sif
#   sandbox_dir/         stessa immagine in formato directory, usata per eseguire i test
#                        (senza squashfuse ogni exec su un .sif lo riconvertirebbe)
# Una ricostruzione sostituisce la directory solo a build riuscita.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
OUT="$WMB/containers"
cd "$REPO_ROOT"
umask 002

module load apptainer/apptainer.module
apptainer --version

# Cache e file temporanei di Apptainer fuori dalla home (quota).
export APPTAINER_CACHEDIR="$OUT/cache"
export APPTAINER_TMPDIR="$OUT/tmp"
mkdir -p "$OUT" "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

echo "##### build del .sif (alcuni minuti)"
apptainer build --fakeroot --force "$OUT/sandbox.sif.new" containers/sandbox.def
mv -f "$OUT/sandbox.sif.new" "$OUT/sandbox.sif"
sha256sum "$OUT/sandbox.sif" | awk '{print $1}' > "$OUT/sandbox.sif.sha256"
echo "sha256: $(cat "$OUT/sandbox.sif.sha256")"

echo "##### conversione in directory"
rm -rf "$OUT/sandbox_dir.new"
apptainer build --sandbox "$OUT/sandbox_dir.new" "$OUT/sandbox.sif"
rm -rf "$OUT/sandbox_dir.old"
if [ -d "$OUT/sandbox_dir" ]; then mv "$OUT/sandbox_dir" "$OUT/sandbox_dir.old"; fi
mv "$OUT/sandbox_dir.new" "$OUT/sandbox_dir"
rm -rf "$OUT/sandbox_dir.old" "$APPTAINER_TMPDIR"
# Leggibile ed eseguibile anche dall'altro utente del gruppo.
chmod -R g+rX "$OUT/sandbox_dir" "$OUT/sandbox.sif" "$OUT/sandbox.sif.sha256"

echo "##### versioni nell'immagine"
apptainer exec --containall --cleanenv --no-home "$OUT/sandbox_dir" cat /opt/wmb/versions.json
du -sh "$OUT/sandbox.sif" "$OUT/sandbox_dir"
