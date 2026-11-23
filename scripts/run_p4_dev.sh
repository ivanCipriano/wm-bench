#!/usr/bin/env bash
# Ultimo giro su sviluppo prima del congelamento (P4+P5 ridotte, pipeline di multimark v1.3.11 e
# v1.3.14, decisioni dell'utente del 23 novembre 2026). P3 è chiusa: il proponente resta static.
# Libreria MultiMark b2b6c18. Punti (scripts/posthoc_config_hashes.py):
#
#   mcss (default del metodo)          1d3087c73439
#   B   (prudente, da congelare)       e52b040770df
#   D   (alta capacità, da congelare)  3fed1f1c1e24
#   ablazioni rispetto a B (§6.3): V0 ebeb5a0753cd, V1 40e5e9455cea, positional 271087ba99b6,
#   mask off 16de27361bad, canonical paper 65eb9730301a
#
# multimark è lo sweep della griglia configs/hpo/multimark.yaml (methods=[multimark],
# hpo.grid=multimark: i 7 punti, nessuna cella di default); mcss è il suo default e si invia a
# parte (methods=[mcss], senza hpo.grid). Solo split=dev, Python, L1 e L2, 2 modelli. Tutto su
# CPU. Si lancia dal nodo di login, una fase alla volta (le fasi con srun dentro tmux):
#
#   bash scripts/run_p4_dev.sh function_pools          # 1) raccolta di funzioni per T2.5
#   bash scripts/run_p4_dev.sh canonical               # 2) canoniche L1 dopo T2.5 (job Apptainer)
#   bash scripts/run_p4_dev.sh table5                  # 3) Tabella 5 di MCSS (srun, tmux)
#   bash scripts/run_p4_dev.sh watermark               # 4) inserimento dei 7 + 1 punti
#   bash scripts/run_p4_dev.sh identity                # 5) mcss, B e D identici alle celle misurate
#   bash scripts/run_p4_dev.sh execute_detect          # 6) test dei marcati, rilevazione
#   bash scripts/run_p4_dev.sh calibrate               # 7) soglie L1+L2 (hash nuovi)
#   bash scripts/run_p4_dev.sh metrics                 # 8) metriche senza attacco
#   bash scripts/run_p4_dev.sh attack                  # 9) attacchi (24 punti)
#   bash scripts/run_p4_dev.sh imperceptibility        # 10) solo le 5 ablazioni
#   bash scripts/run_p4_dev.sh attack_execute_detect   # 11) test e rilevazione degli attaccati
#   bash scripts/run_p4_dev.sh attack_metrics          # 12) metriche degli attaccati
#   bash scripts/run_p4_dev.sh imperceptibility_metrics  # 13) metrics_imperceptibility
#   bash scripts/run_p4_dev.sh report                  # 14) i cinque log P4_*.txt
#
# Le fasi 1 e 3 possono girare insieme; la 2 aspetta la 1. Le fasi 9 e 10 possono girare insieme,
# come le 12 e 13. Attacchi: insieme S di v1.3.3 (T1.4 natural r=1,0; T1.4 random r=0,5; T1.5 k=5;
# T2.1 b=all; T2.4 p=0,5; T4.1 rename=true), T1.6 (caps=convert, caps=keep) e T2.5 ((1, before),
# (2, around), (4, around)); si lanciano le griglie intere di T1.4, T1.5, T2.1, T2.4, T4.1, T1.6 e
# T2.5 (24 punti) e i report usano solo i punti di S, T1.6 e T2.5. Impercettibilità (perplexity,
# adversarial_logreg, adversarial_unixcoder) solo per le 5 ablazioni (hpo.points): mcss, B e D
# hanno le stesse opzioni di ae63f16de28f, a8a296957377 e b4cd11f13a07, già misurate, e la fase 5
# controlla che il loro inserimento sia identico byte per byte (P4_embed_identity_dev.txt). Soglie
# calibrate su dev per gli hash nuovi, più la soglia teorica.
#
# Prima di ogni invio lo script controlla che i job in coda più quelli da inviare non superino
# MAX_JOBS (10). Ogni fase controlla che la precedente sia finita (scripts/check_stage_done.py) e
# che nessun job di quella fase sia in coda. Nessuna fase scrive su un'uscita esistente; un log già
# presente si salta.
#
# Variabili: JOBS (job per invio di multimark, default 3), JOBS_ATTACK (fasi 9, 11, 12 per
# multimark; default 3), JOBS_IMP (fasi 10 e 13, per ognuna delle tre fasi; default 2), MAX_JOBS.
# mcss ha sempre un job per invio (4 celle).
set -euo pipefail

