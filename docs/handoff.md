# Handoff: ripresa del lavoro sul ramo `posthoc-multimark`

Scritto il 28 novembre 2026. Serve a una sessione nuova per riprendere senza perdere niente. Prima di
qualsiasi lavoro valgono `CLAUDE.md`, `SPEC.md` e `docs/cluster_info.md` (in caso di conflitto prevale
`docs/cluster_info.md`). Nel file non ci sono token né indirizzi email. Il token di Sourcery sta solo in
`~/.config/wmb/sourcery_token` sul cluster.

## 1. Stato del ramo

- **Ultimo commit:** `301ef4b` (27 novembre 2026), «[P7] chains: niente invii dietro un passo FAILED
  (DEP_FAILED …), timeout dei comandi SLURM, riga tick start, avviso sul token di Sourcery oltre 12
  giorni». Il ramo locale coincide con `origin/posthoc-multimark`. **Non ci sono commit locali da
  pushare**, a parte il commit di questo file, se viene fatto.
- **Rispetto a `main`:** `main` è fermo ad `a64f4ea` (10 novembre 2026), il punto di partenza del ramo.
  Il ramo ha 111 commit in più. Il dettaglio, con l'effetto sui cinque metodi, è in
  `docs/modifiche_ramo.md`, che arriva fino a `9740349` (25 novembre). In sintesi:
  - **benchmark dei cinque metodi** (SWEET, ACW, STONE, PromptMark, MCGMark): `main` più D42 (limite
    di tempo sulle chiamate a Sourcery di ACW). Il codice che corrisponde esattamente è `ed59236`;
  - **estensione per il metodo della tesi** (`mcss`, `multimark`): ambiente `posthoc`, adapter e
    shim, metriche multi-bit comuni (ADR-017), attacchi nuovi T1.6 e T2.5 (ADR-018), congelamento
    dei post-hoc e controllo sul test (ADR-019), catene. Il congelamento P6 ha il tag `p6-freeze`.
- **Dopo `9740349`** (non ancora in `docs/modifiche_ramo.md`):
  - P6 sul test di `mcss` e `multimark`: report (`7a12a0e`, `92856bc`), correzione di T3.1 dei
    post-hoc con `PARTIAL` (`6695985`) e rilevazione rifatta con la v3 (`862781c`, `b6a6156`),
    esempi di codice per la tesi (`b73170c`, `b003255`);
  - P7.a: catene di T1.6 e T2.5 su L1 e L2 test per ACW, STONE e MCGMark (`cf19aa3`), stime e prova
    a vuoto (`eac622b`, `8f5588a`), risultati di STONE e MCGMark (`ea26d08`, `3ecc725`);
  - catene più robuste (`301ef4b`).
- **Fuori dal controllo di versione:** `docs/da_lanciare/` non è tracciata e contiene
  `analisi_benchmark.md` (analisi del benchmark ad `a64f4ea`, fatta in una sessione separata),
  `comando_completo.txt` e `completezza_cinque.py`. Lo script è identico a quello della §3.1.

## 2. Stato dei risultati sul cluster

Radici: `WMB=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench` (artefatti in `$WMB/artifacts`, log
in `$WMB/logs`) e `REPO=/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench`.

**Completo** (da `docs/provenienza_risultati.md`, 25 novembre, e dai log di P6 e P7.a):

- **i cinque metodi:** sviluppo, calibrazione, scelta e congelamento; L1 test con gli attacchi
  della M10–M11 e l'impercettibilità della M12; L2 e L3 test con T2.4k. Fra questi c'è anche ACW
  su L3, rifatto con D42 dopo il blocco del 5–12 novembre;
- **`mcss` e `multimark`:** parte di test P6 su L1, L2 e ClassEval (log `P6_*`), con T3.1 rifatto
  nella v3;
- **P7.a di STONE e MCGMark:** T1.6 e T2.5 su L1 e L2 test, solo python (log
  `P7a_attacks_test_{stone,mcgmark}_L{1,2}.txt` e `P7a_baseline_stone_mcgmark.txt`).

**Rilanciato:** le rilevazioni P7.a di ACW (T1.6 e T2.5, L1 e L2, varianti ridotta e completa),
lanciate dall'utente il 27–28 novembre 2026 con il token nuovo. **Non è ancora verificato** che le
catene siano arrivate a `DONE`: va controllato con `scripts/chains.py status` **prima** del controllo
di completezza (§3.1).

- **Perché:** tutti i 24 job di rilevazione erano falliti al canarino (`FatalShimError`) perché la
  prova di Sourcery era scaduta. Il token è stato rinnovato il 27 novembre.
