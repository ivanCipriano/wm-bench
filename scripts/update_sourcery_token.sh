#!/usr/bin/env bash
# Aggiorna il token di Sourcery (ACW) quando scade una prova (audit di ACW §6, decisione dell'utente
# del 23 ottobre 2026). Dal nodo di login, nella cartella del repo:
#
#   bash scripts/update_sourcery_token.sh   # chiede il token senza mostrarlo e prova il login
#   source ~/.bashrc                        # SOURCERY_TOKEN nuovo anche nella shell corrente
#
# Il token va solo in ~/.config/wmb/sourcery_token (permessi 600), mai nel repository. I job già in coda
# hanno il token vecchio: dopo l'aggiornamento si rilancia lo stesso comando submit (la ripresa rifà
# solo gli item mancanti).
set -euo pipefail
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
FILE="$HOME/.config/wmb/sourcery_token"
SOURCERY="$WMB/miniforge3/envs/acw/bin/sourcery"
read -rs -p "Nuovo token Sourcery: " TOKEN
echo
if [ -z "$TOKEN" ]; then
    echo "empty token: nothing changed" >&2
    exit 1
fi
mkdir -p "$(dirname "$FILE")"
if [ -f "$FILE" ]; then
    cp -p "$FILE" "$FILE.old"  # token precedente, per tornare indietro
fi
( umask 077; printf '%s\n' "$TOKEN" > "$FILE" )
chmod 600 "$FILE"
echo "token saved in $FILE"
if "$SOURCERY" login --token "$TOKEN" > /dev/null 2>&1; then
    echo "sourcery login: OK"
else
    echo "sourcery login: FAILED (token not valid?); the previous token is in $FILE.old" >&2
    exit 1
fi
unset TOKEN
grep -q "sourcery_token" "$HOME/.bashrc" || echo "WARNING: ~/.bashrc does not export SOURCERY_TOKEN from $FILE" >&2
echo "now run: source ~/.bashrc"