PHASE="${1:?usage: run_p4_dev.sh function_pools|canonical|table5|watermark|identity|execute_detect|calibrate|metrics|attack|imperceptibility|attack_execute_detect|attack_metrics|imperceptibility_metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-3}"
JOBS_ATTACK="${JOBS_ATTACK:-3}"
JOBS_IMP="${JOBS_IMP:-2}"
MAX_JOBS="${MAX_JOBS:-10}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)

# Libreria e punti del giro P4 (tests/unit/test_posthoc_config_hashes.py).
EXPECTED=b2b6c18ea5303ab134f84f11d8b1de1c2bb5ec5a
LIB=build/patched/multimark
P4_MCSS=1d3087c73439
P4_B=e52b040770df
P4_D=3fed1f1c1e24
P4_V0=ebeb5a0753cd
P4_V1=40e5e9455cea
P4_POS=271087ba99b6
P4_MASK=16de27361bad
P4_PAPER=65eb9730301a
ABLATIONS="'$P4_V0','$P4_V1','$P4_POS','$P4_MASK','$P4_PAPER'"  # stringhe per Hydra
# Riferimenti già calcolati, solo letti dai report: P3 (b2b6c18, proponente neurale).
P3_B=d54101565a0b
P3_D=e80cde8924cb
# Giro 4 (0664817).
R4_B=a8a296957377
R4_B_SFX=042968dc17df
R4_B_SFX_CL=317adaa840ee
R4_B_SFX_CLZ=a2d195001771
R4_C=19e744a3b406
R4_C_SFX=71d9d5e6160d
R4_C_SFX_CL=a8fadfc4757a
R4_C_SFX_CLZ=fb24f51f5d15
R4_D=b4cd11f13a07
R4_D_SFX=f4fb209f9226
R4_D_SFX_CL=f765c166de34
R4_D_SFX_CLZ=cb29e1d7b6b8
# mcss rifatto (3c47a1f) e giro di capacità (a4e6972).
MCSS_NEW=ae63f16de28f
MCSS=b521002e09f5
MM=3c1bd11758fc
MM_SL=4937d9ddd78a
MM_A=770f999adefb
MM_B=cb79b2fcb4e6
MM_C=cf7e4c9f106e
MM_D=37400fdcd589
LOGS=docs/milestone_logs
# Selezione della Tabella 5 (P1, 169 file) e raccolta dei nomi di T1.4.
SELECTION="$WMB/artifacts/_runs/diagnostics/inventory/mcss_selftest_eel_fb311767f65c.jsonl"
NAMES_POOL="$WMB/artifacts/data/attack_pools/python.json"

ATTACKS="attacks=[T1.4,T1.5,T2.1,T2.4,T4.1,T1.6,T2.5]"
DEV=("levels=[L1,L2]" "splits=[dev]" "languages=[python]")
CLEAN=("methods=[multimark]" "hpo.grid=multimark" "${DEV[@]}")
CLEAN_MCSS=("methods=[mcss]" "${DEV[@]}")
ATTACKED=("${CLEAN[@]}" "$ATTACKS")
ATTACKED_MCSS=("${CLEAN_MCSS[@]}" "$ATTACKS")
IMP=("${CLEAN[@]}" "attacks=[]" "hpo.points=[$ABLATIONS]")
PPL_SOURCES="execution.sources=[llm_baseline,llm_watermarked]"

die() { echo "[STOP] $*" >&2; exit 1; }