- **Ordine del rilancio:** `git pull`, canarino, `scancel` dei job ACW rimasti, `reset` degli 8 passi
  e poi il ciclo:

  ```bash
  cd "$REPO"; PY="$WMB/bench-core/bin/python"
  ACW=(); for l in L1 L2; do for a in T1.6 T2.5; do ACW+=(--only "acw/$l/$a-reduced/" --only "acw/$l/$a/"); done; done
  "$PY" scripts/chains.py reset --owner ivan "${ACW[@]}" --reason "Sourcery token expired (canary), renewed 2026-11-27"
  "$PY" scripts/chains.py run  --owner ivan --with-acw "${ACW[@]}" --dry-run
  "$PY" scripts/chains.py loop --owner ivan --with-acw "${ACW[@]}"
  ```

- **Da controllare, nell'ordine:**
  1. che le 8 catene di ACW siano `DONE`:
     `"$PY" scripts/chains.py status --owner ivan "${ACW[@]}"`;
  2. il controllo di completezza (§3.1);
  3. i risultati, con `scripts/check_attacks.py acw T1.6|T2.5 [--level L2]`.

**Manca:**

- **Ricalcolo delle metriche di MCGMark: necessario.** La tesi mostra la capacità sotto attacco di
  MCGMark. Basta la fase `metrics` con `force=true` (§3.2). Le celle di M10 e M11 non hanno
  `message_accuracy_detected` né la capacità sulle celle con attacco, perché sono calcolate prima di
  ADR-017. Le metriche P7.a di MCGMark invece le hanno: senza il ricalcolo le tabelle di MCGMark
  restano miste.
- **Figure della tesi, in PDF.** Vanno generate dal ramo, compresi P7.a e le metriche di MCGMark
  ricalcolate, in una cartella nuova sotto `$WMB`. Prima va verificato quali figure produce già il
  report (`scripts/report.py` e gli script `report_*.py`).
- **Controllo di completezza dei cinque metodi:** `completezza_cinque.txt`, §3.1, mai lanciato.

## 3. Comandi pronti, non ancora lanciati

Variabili comuni (nodo di accesso, dalla radice del clone):

```bash
WMB=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench
REPO=/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 2 --mem 8G -t 60)
```

### 3.1 Controllo di completezza dei cinque metodi (`completezza_cinque.txt`, sola lettura)

**Cosa controlla.** Lo script pianifica, con lo stesso `CellPlanner` delle catene, le celle previste:
- le catene dei cinque metodi (tutta la catena di SWEET, L2 e L3, T2.4k, P7.a, baseline);
- L1 test degli altri quattro metodi, lanciato fuori dalle catene (M10–M12): fase 0, T1.1–T1.5,
  T2.1, T2.2 con il controllo D34, T2.4, T3.1 con k2, T4.1, T4.2, M12 e le misure secondarie di ACW.

**Cosa scrive.** Le confronta con gli artefatti e scrive nell'ordine:
1. i congelamenti;
2. lo stato per passo;
3. la matrice catena × modello × linguaggio × parte;
4. gli attacchi per metodo e livello (`NOT RUN`, `INCOMPLETE`, `complete`, `n/a`);
5. le quote `FAILED` e `TOOL_ERROR` delle celle complete, con le metriche di MCGMark che hanno
   `message_accuracy_detected`;
6. gli artefatti senza `repo_commit`;
7. l'elenco delle celle non complete.

Non scrive nello store e non sovrascrive il log. In locale, su uno store vuoto, impiega circa 9
minuti; qui ha `-t 120`.

**Cosa aspettarsi.**
- Le celle P7.a di ACW risultano `MISSING` finché il rilancio non è finito.
- SWEET e PromptMark non hanno T1.6 e T2.5, per decisione dell'utente.
- T4.2 esiste solo per SWEET (JavaScript); per gli altri esce `n/a`.

