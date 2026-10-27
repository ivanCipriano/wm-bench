#!/usr/bin/env bash
# Milestone 10: attacchi T1 sui campioni marcati di L1 test di un metodo CONGELATO (ADR-012,
# ADR-013), con esecuzione, rilevazione e metriche dopo l'attacco (I5):
#
#   bash scripts/submit_attacks_l1.sh attack  stone T1.4 1/1  # 1) campioni attaccati (CPU, defq)
#   bash scripts/submit_attacks_l1.sh execute stone T1.4 1/1  # 2) Pass@1 degli attaccati (defq)
#   bash scripts/submit_attacks_l1.sh detect  stone T1.4 1/1  # 3) punteggi degli attaccati
#   bash scripts/submit_attacks_l1.sh metrics stone T1.4      # 4) metriche (soglie congelate L1+L2)
#   aggiungere --dry-run in fondo per vedere solo il piano; K/M divide le celle fra le due persone.
#
# ATTACCO: un ID (T1.4) o più ID separati da virgole (T1.1,T1.2,T1.3,T1.4,T1.5); ogni attacco si
# espande sulla sua griglia (configs/attack/*.yaml): T1.1 1, T1.2 1, T1.3 2, T1.4 8, T1.5 3, T2.1 4, T2.2 2, T2.4 2
# celle per (modello, linguaggio). T1, T2.1 e T2.4 su CPU (defq); T2.2 su GPU (gpuq, Llama-3.1-8B-Instruct, D33):
# prima del lancio completo di T2.2 serve il pilota C++ (bash scripts/pilot_t2_2_cpp.sh). T2.3 è
# spento (configs/attack/t2_3_roundtrip.yaml, enabled: false).
# Prima: il metodo congelato con la fase 0 completa (python scripts/check_test.py METODO), i tool
# installati (bash scripts/setup_tools.sh) e le raccolte (stage attack_pools).
# Ordine: 1) → 2) e 3) insieme → 4) dopo 2) e 3). Stato: python scripts/check_attacks.py METODO ATTACCO.
# Controllo negativo (D34, misura aggiuntiva; punti con negative_control, oggi T2.2 full_rewrite):
#   bash scripts/submit_attacks_l1.sh control_attack  all   T2.2 1/1  # a) baseline riscritta (gpuq),
#                                                   # una volta per modello e linguaggio (tutti e 4)
#   bash scripts/submit_attacks_l1.sh control_detect  stone T2.2 1/1  # b) rilevazione della baseline
#                                                   # riscritta con il metodo congelato
#   bash scripts/submit_attacks_l1.sh control_metrics stone T2.2      # c) FPR e AUROC (dopo b e 4)
#   Stato e report: python scripts/check_attacks.py METODO T2.2 --control --report
# T3.1 ri-marcatura con k2 (ADR-014 §3): prima la soglia di k2, congelata prima del test (I3),
# poi l'attacco (generatore del metodo con k2; ACW su CPU, gli altri su gpuq) e le rilevazioni:
#   bash scripts/submit_attacks_l1.sh k2_detect_dev    stone T3.1 1/1  # a) negativi umani dev L1+L2 con k2
#   bash scripts/submit_attacks_l1.sh k2_calibrate     stone T3.1      # b) soglia di k2 (dopo a)
#   bash scripts/submit_attacks_l1.sh k2_freeze        stone T3.1      # c) congela la soglia di k2
#   bash scripts/submit_attacks_l1.sh k2_detect_test   stone T3.1 1/1  # d) negativi umani test con k2
#   bash scripts/submit_attacks_l1.sh attack           stone T3.1 1/1  # 1) ri-marcatura
#   poi execute, detect (k1) e k2_detect (attaccati con k2) dopo 1); metrics dopo execute e detect;
#   bash scripts/submit_attacks_l1.sh k2_metrics       stone T3.1      # e) categorie k1/k2 (dopo tutto)
# Catena senza aspettare a mano (AFTER = passi da attendere, stesso metodo e attacchi):
#   bash scripts/submit_attacks_l1.sh attack stone T2.1 1/1
#   AFTER=attack bash scripts/submit_attacks_l1.sh execute stone T2.1 1/1
#   AFTER=attack bash scripts/submit_attacks_l1.sh detect  stone T2.1 1/1
#   AFTER=execute,detect bash scripts/submit_attacks_l1.sh metrics stone T2.1
# Variabile opzionale: JOBS (default: tutti i posti liberi, cioè 10 meno i job già in coda o in
# esecuzione dell'utente; cluster_info §4). Nodi guasti: export WMB_EXCLUDE_NODES=gnode13,gnode14.
set -euo pipefail

USAGE="usage: submit_attacks_l1.sh attack|execute|detect|metrics|control_attack|control_detect|control_metrics|k2_detect_dev|k2_calibrate|k2_freeze|k2_detect_test|k2_detect|k2_metrics METHOD ATTACK[,ATTACK] [K/M] [--dry-run]"
STEP="${1:?$USAGE}"
METHOD="${2:?$USAGE}"
ATTACKS="${3:?$USAGE}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"  # bench-core (e i tool) anche in una shell nuova

# Posti liberi: MaxSubmit = 10 job per utente (in coda + in esecuzione), cluster_info §4.
MAX_JOBS=10
ACTIVE=$(squeue -u "$USER" -h 2>/dev/null | wc -l)
FREE=$((MAX_JOBS - ACTIVE))
DEFAULT_JOBS=$(( FREE > 0 ? FREE : 1 ))

case "$METHOD" in
    sweet) LANGS="[python,java,cpp,javascript]" ;;
    stone) LANGS="[python,java,cpp]" ;;
    mcgmark|promptmark|acw) LANGS="[python]" ;;
    all) LANGS="[python,java,cpp,javascript]" ;;  # solo control_attack: la baseline non ha metodo
    *) echo "unknown method: $METHOD" >&2; exit 2 ;;