check_library() {
    local link head copy
    link="$(git ls-files --stage -- third_party/multimark | awk '$1 == "160000" {print $2}')"
    head="$(git -C third_party/multimark rev-parse HEAD 2>/dev/null || echo none)"
    copy="$(cat "$LIB.source_commit" 2>/dev/null || echo none)"
    [ "$link" = "$EXPECTED" ] || die "submodule recorded at $link, expected $EXPECTED: git pull first"
    [ "$head" = "$EXPECTED" ] || die "third_party/multimark checked out at $head: run git submodule update --init third_party/multimark"
    [ "$copy" = "$EXPECTED" ] || die "$LIB at $copy: run bash scripts/apply_patches.sh multimark"
}

check_hashes() {  # i punti devono essere quelli scritti qui sopra, in quest'ordine
    local grid want mcss
    grid="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only grid --expect-commit "$EXPECTED" | paste -sd' ' -)"
    want="$P4_B $P4_D $P4_V0 $P4_V1 $P4_POS $P4_MASK $P4_PAPER"
    [ "$grid" = "$want" ] || die "multimark grid points are $grid, expected $want"
    mcss="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods mcss --only default --expect-commit "$EXPECTED")"
    [ "$mcss" = "$P4_MCSS" ] || die "mcss default is $mcss, expected $P4_MCSS"
}

no_jobs() {  # $1 = prefisso del nome dei job ancora in coda o in esecuzione
    local running
    running="$(squeue -h -u "$USER" -o '%i %j' 2>/dev/null | awk -v p="$1" '$2 == p || index($2, p ".") == 1 || index($2, p "-") == 1' || true)"
    [ -z "$running" ] || die "jobs still queued or running for $1: $(echo "$running" | paste -sd' ' -)"
}

room() {  # $1 = job che la fase invia al più
    local queued
    queued="$(squeue -h -u "$USER" 2>/dev/null | wc -l || echo 0)"
    [ $((queued + $1)) -le "$MAX_JOBS" ] || die "$queued jobs in queue + $1 to submit > $MAX_JOBS: wait for some jobs to finish"
}

done_cells() {  # $1 = fase; altri argomenti = override di check_stage_done
    local stage="$1"
    shift
    no_jobs "wmb-$stage"
    "$BENCH_PY" scripts/check_stage_done.py "stage=$stage" "$@" || die "$stage not finished"
}

submit() {  # $@ = argomenti di bench submit
    local out
    out="$(bench submit "$@")" || { echo "$out" >&2; die "bench submit failed"; }
    echo "$out" | grep -v "parameters:" >&2
}

finish_log() {  # $1 = nome del log, $2 = intestazione, $3 = uscita temporanea del comando
    local name="$1" header="$2" tmp="$3"
    {
        echo "# $header (scripts/run_p4_dev.sh): $(date -Iseconds)"
        echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED for the points of P4; references read from existing cells"
        cat "$tmp"
    } > "$tmp.log"
    rm -f "$tmp"
    mv -n "$tmp.log" "$LOGS/$name"
    [ ! -e "$tmp.log" ] || die "$LOGS/$name appeared meanwhile: output left in $tmp.log"
    echo "[OK] $LOGS/$name"
}

report_log() {  # $1 = nome del log, $2 = intestazione; altri argomenti = comando del report
    local name="$1" header="$2" tmp
    shift 2
    if [ -e "$LOGS/$name" ]; then
        echo "[SKIP] $LOGS/$name already exists (not overwritten)"
        return 0
    fi
    tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
    "${SRUN[@]}" -c 2 --mem 16G -t 240 "$BENCH_PY" "$@" --report "$tmp"
    finish_log "$name" "$header" "$tmp"
}

# Etichette dei report: punti nuovi (p4_*) e riferimenti già misurati.
NEW=(--config "mcss:$P4_MCSS:p4_mcss" --config "multimark:$P4_B:p4_B" --config "multimark:$P4_D:p4_D"
     --config "multimark:$P4_V0:p4_B_V0" --config "multimark:$P4_V1:p4_B_V1"
     --config "multimark:$P4_POS:p4_B_positional" --config "multimark:$P4_MASK:p4_B_mask_off"
     --config "multimark:$P4_PAPER:p4_B_canonical_paper")