```bash
WMB=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench
REPO=/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 2 --mem 8G -t 120)
OUT="$WMB/logs/completezza_cinque.txt"
cd "$REPO"
if [ -e "$OUT" ]; then echo "[SKIP] $OUT exists (not overwritten)"; else
"${SRUN[@]}" "$WMB/bench-core/bin/python" - "$WMB" > "$OUT.tmp" 2> "$OUT.stderr.tmp" <<'EOF'
# Controllo di completezza dei cinque metodi (sola lettura): celle previste dalle catene e dai
# lanci di L1 fuori dalle catene (M10-M12), pianificate con lo stesso CellPlanner, contro gli
# artefatti completi dello store. Non scrive niente: legge manifest e colonne dei Parquet.
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

import pyarrow.parquet as pq

import bench.pipeline.stages  # noqa: F401  (popola STAGES)
from bench.chains import _test, all_steps, attack_steps, m12_steps, METHOD_OWNER, Step, t3_1_steps, test_phase
from bench.config.builder import load_experiment
from bench.hpo.freeze import load_freeze
from bench.pipeline.stage import CellPlanner
from bench.registry import STAGES
from bench.store.artifact_store import ArtifactStore

WMB, PATHS = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "cluster")
BASE = [f"paths={PATHS}"]
FIVE = ("sweet", "acw", "stone", "promptmark", "mcgmark")
L1_ATTACKS = ("T1.1", "T1.2", "T1.3", "T1.4", "T1.5", "T2.1", "T2.2", "T2.4", "T4.1", "T4.2")
FLAG = 0.05  # quota oltre la quale una frazione FAILED / TOOL_ERROR si segnala


def out(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------------ passi previsti
def l1_steps(m: str) -> list[tuple[str, Step]]:
    """L1 test dei quattro metodi lanciati fuori dalle catene (M10-M12), con le stesse funzioni."""
    o, steps = METHOD_OWNER[m], []
    steps += [("L1 (M10)", s) for s in test_phase(m, "L1", WMB)]
    for a in L1_ATTACKS:
        steps += [("L1 attacks (M10-M11)", s) for s in attack_steps(m, a, "L1", WMB)]
    t22 = _test(m, "L1", "[T2.2]")
    steps += [
        ("L1 D34 control", Step(f"{m}/L1/T2.2/control_detect", o, "detect_attack_control", t22)),
        ("L1 D34 control", Step(f"{m}/L1/T2.2/control_metrics", o, "metrics_attack_control",
                                (*t22, "detection.threshold_levels=[L1,L2]"))),
    ]
    steps += [("L1 T3.1 (M11)", s) for s in t3_1_steps(m, WMB, after=())]
    steps += [("M12", s) for s in m12_steps(m, after=())]
    if m == "acw":  # misure secondarie al netto della formattazione (ADR-015 §7)
        test = _test(m, "L1")
        steps += [("M12 secondary", Step("acw/M12/logreg_fmtfree", o, "adversarial_logreg_fmtfree", test)),
                  ("M12 secondary", Step("acw/M12/unixcoder_fmtfree", o, "adversarial_unixcoder_fmtfree", test)),
                  ("M12 secondary", Step("acw/M12/acw_format", o, "metrics_acw_format",
                                         (*test, "detection.threshold_levels=[L1,L2]")))]
    return steps


def expected() -> list[tuple[str, Step]]:
    steps: list[tuple[str, Step]] = []
    for s in all_steps(WMB):
        head = s.key.split("/")[0]
        if head == "base":
            steps.append(("baseline (prerequisite)", s))
        elif head in FIVE:
            parts = s.key.split("/")
            p7a = len(parts) > 3 and parts[2].startswith(("T1.6", "T2.5"))
            group = "P7.a" if p7a else "chains"
            steps.append((group, s))
    for m in ("promptmark", "stone", "mcgmark", "acw"):
        steps += l1_steps(m)
    return steps


# ------------------------------------------------------------------ stato degli artefatti
def ref_state(store: ArtifactStore, ref) -> str:
    path, man = store.path_of(ref), store.read_manifest(ref)
    if man is None:
        return "NO_MANIFEST" if path.exists() else "MISSING"
    if man.status != "complete":
        return man.status.upper()  # RUNNING (job in corso o ucciso) / FAILED
    if not path.is_file() or man.data_size != path.stat().st_size:
        return "SIZE_MISMATCH"
    return "SUPERSEDED" if (man.extra or {}).get("superseded") else "OK"


def cell_state(states: list[str]) -> str:
    if all(s == "OK" for s in states):
        return "COMPLETE"
    if any(s == "OK" for s in states):
        return "PARTIAL"
    for s in ("FAILED", "RUNNING", "SIZE_MISMATCH", "NO_MANIFEST", "SUPERSEDED"):
        if s in states:
            return {"SIZE_MISMATCH": "CORRUPT", "NO_MANIFEST": "CORRUPT"}.get(s, s)
    return "MISSING"


def columns(path, wanted):
    try:
        names = set(pq.read_schema(path).names)
        cols = [c for c in wanted if c in names]
        return pq.read_table(path, columns=cols).to_pandas() if cols else None
    except Exception:  # noqa: BLE001
        return None


def content(store, ref, stats, method, unregistered):
    """Contatori dal contenuto di un artefatto completo (solo colonne di stato)."""
    rel = str(ref.path)
    man = store.read_manifest(ref)
    prov = (man.provenance.model_dump() if man is not None and man.provenance is not None else {})
    if not prov.get("repo_commit"):
        unregistered[method] += 1
    if not rel.endswith(".parquet"):
        return
    path = store.path_of(ref)
    if rel.startswith("watermarked/"):
        t = columns(path, ["embed_status"])
        if t is not None:
            stats["embed"] += len(t)
            stats["embed_FAILED"] += int((t["embed_status"] == "FAILED").sum())
    elif rel.startswith("attacked/"):
        t = columns(path, ["attack_status"])
        if t is not None:
            c = Counter(t["attack_status"].astype(str))
            stats["attack"] += len(t)
            for k, v in c.items():
                stats[f"attack_{k}"] += v
    elif rel.startswith("detection"):
        t = columns(path, ["status"])
        if t is not None:
            kind = ("pos" if "/positives/" in rel else "att" if "/attacked/" in rel else "neg")
            stats[f"det_{kind}"] += len(t)
            stats[f"det_{kind}_FAILED"] += int((t["status"] != "OK").sum())
    elif rel.startswith("metrics") and method == "mcgmark":
        t = columns(path, ["name"])
        if t is not None:
            stats["mcg_metrics"] += 1
            stats["mcg_metrics_with_mad"] += int((t["name"] == "message_accuracy_detected").any())


# ------------------------------------------------------------------ main
def main() -> None:
    cfg0 = load_experiment([*BASE, "stage=watermark"])
    store = ArtifactStore(cfg0.paths.artifacts)
    try:
        commit = subprocess.run(["git", "--no-optional-locks", "rev-parse", "--short=12", "HEAD"],
                                capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = "?"
    out(f"# completeness of the five methods (read-only) {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    out(f"# repo HEAD {commit or '?'}  artifacts {cfg0.paths.artifacts}")

    out("\n## 1. freeze records (dev part: calibrate, tune, freeze)")
    for m in FIVE:
        for key in ("k1", "k2"):
            rec = load_freeze(store, m, key)
            if rec is None:
                out(f"  {m:<11}{key}  NOT FROZEN")
                continue
            cells = ", ".join(f"{c.model_id}/{c.language}="
                              f"{'n/a' if c.not_applicable else c.config_hash}"
                              f"{'' if c.not_applicable or c.threshold is not None else ' (no threshold)'}"
                              for c in rec.cells)
            out(f"  {m:<11}{key}  frozen {rec.frozen_at:%Y-%m-%d}  levels {rec.levels}  {cells}")

    rows: dict = defaultdict(lambda: {"stages": {}, "stats": Counter()})
    step_lines, bad_cells, unplannable = [], [], []
    unregistered: Counter = Counter()
    seen_cells: set = set()
    for group, step in expected():
        try:
            cfg = load_experiment([*BASE, f"stage={step.stage}", *step.overrides])
            stage_cls = STAGES.get(step.stage)
            stage = stage_cls.create(cfg)
            cells = CellPlanner(cfg).cells_for(stage_cls)
        except Exception as exc:  # noqa: BLE001  (es. metodo non congelato)
            unplannable.append((group, step.key, f"{type(exc).__name__}: {str(exc)[:200]}"))
            continue
        tally: Counter = Counter()
        chain = step.chain
        for cell in cells:
            method = cell.method or step.key.split("/")[0]
            if cell.split == "test" and cell.method and cell.config_hash is None:
                tally["NOT_APPLICABLE"] += 1
                continue
            try:
                refs = stage.outputs(cell)
            except Exception as exc:  # noqa: BLE001
                tally["ERROR"] += 1
                bad_cells.append((step.key, cell.key(), "ERROR", f"{type(exc).__name__}: {str(exc)[:160]}"))
                continue
            states = [ref_state(store, r) for r in refs]
            state = cell_state(states)
            tally[state] += 1
            row = rows[(group, chain, method, cell.model_id or "-", cell.language or "-", cell.split or "-")]
            st = row["stages"].setdefault(step.stage, Counter())
            st["total"] += 1
            st["ok"] += state == "COMPLETE"
            first_new = (step.stage, cell.key()) not in seen_cells  # ridotta e completa di P7.a
            seen_cells.add((step.stage, cell.key()))
            for r, s in zip(refs, states):
                if s == "OK" and first_new:
                    content(store, r, row["stats"], method, unregistered)
            if state != "COMPLETE":
                detail = "; ".join(f"{s} {r.path}" for r, s in zip(refs, states) if s != "OK")
                bad_cells.append((step.key, cell.key(), state, detail[:400]))
        step_lines.append((group, step.key, step.stage, len(cells), tally))

    out("\n## 2. steps: planned cells and state (COMPLETE = every output complete)")
    out(f"  {'group':<24}{'step':<42}{'stage':<28}{'cells':>6}  states")
    for group, key, stage, n, tally in step_lines:
        flag = "" if tally.get("COMPLETE", 0) + tally.get("NOT_APPLICABLE", 0) == n else "  <<"
        if n == 0:
            flag = "  (no cells: not applicable)"
        out(f"  {group:<24}{key:<42}{stage:<28}{n:>6}  {dict(sorted(tally.items()))}{flag}")
    out("\n## 2b. steps that cannot be planned")
    for group, key, msg in unplannable:
        out(f"  {group:<24}{key:<42}{msg}")
    if not unplannable:
        out("  none")

    out("\n## 3. matrix: chain x model x language x split, complete/planned cells per stage")
    for (group, chain, method, model, lang, split), row in sorted(rows.items()):
        cols = "  ".join(f"{s}={c['ok']}/{c['total']}" for s, c in row["stages"].items())
        full = all(c["ok"] == c["total"] for c in row["stages"].values())
        out(f"  {'ok ' if full else '!! '}{chain:<30}{model:<22}{lang:<11}{split:<5}  {cols}")

    out("\n## 4. attacks by method and level (NOT RUN = no complete attack cell)")
    agg: dict = defaultdict(dict)
    for (group, chain, method, model, lang, split), row in rows.items():
        parts = chain.split("/")
        if len(parts) >= 3 and parts[2].startswith("T"):
            a = agg[(method, parts[1], parts[2])]
            for s, c in row["stages"].items():
                ok, tot = a.get(s, (0, 0))
                a[s] = (ok + c["ok"], tot + c["total"])
    for (method, level, attack), a in sorted(agg.items()):
        cols = "  ".join(f"{s} {ok}/{tot}" for s, (ok, tot) in a.items())
        if a.get("attack", (1, 1))[0] == 0:
            state = "NOT RUN"
        elif all(ok == tot for ok, tot in a.values()):
            state = "complete"
        else:
            state = "INCOMPLETE"
        out(f"  {state:<11}{method:<11}{level:<4}{attack:<16}{cols}")
    for _, k, _, n, _ in step_lines:
        if n == 0 and k.endswith("/attack"):
            out(f"  {'n/a':<11}{k:<42}no cells (attack not applicable to the method's languages)")

    out(f"\n## 5. content of complete cells (flag > {FLAG:.0%})")
    out("  embed FAILED / rows; attack status; detection non-OK / rows (pos, neg, att)")
    for (group, chain, method, model, lang, split), row in sorted(rows.items()):
        s = row["stats"]
        if not s:
            continue
        bits, flags = [], []
        if s["embed"]:
            f = s["embed_FAILED"] / s["embed"]
            bits.append(f"embed FAILED {s['embed_FAILED']}/{s['embed']}")
            flags += ["embed"] if f > FLAG else []
        if s["attack"]:
            att = {k[7:]: v for k, v in s.items() if k.startswith("attack_")}
            bits.append(f"attack {dict(sorted(att.items()))}")
            flags += ["tool_error"] if att.get("TOOL_ERROR", 0) / s["attack"] > FLAG else []
        for kind in ("pos", "neg", "att"):
            if s[f"det_{kind}"]:
                f = s[f"det_{kind}_FAILED"] / s[f"det_{kind}"]
                bits.append(f"det_{kind} {s[f'det_{kind}_FAILED']}/{s[f'det_{kind}']}")
                flags += [f"det_{kind}"] if f > FLAG else []
        if s["mcg_metrics"]:
            bits.append(f"mcgmark metrics with message_accuracy_detected {s['mcg_metrics_with_mad']}/{s['mcg_metrics']}")
        out(f"  {'!! ' + ','.join(flags) if flags else 'ok'}  {chain} {model} {lang} {split}: " + "; ".join(bits))

    out("\n## 6. complete artifacts without repo_commit (provenance), by method")
    for m, n in sorted(unregistered.items()):
        out(f"  {m:<11}{n}")

    out(f"\n## 7. cells not complete ({len(bad_cells)})")
    for key, cell, state, detail in bad_cells:
        out(f"  {state:<10}{key:<40}{cell}")
        out(f"            {detail}")

    total = Counter()
    for _, _, _, _, tally in step_lines:
        total.update(tally)
    out(f"\n## summary: {len(step_lines)} plannable steps, {len(unplannable)} not plannable; cells {dict(sorted(total.items()))}")


main()
EOF
mv -n "$OUT.tmp" "$OUT"; mv -n "$OUT.stderr.tmp" "${OUT%.txt}.stderr.txt"
sed -n '/^## 4\./,/^## 5\./p' "$OUT"; tail -n 1 "$OUT"
fi
```

