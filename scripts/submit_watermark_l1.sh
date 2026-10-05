#!/usr/bin/env bash
# Invia la fase watermark di L1 (dev) per un metodo, con la configurazione di default (gpuq).
#
#   bash scripts/submit_watermark_l1.sh stone 1/1            # tutte le celle (2 modelli x 4 linguaggi)
#   bash scripts/submit_watermark_l1.sh stone 1/2            # metà a testa (l'altra persona: 2/2)
#   bash scripts/submit_watermark_l1.sh stone 1/1 --dry-run  # solo il piano
# Variabile opzionale: JOBS (default 2). I linguaggi non supportati (JavaScript per STONE) danno
# righe NOT_APPLICABLE in pochi secondi.
set -euo pipefail

METHOD="${1:?usage: submit_watermark_l1.sh METHOD K/M [--dry-run]}"
SHARE="${2:?usage: submit_watermark_l1.sh METHOD K/M [--dry-run]}"
MODE="${3:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002

ARGS=(--jobs "${JOBS:-2}" --share "$SHARE" "stage=watermark" "methods=[$METHOD]" "levels=[L1]" "splits=[dev]")
echo "##### piano (dry run): watermark $METHOD, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: watermark $METHOD, quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.34j %.8T %.10M"