OLD=(--config "mcss:$MCSS_NEW:mcss_new_3c47a1f" --config "mcss:$MCSS:mcss_old_a4e6972"
     --config "multimark:$MM:multimark" --config "multimark:$MM_SL:single_letter"
     --config "multimark:$MM_A:A" --config "multimark:$MM_B:B"
     --config "multimark:$MM_C:C" --config "multimark:$MM_D:D"
     --config "multimark:$R4_B:r4_B" --config "multimark:$R4_B_SFX:r4_B_sfx"
     --config "multimark:$R4_B_SFX_CL:r4_B_sfx_cl" --config "multimark:$R4_B_SFX_CLZ:r4_B_sfx_clz"
     --config "multimark:$R4_C:r4_C" --config "multimark:$R4_C_SFX:r4_C_sfx"
     --config "multimark:$R4_C_SFX_CL:r4_C_sfx_cl" --config "multimark:$R4_C_SFX_CLZ:r4_C_sfx_clz"
     --config "multimark:$R4_D:r4_D" --config "multimark:$R4_D_SFX:r4_D_sfx"
     --config "multimark:$R4_D_SFX_CL:r4_D_sfx_cl" --config "multimark:$R4_D_SFX_CLZ:r4_D_sfx_clz"
     --config "multimark:$P3_B:p3_B" --config "multimark:$P3_D:p3_D")
PAIRS=(--pair "mcss:$P4_MCSS:$MCSS_NEW:p4_mcss" --pair "multimark:$P4_B:$R4_B:p4_B"
       --pair "multimark:$P4_D:$R4_D:p4_D")
ABL_LABELS=(p4_B_V0 p4_B_V1 p4_B_positional p4_B_mask_off p4_B_canonical_paper)
REPORT_DEV=(--levels L1 L2 --split dev --language python)