### 3.2 Ricalcolo delle metriche di MCGMark (da `docs/da_fare_cluster.md` §3), necessario

È necessario perché la tesi mostra la capacità sotto attacco di MCGMark.

**Cosa fa.** Riscrive solo `metrics_L1L2/mcgmark/...`: stesse celle, stessi percorsi, soltanto la
configurazione congelata `e4a6d39`. Non tocca calibrazione, scelta né congelamento. Aggiunge
`message_accuracy_detected`; sulle celle con attacco aggiunge anche `message_accuracy`,
`bit_accuracy` ed `embedding_success_rate`. I valori che esistevano già restano identici.

**Prima del lancio:**
1. il ciclo delle catene deve girare solo con filtri che escludono MCGMark: mentre una cella si
   riscrive, il suo manifest manca per un momento;
2. serve una copia di sicurezza;
3. servono 4 posti liberi in coda.

**Dopo:**
- rigenerare il report in una cartella nuova;
- aggiornare `docs/provenienza_risultati.md`.

**Celle P7.a.** Le metriche P7.a di MCGMark (T1.6 e T2.5) hanno già le righe nuove. Non stanno negli
attacchi qui sotto e non vanno ricalcolate.

```bash
cp -a "$WMB/artifacts/metrics_L1L2/mcgmark" "$WMB/backup_metrics_mcgmark_$(date +%Y%m%d)"
B=("stage=metrics" "methods=[mcgmark]" "splits=[test]" "languages=[python]"
   "detection.threshold_levels=[L1,L2]" "force=true")
# prima a vuoto: elenco delle celle, nessun invio
bench submit --dry-run --jobs 1 "${B[@]}" "levels=[L1,L2,L3]" "attacks=[]"
bench submit --dry-run --jobs 2 "${B[@]}" "levels=[L1]" "attacks=[T1.1,T1.2,T1.3,T1.4,T1.5,T2.1,T2.2,T2.4,T3.1,T4.1]"
bench submit --dry-run --jobs 1 "${B[@]}" "levels=[L2,L3]" "attacks=[T2.4k]"
# poi gli stessi tre comandi senza --dry-run
```

