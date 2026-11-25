# Da fare sul cluster

Comandi pronti, dalla radice di wm-bench sul nodo di accesso (`cd ~/wm-bench`, ramo
`posthoc-multimark` aggiornato). I primi due sono in **sola lettura**: girano con `srun` su un nodo
di calcolo (2 CPU, niente GPU) e scrivono solo il loro log in `$WMB/logs/`, che **non si
sovrascrive** (se esiste già, il comando si salta). Il terzo riscrive delle metriche ed è da fare
più avanti.

```bash
WMB=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 2 --mem 8G -t 60)
```

## 1. ACW su L3: campioni `sourcery timeout` (D42)

D42 chiede di riportare, per livello, modello e insieme, i campioni finiti in `sourcery timeout`.
Il comando conta:

- **rilevazione:** dalle tabelle finali `detection/acw/<modello>/<cfg>/<soggetto>/L3_python_test.parquet`
  (righe, `FAILED`, `extra.sourcery_timeout`), per soggetto (positivi, `neg_human`, `neg_llm`,
  `attacked/T2.4k/<punto>`) e in sintesi per insieme (positivi, negativi, attacchi);
- **inserimento:** dalla tabella finale `watermarked/acw/<modello>/<cfg>/L3_python_test.parquet`
  (conteggi di `embed_status`; la tabella non ha il motivo) e dai risultati del worker
  `_runs/watermark/acw/<modello>/<cfg>/L3_python_test/**/results.jsonl` (un risultato per item,
  l'ultimo scritto), dove il timeout è `extra.sourcery_timeout` o l'errore `sourcery timeout`.
  L'attacco T2.4k (troncamento) non chiama Sourcery: i suoi timeout possono esserci solo nella
  rilevazione.

Il primo giro (`acw_l3_sourcery_timeouts.txt`, 25 novembre 2026) cercava il manifest al percorso
sbagliato (`<file>.manifest.json` invece di `<file>.parquet.manifest.json`) e ha saltato tutte le
tabelle della rilevazione come incomplete; i conteggi dell'inserimento erano giusti. Il comando
sotto è corretto e scrive `acw_l3_sourcery_timeouts_v2.txt` (il primo log resta com'è).

```bash
OUT="$WMB/logs/acw_l3_sourcery_timeouts_v2.txt"
if [ -e "$OUT" ]; then echo "[SKIP] $OUT exists (not overwritten)"; else
"${SRUN[@]}" "$WMB/bench-core/bin/python" - "$WMB/artifacts" > "$OUT.tmp" <<'EOF'
import glob, json, os, sys
from collections import Counter
import pandas as pd

root = sys.argv[1]
SUFFIX = "L3_python_test"


def complete(path):
    man = path + ".manifest.json"  # <file>.parquet.manifest.json (ArtifactRef.manifest_path)
    try:
        with open(man) as h:
            return json.load(h).get("status") == "complete"
    except (OSError, ValueError):
        return False


def is_timeout(extra, error=None):
    return bool((extra or {}).get("sourcery_timeout")) or error == "sourcery timeout"


print("# ACW, L3 test: sourcery timeout (D42)")
print("## detection (final tables)")
print(f"{'model':<22}{'cfg':<14}{'subject':<40}{'rows':>7}{'FAILED':>8}{'timeout':>9}")
summary = Counter()
base = f"{root}/detection/acw"
for path in sorted(glob.glob(f"{base}/*/*/**/{SUFFIX}.parquet", recursive=True)):
    model, cfg, *subj = os.path.relpath(path, base).split(os.sep)[:-1]
    subject = "/".join(subj)
    if not complete(path):
        print(f"{model:<22}{cfg:<14}{subject:<40}  not complete (skipped)")
        continue
    t = pd.read_parquet(path, columns=["status", "extra_json"])
    n_to = sum(is_timeout(json.loads(x)) for x in t["extra_json"] if isinstance(x, str) and x)
    n_failed = int((t["status"] == "FAILED").sum())
    part = ("positives" if subject == "positives"
            else "attacks" if subject.startswith("attacked") else "negatives")
    for key, value in (("rows", len(t)), ("FAILED", n_failed), ("timeout", n_to)):
        summary[(model, part, key)] += value
    print(f"{model:<22}{cfg:<14}{subject:<40}{len(t):>7}{n_failed:>8}{n_to:>9}")

print("## detection by model and set")
for model in sorted({m for m, _, _ in summary}):
    for part in ("positives", "negatives", "attacks"):
        if (model, part, "rows") in summary:
            print(f"  {model:<22}{part:<11} rows {summary[(model, part, 'rows')]:>6}  "
                  f"FAILED {summary[(model, part, 'FAILED')]:>5}  "
                  f"sourcery timeout {summary[(model, part, 'timeout')]:>4}")

print("## embedding (final table + worker results)")
base = f"{root}/watermarked/acw"
for path in sorted(glob.glob(f"{base}/*/*/{SUFFIX}.parquet")):
    model, cfg = os.path.relpath(path, base).split(os.sep)[:2]
    t = pd.read_parquet(path, columns=["embed_status"])
    counts = dict(Counter(t["embed_status"].fillna("None")))
    items = {}
    pattern = f"{root}/_runs/watermark/acw/{model}/{cfg}/{SUFFIX}/**/results.jsonl"
    for res in sorted(glob.glob(pattern, recursive=True)):
        with open(res) as h:
            for line in h:
                if line.strip():
                    r = json.loads(line)
                    items[r.get("item_id")] = r
    n_to = sum(is_timeout(r.get("extra"), r.get("error")) for r in items.values())
    state = "" if complete(path) else "  (table not complete)"
    print(f"  {model:<22}{cfg:<14}rows {len(t)}  embed_status {counts}  worker items "
          f"{len(items) or 'none found'}  sourcery timeout {n_to}{state}")
EOF
mv -n "$OUT.tmp" "$OUT"; cat "$OUT"
fi
```

## 2. Manifest senza commit registrato (account, job, nodo, librerie)

Per tutti i manifest completi con `provenance.repo_commit` vuoto (soprattutto SWEET e PromptMark,
alcune celle di STONE e MCGMark; `docs/provenienza_risultati.md`): una riga per (metodo, job SLURM)
con l'account che ha lanciato il job e la data di partenza (da `sacct`), il nodo, il commit della
copia patchata del metodo (`patched_source_commits`, letto da file e non con git), il numero di
file, le date e i gruppi (livello/parte/fase); in fondo il riepilogo per metodo e account. Serve a
verificare l'ipotesi «job lanciati dall'altro account sul clone condiviso, git rifiutato per
proprietà diversa». Se `sacct` non risponde dal nodo di calcolo, la colonna dell'account resta `?`:
in quel caso basta rilanciare solo `sacct -n -X -P -j <job,...> -o JobID,User,Start` dal nodo di
accesso con i job stampati.

```bash
OUT="$WMB/logs/provenance_none.txt"
if [ -e "$OUT" ]; then echo "[SKIP] $OUT exists (not overwritten)"; else
"${SRUN[@]}" "$WMB/bench-core/bin/python" - "$WMB/artifacts" > "$OUT.tmp" <<'EOF'
import json, os, subprocess, sys
from collections import Counter, defaultdict

root = sys.argv[1]
SKIP = {"_slurm", "_hydra", "_runs", "tmp"}
jobs = defaultdict(lambda: {"files": 0, "first": "", "last": "", "nodes": set(),
                            "libs": set(), "groups": set()})
for d, dirs, files in os.walk(root):
    dirs[:] = [x for x in dirs if x not in SKIP]
    for f in files:
        if not f.endswith(".manifest.json"):
            continue
        try:
            with open(os.path.join(d, f)) as h:
                m = json.load(h)
        except (OSError, ValueError):
            continue
        p = m.get("provenance") or {}
        if m.get("status") != "complete" or p.get("repo_commit"):
            continue
        cell = m.get("cell") or {}
        method = cell.get("method") or "-"
        j = jobs[(method, str(p.get("slurm_job_id")))]
        t = m.get("finished_at") or ""
        j["files"] += 1
        j["first"] = min(j["first"] or t, t)
        j["last"] = max(j["last"], t)
        j["nodes"].add(p.get("node") or "?")
        j["libs"].add((p.get("patched_source_commits") or {}).get(method, "-")[:12])
        j["groups"].add(f"{cell.get('level') or '-'}/{cell.get('split') or '-'}/{m.get('stage')}")

ids = sorted({job.split("_")[0] for _, job in jobs if job not in ("None", "")})
users = {}
for i in range(0, len(ids), 200):
    try:
        out = subprocess.run(["sacct", "-n", "-X", "-P", "-j", ",".join(ids[i:i + 200]),
                              "-o", "JobID,User,Start"], capture_output=True, text=True).stdout
    except OSError:  # sacct non disponibile sul nodo: account «?»
        out = ""
    for line in out.splitlines():
        jid, user, start = (line.split("|") + ["", "", ""])[:3]
        users[jid.split("_")[0]] = (user or "?", start[:10])

print("# manifests without repo_commit: one line per (method, SLURM job)")
print(f"{'method':<11}{'job':<12}{'user':<14}{'start':<12}{'nodes':<16}{'library':<14}"
      f"{'files':>6}  {'finished':<23}groups")
summary = Counter()
for (method, job), j in sorted(jobs.items(), key=lambda kv: (kv[0][0], kv[1]["first"])):
    user, start = users.get(job.split("_")[0], ("?", "?"))
    summary[(method, user)] += j["files"]
    print(f"{method:<11}{job:<12}{user:<14}{start:<12}{','.join(sorted(j['nodes'])):<16}"
          f"{','.join(sorted(j['libs'])):<14}{j['files']:>6}  "
          f"{j['first'][:10]}..{j['last'][:10]}  {' '.join(sorted(j['groups']))}")
print()
print("## files without repo_commit by method and account")
for (method, user), n in sorted(summary.items()):
    print(f"  {method:<11}{user:<14}{n:>7}")
print(f"## {len(jobs)} (method, job) pairs, {sum(summary.values())} files")
EOF
mv -n "$OUT.tmp" "$OUT"; tail -n 20 "$OUT"
fi
```

## 3. MCGMark: ricalcolo delle metriche (più avanti)

Dalla provenienza (`docs/provenienza_risultati.md`) **nessuna** cella di MCGMark ha
`message_accuracy_detected` né la capacità sulle celle attaccate: sono tutte calcolate entro il 5
novembre, le misure sono arrivate con ADR-017 dal 16 novembre (31743b0, ac86d80). Basta la fase
`metrics` con `force=true`:

- scrive solo `metrics_L1L2/mcgmark/...` (stesse celle, stessi percorsi) e legge rilevazioni,
  esecuzioni, soglie e `freeze/mcgmark.json` senza modificarli;
- non rilancia `calibrate`, `tune` o `freeze`: soglie, selezione e congelamento restano quelli;
- sul test controlla soglia definitiva, `config_hash` congelato e sha256 della soglia rispetto al
  congelamento: se qualcosa non torna, la cella fallisce invece di scrivere;
- le celle della configurazione di MCGMark prima della patch degli outlier (congelamento 60f4f65)
  non si pianificano e restano come sono: si ricalcola solo la configurazione congelata (e4a6d39).

Prima del lancio:

1. il loop delle catene deve girare solo con `--only mcss/ --only multimark/` (mentre una cella si
   riscrive il suo manifest manca per un momento, e un loop senza filtro potrebbe rilanciare il
   passo di MCGMark);
2. copia di sicurezza delle metriche vecchie (`--force` le sostituisce);
3. posti liberi in coda per 4 job (`squeue -u $USER`).

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

Cosa producono: le stesse celle riscritte, con in più `message_accuracy_detected` ovunque e
`message_accuracy`, `bit_accuracy`, `embedding_success_rate`, `message_accuracy_detected` sulle
celle attaccate; i valori delle metriche già presenti restano identici (confrontabili con la
copia). Dopo:

- rigenerare il report (`scripts/report.py`) in una cartella nuova;
- aggiornare `docs/provenienza_risultati.md`: le metriche di test di MCGMark avranno il commit del
  ramo (misure dell'estensione, ADR-017), il resto della catena di MCGMark resta su `main`.

Una cella di un attacco che MCGMark non ha mai girato fallirebbe per input mancante senza fermare le
altre: se il dry run ne mostra, togliere quell'attacco dalla lista.