check_library
check_hashes
mkdir -p "$WMB/logs"
case "$PHASE" in
    function_pools)
        no_jobs wmb-function_pools
        room 1
        submit --jobs 1 stage=function_pools "languages=[python]"
        ;;
    canonical)
        done_cells function_pools "languages=[python]"
        no_jobs wmb-check-attacks-canonical
        room 1
        if [ -e "$LOGS/P4_t2_5_canonical.txt" ]; then
            echo "[SKIP] $LOGS/P4_t2_5_canonical.txt already exists (not overwritten)"
        else
            LOG=P4_t2_5_canonical.txt bash scripts/check_attacks_canonical.sh --languages python --attacks T2.5
        fi
        ;;
    table5)
        name=P4_mcss_table5.txt
        if [ -e "$LOGS/$name" ]; then
            echo "[SKIP] $LOGS/$name already exists (not overwritten)"
            exit 0
        fi
        [ -f "$SELECTION" ] || die "selection not found: $SELECTION"
        [ -f "$NAMES_POOL" ] || die "name pool of T1.4 not found: $NAMES_POOL (stage attack_pools)"
        POSTHOC_PY="${POSTHOC_PY:-$(sed -n 's/^  posthoc: *{python: *\([^}]*\)}.*$/\1/p' configs/envs/envs.yaml)}"
        [ -x "$POSTHOC_PY" ] || die "posthoc interpreter not found: '$POSTHOC_PY' (configs/envs/envs.yaml)"
        room 1
        tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
        "${SRUN[@]}" -c 8 --mem 16G -t 240 "$POSTHOC_PY" scripts/mcss_paper_attacks.py --attack table5 \
            --workers 8 --selection "$SELECTION" --names-pool "$NAMES_POOL" --library "$LIB" \
            --records "${tmp%.txt}_records.jsonl" --report "$tmp"
        finish_log "$name" "P4 MCSS Table 5 on the 169 files of the selection" "$tmp"
        ;;
    watermark)
        echo "##### config_hash of P4 (scripts/posthoc_config_hashes.py)"
        "$BENCH_PY" scripts/posthoc_config_hashes.py --methods mcss multimark --expect-commit "$EXPECTED"
        no_jobs wmb-watermark
        room $((1 + JOBS))
        submit --jobs 1 stage=watermark "${CLEAN_MCSS[@]}"
        submit --jobs "$JOBS" stage=watermark "${CLEAN[@]}"
        ;;
    identity)
        done_cells watermark "${CLEAN_MCSS[@]}"
        done_cells watermark "${CLEAN[@]}"
        name=P4_embed_identity_dev.txt
        if [ -e "$LOGS/$name" ]; then
            echo "[SKIP] $LOGS/$name already exists (not overwritten)"
            exit 0
        fi
        room 1
        tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
        status=0
        "${SRUN[@]}" -c 2 --mem 16G -t 120 "$BENCH_PY" scripts/check_embed_identity.py "${PAIRS[@]}" \
            --levels L1 L2 --split dev --language python --report "$tmp" || status=$?
        [ -e "$tmp" ] || die "check_embed_identity.py wrote no report (exit $status)"
        finish_log "$name" "P4 embedding of mcss, B and D at b2b6c18 vs the measured cells" "$tmp"
        tail -n 1 "$LOGS/$name"
        [ "$status" -eq 0 ] || die "embedding NOT identical: the imperceptibility of mcss, B and D must be measured again (see $LOGS/$name)"
        ;;
    execute_detect)
        done_cells watermark "${CLEAN_MCSS[@]}"
        done_cells watermark "${CLEAN[@]}"
        room $((2 + 2 * JOBS))
        submit --jobs 1 stage=execute "${CLEAN_MCSS[@]}" "execution.sources=[llm_watermarked]"
        submit --jobs "$JOBS" stage=execute "${CLEAN[@]}" "execution.sources=[llm_watermarked]"
        submit --jobs 1 stage=detect "${CLEAN_MCSS[@]}"
        submit --jobs "$JOBS" stage=detect "${CLEAN[@]}"
        ;;
    calibrate)
        done_cells execute "${CLEAN_MCSS[@]}" "execution.sources=[llm_watermarked]"
        done_cells execute "${CLEAN[@]}" "execution.sources=[llm_watermarked]"
        done_cells detect "${CLEAN_MCSS[@]}"
        done_cells detect "${CLEAN[@]}"
        room $((1 + JOBS))
        submit --jobs 1 stage=calibrate "${CLEAN_MCSS[@]}"
        submit --jobs "$JOBS" stage=calibrate "${CLEAN[@]}"
        ;;
    metrics)
        done_cells calibrate "${CLEAN_MCSS[@]}"
        done_cells calibrate "${CLEAN[@]}"
        room $((1 + JOBS))
        submit --jobs 1 stage=metrics "${CLEAN_MCSS[@]}"
        submit --jobs "$JOBS" stage=metrics "${CLEAN[@]}"
        ;;
    attack)
        done_cells metrics "${CLEAN_MCSS[@]}"
        done_cells metrics "${CLEAN[@]}"
        done_cells attack_pools "languages=[python]"
        done_cells function_pools "languages=[python]"
        no_jobs wmb-attack
        room $((1 + JOBS_ATTACK))
        submit --jobs 1 stage=attack "${ATTACKED_MCSS[@]}"
        submit --jobs "$JOBS_ATTACK" stage=attack "${ATTACKED[@]}"
        ;;
    imperceptibility)
        done_cells metrics "${CLEAN[@]}"
        for stage in perplexity adversarial_logreg adversarial_unixcoder; do no_jobs "wmb-$stage"; done
        room $((3 * JOBS_IMP))
        submit --jobs "$JOBS_IMP" stage=perplexity "${IMP[@]}" "$PPL_SOURCES"
        submit --jobs "$JOBS_IMP" stage=adversarial_logreg "${IMP[@]}"
        submit --jobs "$JOBS_IMP" stage=adversarial_unixcoder "${IMP[@]}"
        ;;
    attack_execute_detect)
        done_cells attack "${ATTACKED_MCSS[@]}"
        done_cells attack "${ATTACKED[@]}"
        room $((2 + 2 * JOBS_ATTACK))
        submit --jobs 1 stage=execute "${ATTACKED_MCSS[@]}" "execution.sources=[attacked]"
        submit --jobs "$JOBS_ATTACK" stage=execute "${ATTACKED[@]}" "execution.sources=[attacked]"
        submit --jobs 1 stage=detect "${ATTACKED_MCSS[@]}"
        submit --jobs "$JOBS_ATTACK" stage=detect "${ATTACKED[@]}"
        ;;
    attack_metrics)
        done_cells execute "${ATTACKED_MCSS[@]}" "execution.sources=[attacked]"
        done_cells execute "${ATTACKED[@]}" "execution.sources=[attacked]"
        done_cells detect "${ATTACKED_MCSS[@]}"
        done_cells detect "${ATTACKED[@]}"
        room $((1 + JOBS_ATTACK))
        submit --jobs 1 stage=metrics "${ATTACKED_MCSS[@]}"
        submit --jobs "$JOBS_ATTACK" stage=metrics "${ATTACKED[@]}"
        ;;
    imperceptibility_metrics)
        done_cells perplexity "${IMP[@]}" "$PPL_SOURCES"
        done_cells adversarial_logreg "${IMP[@]}"
        done_cells adversarial_unixcoder "${IMP[@]}"
        no_jobs wmb-metrics_imperceptibility
        room "$JOBS_IMP"
        submit --jobs "$JOBS_IMP" stage=metrics_imperceptibility "${IMP[@]}"
        ;;
    report)
        done_cells metrics "${ATTACKED_MCSS[@]}"
        done_cells metrics "${ATTACKED[@]}"
        done_cells metrics_imperceptibility "${IMP[@]}"
        [ -e "$LOGS/P4_embed_identity_dev.txt" ] || die "run the identity phase first"
        room 1
        report_log P4_rename_check_dev.txt "P4 rename check of the new points" \
            scripts/check_rename_dev.py "${NEW[@]}" --multimark third_party/multimark \
            --expect-commit "${EXPECTED:0:7}" "${REPORT_DEV[@]}"
        report_log P4_capacity_dev.txt "P4 on dev: S tables and curve in K" \
            scripts/report_round3.py "${OLD[@]}" "${NEW[@]}" \
            --curve mcss_new_3c47a1f multimark p4_mcss p4_B p4_D "${REPORT_DEV[@]}"
        report_log P4_snake_dev.txt "P4 T1.6 snake_case on dev" \
            scripts/report_snake_dev.py "${OLD[@]}" "${NEW[@]}" "${REPORT_DEV[@]}"
        report_log P4_imperceptibility_dev.txt "P4 imperceptibility of the ablations on dev" \
            scripts/report_imperceptibility_dev.py \
            --config "mcss:$MCSS_NEW:mcss_new_3c47a1f" --config "multimark:$MM:multimark" \
            --config "multimark:$R4_B:r4_B" --config "multimark:$R4_D:r4_D" \
            --config "multimark:$P4_V0:p4_B_V0" --config "multimark:$P4_V1:p4_B_V1" \
            --config "multimark:$P4_POS:p4_B_positional" --config "multimark:$P4_MASK:p4_B_mask_off" \
            --config "multimark:$P4_PAPER:p4_B_canonical_paper" --base multimark "${REPORT_DEV[@]}"
        report_log P4_dev.txt "P4 on dev: rules of v1.3.10, ablations next to B, T2.5, 8/16 bits" \
            scripts/report_p4.py "${OLD[@]}" "${NEW[@]}" --base multimark \
            --mcss mcss_new_3c47a1f --b p4_B --ablation "${ABL_LABELS[@]}" --one-bit p4_B_V0 p4_B_V1 \
            --bits p4_B p4_D \
            --t25 p4_mcss p4_B p4_D "${ABL_LABELS[@]}" \
            --same p4_mcss=mcss_new_3c47a1f p4_B=r4_B p4_D=r4_D "${PAIRS[@]}" \
            --multimark third_party/multimark --expect-commit "${EXPECTED:0:7}" "${REPORT_DEV[@]}"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