### 3.3 Controllo delle 6 estrazioni fallite di DeepSeek (ACW, L3 test; da `docs/da_fare_cluster.md` §4)

Sola lettura. Riguarda DeepSeek su L3 test (configurazione `86851261a344`) e stampa:
- i 5 codici più lunghi della baseline;
- per ogni inserimento `FAILED`, il risultato del worker;
- le rilevazioni `FAILED` di positivi, `neg_llm` e del primo punto di T2.4k.

**Il controllo è già fatto e non va rilanciato.** Il blocco resta qui solo come riferimento. I commit `9749ca4` e `7bf23b4` (25 novembre)
riportano in `docs/provenienza_risultati.md` le cause, lette da `$WMB/logs/acw_l3_deepseek_failed.txt`:
- 2 `sourcery timeout` (D42), cioè i due campioni da 24.717 caratteri;
- 3 campioni con codice vuoto nella baseline;
- 1 campione senza regole applicabili.

Se per errore lo si rilancia, il comando trova il log esistente e si salta senza sovrascriverlo.

```bash
OUT="$WMB/logs/acw_l3_deepseek_failed.txt"
if [ -e "$OUT" ]; then echo "[SKIP] $OUT exists (not overwritten)"; else
"${SRUN[@]}" "$WMB/bench-core/bin/python" - "$WMB/artifacts" > "$OUT.tmp" <<'EOF'
import glob, json, os, sys
import pandas as pd

root = sys.argv[1]
M, C, S = "deepseek_coder_6p7b", "86851261a344", "L3_python_test"


def short(extra):
    extra = {k: v for k, v in (extra or {}).items() if k != "rules"}
    return json.dumps(extra, sort_keys=True, default=str)[:500]


base = pd.read_parquet(f"{root}/baseline/{M}/{S}.parquet", columns=["sample_id", "dataset", "code"])
base["chars"] = base["code"].str.len()
longest = base.sort_values("chars", ascending=False)
top2 = set(longest["sample_id"].head(2))
print(f"# ACW, L3 test, {M}, config {C}")
print("## longest baseline codes")
for r in longest.head(5).itertuples():
    print(f"  {r.chars:>7}  {r.sample_id}  {r.dataset}")

items = {}
for res in sorted(glob.glob(f"{root}/_runs/watermark/acw/{M}/{C}/{S}/**/results.jsonl", recursive=True)):
    with open(res) as h:
        for line in h:
            if line.strip():
                r = json.loads(line)
                items[r.get("item_id")] = r
wm = pd.read_parquet(f"{root}/watermarked/acw/{M}/{C}/{S}.parquet",
                     columns=["sample_id", "parent_id", "dataset", "embed_status"])
chars = dict(zip(base["sample_id"], base["chars"]))
print("## FAILED embeddings (worker results)")
for r in wm[wm["embed_status"] == "FAILED"].itertuples():
    w = items.get(r.parent_id, {})
    print(f"- {r.parent_id}  {r.dataset}  chars {chars.get(r.parent_id)}  "
          f"{'LONGEST-2' if r.parent_id in top2 else ''}")
    print(f"    worker status {w.get('status')}  error {str(w.get('error'))[:400]!r}")
    print(f"    extra {short(w.get('extra'))}")

det = f"{root}/detection/acw/{M}/{C}"
points = sorted(glob.glob(f"{det}/attacked/T2.4k/*/{S}.parquet"))
tables = [("positives", f"{det}/positives/{S}.parquet"), ("neg_llm", f"{det}/neg_llm/{S}.parquet")]
tables += [("T2.4k " + p.split(os.sep)[-2], p) for p in points[:1]]
parents = dict(zip(wm["sample_id"], wm["parent_id"]))  # marcato -> baseline
for p in points[:1]:
    h = p.split(os.sep)[-2]
    att = glob.glob(f"{root}/attacked/T2.4k/{h}/acw/{M}/{C}/{S}.parquet")
    if att:
        a = pd.read_parquet(att[0], columns=["sample_id", "parent_id"])
        parents.update(dict(zip(a["sample_id"], a["parent_id"])))
print("## FAILED detections (positives, neg_llm, first T2.4k point)")
for name, path in tables:
    t = pd.read_parquet(path, columns=["sample_id", "status", "extra_json"])
    for r in t[t["status"] == "FAILED"].itertuples():
        extra = json.loads(r.extra_json) if isinstance(r.extra_json, str) and r.extra_json else {}
        origin = parents.get(r.sample_id, r.sample_id)
        origin = parents.get(origin, origin)  # attaccato -> marcato -> baseline
        print(f"- {name:<20} {r.sample_id}  origin {origin}  "
              f"{'LONGEST-2' if origin in top2 else ''}")
        print(f"    extra {short(extra)}")
EOF
mv -n "$OUT.tmp" "$OUT"; cat "$OUT"
fi
```