esac
if [ "$METHOD" = all ] && [ "$STEP" != control_attack ]; then
    echo "METHOD all is only for control_attack" >&2; exit 2
fi
TEST=("levels=[L1]" "splits=[test]" "languages=$LANGS" "attacks=[$ATTACKS]")
[ "$METHOD" = all ] || TEST=("methods=[$METHOD]" "${TEST[@]}")
# Seconda chiave senza attacco (T3.1): negativi umani, configurazione congelata del metodo.
K2=("methods=[$METHOD]" "languages=$LANGS" "attacks=[]")
case "$STEP" in
    k2_*) [ "$ATTACKS" = T3.1 ] || { echo "the k2 steps are only for T3.1" >&2; exit 2; } ;;
esac

case "$STEP" in
    attack|detect)
        SHARE="${4:?$USAGE}"; MODE="${5:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=$STEP" "${TEST[@]}")
        ;;
    execute)
        SHARE="${4:?$USAGE}"; MODE="${5:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=execute"
              "execution.sources=[attacked]" "${TEST[@]}")
        ;;
    metrics)
        SHARE="1/1"; MODE="${4:-}"
        ARGS=(--jobs 1 "stage=metrics" "detection.threshold_levels=[L1,L2]" "${TEST[@]}")
        ;;
    control_attack)
        SHARE="${4:?$USAGE}"; MODE="${5:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=attack_baseline" "${TEST[@]}")
        ;;
    control_detect)
        SHARE="${4:?$USAGE}"; MODE="${5:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=detect_attack_control"
              "${TEST[@]}")
        ;;
    control_metrics)
        SHARE="1/1"; MODE="${4:-}"
        ARGS=(--jobs 1 "stage=metrics_attack_control" "detection.threshold_levels=[L1,L2]"
              "${TEST[@]}")
        ;;
    k2_detect_dev)
        SHARE="${4:?$USAGE}"; MODE="${5:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=detect_k2"
              "levels=[L1,L2]" "splits=[dev]" "${K2[@]}")
        ;;
    k2_calibrate)
        SHARE="1/1"; MODE="${4:-}"
        ARGS=(--jobs 1 "stage=calibrate_k2" "levels=[L1,L2]" "${K2[@]}")
        ;;
    k2_freeze)
        SHARE="1/1"; MODE="${4:-}"
        ARGS=(--jobs 1 "stage=freeze_k2" "levels=[L1,L2]" "${K2[@]}")
        ;;
    k2_detect_test)
        SHARE="${4:?$USAGE}"; MODE="${5:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=detect_k2"
              "levels=[L1]" "splits=[test]" "${K2[@]}")
        ;;
    k2_detect)
        SHARE="${4:?$USAGE}"; MODE="${5:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=detect_k2" "${TEST[@]}")
        ;;
    k2_metrics)
        SHARE="1/1"; MODE="${4:-}"
        ARGS=(--jobs 1 "stage=metrics_remark" "detection.threshold_levels=[L1,L2]" "${TEST[@]}")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

# AFTER (opzionale): passi da attendere, es. AFTER=attack o AFTER=execute,detect. I job partono
# quando sono finiti i job ancora in coda o in esecuzione di quei passi (stesso metodo e attacchi,
# ID salvati a ogni invio). I job in attesa contano nei 10 posti di MaxSubmit.
IDS_DIR="$WMB/logs/submit_ids"
mkdir -p "$IDS_DIR"
ids_file() { echo "${1}_${METHOD}_${ATTACKS//,/-}.txt"; }
AFTER_ARGS=()
if [ -n "${AFTER:-}" ]; then
    QUEUED=" $(squeue -u "$USER" -h -o %i | tr '\n' ' ') "
    WAIT=()
    for step in ${AFTER//,/ }; do
        [ -f "$IDS_DIR/$(ids_file "$step")" ] || { echo "AFTER: no saved jobs for $step" >&2; exit 2; }
        for id in $(cat "$IDS_DIR/$(ids_file "$step")"); do
            case "$QUEUED" in *" $id "*) WAIT+=("$id") ;; esac
        done
    done
    if [ "${#WAIT[@]}" -gt 0 ]; then
        AFTER_ARGS=(--after "$(IFS=:; echo "${WAIT[*]}")")
        echo "##### parte dopo i job ${WAIT[*]} ($AFTER)"
    else
        echo "##### AFTER=$AFTER: nessun job ancora in coda, parte subito"
    fi
fi
echo "##### job attivi: $ACTIVE, posti liberi: $FREE su $MAX_JOBS"
if [ "$MODE" != "--dry-run" ]; then
    NEED=1
    case "$STEP" in metrics|control_metrics|k2_calibrate|k2_freeze|k2_metrics) ;;
        *) NEED="${JOBS:-1}" ;; esac
    if [ "$FREE" -lt 1 ] || [ "$NEED" -gt "$FREE" ]; then
        echo "not enough free slots: need $NEED, free $FREE (wait for a job to end or lower JOBS)" >&2
        exit 2
    fi
fi
echo "##### piano (dry run): L1 test $STEP $METHOD $ATTACKS, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: L1 test $STEP $METHOD $ATTACKS, quota $SHARE"
OUT=$(bench submit "${AFTER_ARGS[@]}" "${ARGS[@]}")
echo "$OUT" | grep -v "parameters:"
# ID dei job di questo passo, per AFTER di un passo successivo
echo "$OUT" | sed -n 's/^SLURM job \([0-9][0-9]*\) .*/\1/p' > "$IDS_DIR/$(ids_file "$STEP")"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.50j %.8T %.10M"