## 4. Domande aperte

1. **Cosa fa T1.6 e su quali metodi è stato lanciato P7.a.**
   - **T1.6** (ADR-018, `src/bench/attacks/t1/snake_case.py`) porta i nomi in `snake_case`, come
     chiede PEP 8. Vale solo per Python, non usa l'RNG e ha due punti:
     - `convert` (`9ad68973c317`): tutto in `snake_case`, costanti di modulo comprese;
     - `keep` (`8e1e8882fb91`): i nomi di modulo in `ALL_CAPS` restano.

     È stato aggiunto per il canale dei nomi V1 di `mcss` (2 bit nello stile del nome).
   - **P7.a** (ADR-018 §5, decisione del 26 novembre 2026) è T1.6 e T2.5 sul test di L1 e L2, solo
     python, per **ACW, STONE e MCGMark** (i metodi del proprietario `ivan`). SWEET e PromptMark
     (account di Simone) restano fuori.
   - Stato: STONE e MCGMark finiti; ACW rilanciato il 27–28 novembre, ma `DONE` non ancora
     verificato (§2).
   - Da confermare con il controllo di completezza.
2. **Il token scaduto ha toccato rilevazioni di ACW fuori da P7.a?**
   - **Risposta probabile: no.** Il canarino ferma il worker all'avvio (`FatalShimError`), quindi un
     job con il token scaduto non scrive risultati: lascia la cella mancante, non sbagliata. Inoltre
     l'ultimo lavoro di ACW prima di P7.a risulta finito il 13 novembre (L3, T2.4k).
   - **Da verificare** con due controlli in sola lettura, poi decidere:
     - la sezione 7 di `completezza_cinque.txt` (nessuna cella di ACW fuori da P7.a deve risultare
       mancante);
     - i job di ACW dopo il 13 novembre:

       ```bash
       sacct -X -u "$USER" -S 2026-11-14 --format=JobID,JobName%45,State,Start,End | grep -i acw
       ```

     Ogni job `FAILED` fuori da P7.a va guardato nel suo log SLURM.
3. **Quota di inserimento di MCGMark su L1: 10,0% e 3,8% (zip dall'account di Simone) contro 8% e 1,7% (note).**
   - **Risolta: sono due misure diverse, non un errore.** L'utente ha confermato che lo zip
     dall'account di Simone riporta il test con le configurazioni congelate (0,0998 e 0,0377, n = 2676).
     **Nella tesi si usano questi valori.**

     | Numeri | Fonte | Insieme | Configurazione |
     |---|---|---|---|
     | 8,0% (46/576) e 1,7% (10/576) | `docs/audit/mcgmark.md` §2.1; ripresi in `docs/da_lanciare/analisi_benchmark.md` | L1 **dev**, generazione dell'8 ottobre 2026 | default, prima dell'HPO e della patch 0004 degli outlier |
     | 0,0998 (Qwen, `697c5eb78b23`, S = 1,5) e 0,0377 (DeepSeek, `6c30ca18a68e`, S = 3,0) | `docs/milestone_logs/M10_test_mcgmark_outliers.txt` | L1 **test** (n = 2676) | congelata (`e4a6d39`) |

   - Nello stesso log, la configurazione di prima degli outlier (`9df49d3f4f38`) dà 0,1136 per Qwen
     e 0,0217 per DeepSeek. Il TPR@1% sul test è 0,0811 per Qwen e 0,0340 per DeepSeek.
4. **Analisi del benchmark** (`docs/da_lanciare/analisi_benchmark.md`, ad `a64f4ea`).
   - **Già risolti sul ramo:**
     - bit corretti e lunghezza del messaggio per metodo;
     - capacità sulle celle con attacco;
     - `conditional_preservation` per ogni `CodeEmbedder` (ADR-017, `docs/modifiche_ramo.md` punti
       6–9);
     - T1.6 e T2.5 (ADR-018).
   - **Restano da dichiarare nella tesi, senza riesecuzioni:**
     - il TPR di MCGMark è una verifica a messaggio noto;
     - T2.1 con `b = all` colpisce più canali insieme;
     - i negativi non vengono attaccati;
     - il criterio dell'HPO non considera l'impercettibilità.
   - **Unico seguito pratico per i cinque:** il ricalcolo delle metriche di MCGMark (§3.2), che è
     necessario perché la tesi mostra la capacità sotto attacco di MCGMark.

## 5. Documenti e log

**Documenti chiave (nel repository):**

- `docs/modifiche_ramo.md`: modifiche del ramo rispetto a `main` ed effetto sui cinque metodi;
- `docs/da_fare_cluster.md`: comandi per il cluster (timeout di Sourcery, manifest senza commit,
  ricalcolo di MCGMark, cause dei `FAILED` di DeepSeek);
- `docs/provenienza_risultati.md`: commit dei risultati per metodo, livello e parte; celle senza
  commit (account di Simone); conteggi di D42;
- `docs/deviations.md` (D42, P6, P7.a), `docs/results_notes.md` (R1–R5), ADR-017, ADR-018 e
  ADR-019 in `docs/decisions/`, audit in `docs/audit/`;
- `docs/milestone_logs/`: log committati (`P6_*`, `P7a_*`, `M10_*`–`M13_*`), da non sovrascrivere
  mai.

**Log già prodotti sul cluster, in `$WMB/logs/`:**

- `provenance_five.txt`: commit dei manifest dei cinque metodi;
- `provenance_none.txt`: manifest senza commit, per job e account;
- `acw_l3_sourcery_timeouts.txt` e `acw_l3_sourcery_timeouts_v2.txt`: timeout di D42. Il primo
  ha saltato la rilevazione per un percorso sbagliato; vale il v2;
- `acw_l3_deepseek_failed.txt`: cause dei `FAILED` di DeepSeek su L3;
- `chains/loop_ivan.log` e i file di stato delle catene (`state_<owner>__<runner>.json`);
- da produrre: `completezza_cinque.txt` (§3.1).

## 6. Regole da ricordare

- Rispondere sempre in italiano. Mai `git push` né modifiche ai remote: il push lo fa l'utente. Solo
  commit locali.
- Nessun comando sul cluster da parte dell'agente: si scrivono comandi completi da copiare. Niente sul
  cluster senza approvazione.
- Prima della suite di test o di comandi pesanti in locale: avvisare e aspettare «vai». Un blocco alla
  volta, mai in parallelo.
- Mai sovrascrivere o cancellare log e artefatti committati. Le prove si fanno in una cartella
  temporanea.
- Non toccare `third_party/` né le patch esistenti. Ogni deviazione va in `docs/deviations.md`. Se
  qualcosa è ambiguo, chiedere.
- Non riaprire la questione della licenza di Sourcery. Quando la prova scade:
  `bash scripts/update_sourcery_token.sh`, poi `source ~/.bashrc`, poi rilanciare. Il token non va
  mai scritto in file del repository né stampato.
- Dopo il congelamento P6 (`p6-freeze`) non si cambiano il submodule `third_party/multimark`,
  `configs/freeze/posthoc.yaml` e `configs/attack/*.yaml`.
