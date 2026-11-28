# Modifiche del ramo `posthoc-multimark` rispetto a `main`

Confronto fra `main` (a64f4ea, 10 novembre 2026, punto di partenza del ramo; `main` non è andato
avanti) e `posthoc-multimark` (9740349, 25 novembre 2026): 92 commit. Il benchmark si descrive in
due parti:

- **Benchmark dei cinque metodi** (SWEET, ACW, STONE, PromptMark, MCGMark): `main` a a64f4ea più
  D42 su ACW. Il codice che corrisponde esattamente è **ed59236** (a64f4ea + il submodule
  `third_party/multimark`, non usato dai cinque, + D42).
- **Estensione per il nuovo metodo** (`mcss`, `multimark`): tutto il resto del ramo.

Colonna «Effetto sui cinque metodi»: **nessuno** = stessi valori e stesse celle; **additivo** =
righe di metriche nuove, valori esistenti identici; **comportamento** = un risultato può cambiare.
I valori identici sono verificati dai test di confronto con le metriche registrate prima delle
modifiche comuni (`tests/unit/test_metrics_compat.py`, commit 0d175f5, ADR-017). Le catene dei cinque
metodi (`bench.chains.all_steps`) sono identiche a quelle di `main`: 159 passi con le stesse chiavi,
override e dipendenze; il ramo aggiunge solo i 234 passi di `mcss` e `multimark`.

## Tabella

| # | Cambiamento | Parte | Effetto sui cinque metodi | Riferimento |
|---|---|---|---|---|
| 1 | ACW: limite di tempo sulle chiamate a Sourcery (lotto 20 min, poi campione per campione 10 min; oltre il limite `FAILED`, `sourcery timeout`) | Benchmark dei cinque metodi | **Comportamento**, solo ACW: i campioni che sforano sono `FAILED` (inserimento non riuscito o rilevazione `FAILED`); i lotti senza campioni lenti sono identici. Caso noto: L3 test, DeepSeek, 2 campioni da 24.717 caratteri | ed59236; `deviations.md` D42; `configs/report/annotations.yaml` |
| 2 | Submodule `third_party/multimark`, ambiente conda `posthoc`, `envs.yaml`, `apply_patches.sh` e `setup_submodules.sh` estesi a multimark (zero patch, `mcss` alias della stessa copia) | Estensione | Nessuno sui dati. `apply_patches.sh` senza argomenti prepara anche `build/patched/multimark` | 0768c4c, 63509a0, 7e9dd46, 3fc2a0f, 96c7abb |
| 3 | Doctor: 9 submodule invece di 8, 7 ambienti invece di 6, copia patchata condivisa per `mcss` | Estensione | Nessuno sui dati; **il doctor fallisce** se manca il submodule `third_party/multimark` o l'interprete `posthoc` (vedi «Avvisi per chi lavora sui cinque metodi») | 7e9dd46, 96c7abb |
| 4 | Provenienza e doctor: comandi git in sola lettura (`--no-optional-locks`) | Estensione (correzione tecnica, vale per tutti) | Nessuno sui dati: `git status` non riscrive più l'indice e non lascia `.git/index.lock` se il job viene ucciso. I manifest registrano anche il submodule e la copia di multimark | 9676777; `src/bench/store/manifest.py` |
| 5 | `KNOWN_METHODS` divisa in `BENCHMARK_METHODS` (i cinque, lista `methods` di default) e `THESIS_METHODS` (`mcss`, `multimark`); adapter, shim, configurazioni e risorse (CPU) dei due metodi | Estensione | Nessuno: la lista di default e le risorse dei cinque non cambiano | 7f8012a, e7a8664; `domain/enums.py`, `configs/resources/default.yaml` |
| 6 | `bits_correct` calcolato dall'adapter (`Detector.bits_correct`) invece che in `detect.py` | Estensione | Nessuno: il default è il codice di prima (MCGMark: `int(score)`) | beff6e6; ADR-017 §1 |
| 7 | Lunghezza del messaggio dall'adapter (`Detector.message_bits`, MCGMark 12) invece della costante `MESSAGE_BITS = 12` in `metrics.py`; metrica nuova `message_accuracy_detected` (esattezza del messaggio sui positivi rilevati) | Estensione | **Additivo**, solo MCGMark: `message_accuracy`, `bit_accuracy`, `embedding_success_rate` identici; riga nuova `message_accuracy_detected` nelle celle di metriche calcolate da 31743b0 in poi | 31743b0; ADR-017 §2 |
| 8 | Capacità anche sulle celle attaccate dei metodi multi-bit; `wrong_attribution_rate_<nome>` per i punteggi con registro di esche | Estensione | **Additivo**, solo MCGMark: nelle celle attaccate calcolate da ac86d80 in poi si aggiungono `message_accuracy`, `bit_accuracy`, `embedding_success_rate`, `message_accuracy_detected`. `wrong_attribution_rate_*` non si emette per nessuno dei cinque (nessun `best_<nome>` in `extra`) | ac86d80; ADR-017 §3 |
| 9 | `conditional_preservation` per ogni metodo post-hoc (`CodeEmbedder`) invece che per `method == "acw"` | Estensione | Nessuno: fra i cinque l'unico `CodeEmbedder` è ACW, valore identico | 3c8ac79; ADR-017 §4 |
| 10 | Sweep dell'HPO (`hpo.grid`): `metrics` per ogni candidato su dev; celle attaccate di dev nello sweep (`attack`, `execute`, `detect`, `metrics`), con il punto di default che diventa la cella di default; impercettibilità di dev per candidato | Estensione | Nessuno senza `hpo.grid`. Con `hpo.grid` su celle **attaccate di dev** il punto di default avrebbe la chiave della cella di default invece di quella con l'hash (per i cinque non è mai stato lanciato) | bd08748, a3b0455, ede6e88; ADR-017 §5–§7 |
| 11 | Opzioni nuove `hpo.points`, `attack_points`, `posthoc_datasets` | Estensione | Nessuno: default `null` = comportamento di prima | e52aef1, c3ebf6f; ADR-019 §5 |
| 12 | Attacco nuovo T1.6 (nomi in snake_case) | Estensione | Nessuno: non gira sui cinque (decisione dell'utente, P6) | cd0f98c; ADR-018 |
| 13 | Attacco nuovo T2.5 (diluizione con funzioni umane) e fase `function_pools`; caricamento delle raccolte degli attacchi rifattorizzato (`load_attack_pools`, `read_pools` in `check_attacks_canonical.py`) | Estensione | Nessuno: commenti e nomi si leggono come prima; la raccolta di funzioni è vuota per gli attacchi che non la chiedono. T2.5 non gira sui cinque | e52aef1 |
| 14 | Congelamento dei post-hoc senza `tune` (registro `configs/freeze/posthoc.yaml`, più configurazioni per (modello, linguaggio)); campi nuovi di `FreezeRecord` (`library_commit`, `library_patches_sha256`, `files_sha256`, `aggregated_sha256`, `label`) | Estensione | Nessuno: i campi nuovi hanno un default e i record dei cinque restano validi; `FreezeRecord.cell(modello, linguaggio)` restituisce la stessa cella di prima | c3ebf6f; ADR-019 §2 |
| 15 | Controllo sulla parte di test (`CellPlanner._posthoc`, `posthoc_freeze.plan_cells`) | Estensione | Nessuno: vale solo per `mcss` e `multimark`. Per i cinque resta la falla di `_selected` con `hpo.grid` sul test (documentata, non corretta per decisione dell'utente; nessun lancio del genere risulta dai log) | c3ebf6f; ADR-019 §3; `deviations.md` voce P6 |
| 16 | `metrics` sul test: scelta della cella congelata per `config_hash` quando il record ha più configurazioni; seconda chiave (`detect_k2`, `calibrate_k2`, `metrics_remark`) per configurazione | Estensione | Nessuno: con una sola configurazione per (modello, linguaggio) si prende l'unica cella, come prima; stessi controlli (soglia definitiva, `config_hash`, sha256 della soglia) | c3ebf6f; ADR-019 §2, §4 |
| 17 | Script: `report_metrics.py` (`--config`, soglia teorica `score >= 2`), `estimate_closing_cost.py --methods`, `check_test.py` e `freeze_method.py` estesi ai post-hoc, `report_aggregated.py`, `run_p6.sh` | Estensione | Nessuno: per i cinque gli script si comportano come prima (il rifiuto del repository «sporco» in `freeze_method.py` vale solo per i post-hoc) | b499905, c3ebf6f, 60c6a9d |
| 18 | Catene: passi di test di `mcss` e `multimark` (fermi fino al congelamento) | Estensione | Nessuno: i 159 passi dei cinque sono identici (verificato confrontando `all_steps` di a64f4ea e del ramo) | c3ebf6f |
| 19 | Report della tesi (28 novembre 2026): `scripts/report.py --with-p7a` (T1.6 e T2.5 di ACW, STONE e MCGMark su L1 e L2 in `metrics_long` e nella tabella `attacks_P7a`) e `--with-capacity` (colonne `message_accuracy`, `bit_accuracy`, `message_accuracy_detected` nelle tabelle degli attacchi, solo MCGMark; curve `capacity_by_sites_mcgmark_<livello>` con fasce a giri 1–23, 24–47, 48–95, 96+ e `capacity_by_loc_mcgmark_<livello>`); guardia: una cartella `--out` esistente si rifiuta; fasce e tabella della cella spostate da `scripts/report_capacity.py` a `bench.metrics.capacity_bands` | Estensione (report) | Nessuno sui dati; senza le opzioni il report è lo stesso byte per byte (test) | 075b7eb, e8aa553 |

## Avvisi per chi lavora sui cinque metodi

- **Doctor:** dopo il passaggio al ramo servono `git submodule update --init third_party/multimark`
  e `bash scripts/apply_patches.sh multimark`, altrimenti il doctor segnala il submodule e la copia
  mancanti; l'interprete dell'ambiente `posthoc` va trovato al percorso di `configs/envs/envs.yaml`.
- **Metriche di MCGMark:** le celle calcolate prima del 16 novembre 2026 (31743b0, ac86d80) non hanno
  `message_accuracy_detected` né la capacità sulle celle attaccate; quelle calcolate dopo sì. I valori
  delle metriche che esistevano già sono identici. Le tabelle del report possono quindi avere righe in
  più per alcune celle di MCGMark e non per altre, finché la fase `metrics` non si rifà.
  **Rifatta il 28 novembre 2026** sulle 98 celle di test della configurazione congelata (sezione
  «Ricalcolo delle metriche di MCGMark» qui sotto): le tabelle di MCGMark non sono più miste.
- **ACW con D42:** i campioni `sourcery timeout` si riportano per livello, modello e insieme quando
  ACW su L3 è finito (D42).
- **Provenienza:** i manifest scritti dal ramo hanno `repo_commit` di `posthoc-multimark`; per le fasi
  dei cinque metodi il codice usato è equivalente a ed59236 salvo le righe additive dei punti 7 e 8.
- **Opzioni nuove** (`hpo.points`, `attack_points`, `posthoc_datasets`): non servono ai cinque; con il
  default `null` non cambiano niente.

## Ricalcolo delle metriche di MCGMark (28 novembre 2026)

Risultati dei cinque metodi ricalcolati sul ramo; il codice è quello della tabella (punti 7 e 8,
ADR-017), nessun commit di codice nuovo.

- **Perché.** Le metriche di test di MCGMark di M10 e M11 (fino al 5 novembre) non avevano
  `message_accuracy_detected` né la capacità sulle celle con attacco; quelle di P7.a (T1.6, T2.5,
  calcolate sul ramo) sì. La tesi riporta la capacità sotto attacco di MCGMark, quindi le tabelle
  non potevano restare miste.
- **Cosa.** Solo la fase `metrics` con `force=true`, `methods=[mcgmark]`, `splits=[test]`,
  `languages=[python]`, `detection.threshold_levels=[L1,L2]`, configurazione congelata e4a6d39
  (Qwen `697c5eb78b23`, DeepSeek `6c30ca18a68e`). Tre invii, 98 celle (dry run: 6, 52, 40):
  - senza attacco, L1, L2 e L3: 6 celle;
  - L1 con T1.1, T1.2, T1.3, T1.4, T1.5, T2.1, T2.2, T2.4, T3.1, T4.1: 52 celle;
  - L2 e L3 con T2.4k: 40 celle.
- **Cosa non si è toccato.** Calibrazione, scelta, congelamento, rilevazioni ed esecuzioni (solo
  letti). Non ricalcolate: le celle di P7.a (avevano già le righe nuove), `metrics_remark` di T3.1
  (k2) e `metrics_attack_control` di T2.2 (D34): queste due fasi non calcolano metriche di capacità,
  quindi un ricalcolo non aggiungerebbe niente.
- **Copia di sicurezza.** `$WMB/backup_metrics_mcgmark_20261128`, creata alle 11:26:05: tutte le
  cartelle `mcgmark` sotto `metrics_L1L2/` (anche `attacked/`, `attack_control/` e `remark/`), 312
  file nello store e 312 nella copia. Il comando di `docs/handoff.md` §3.2 (`cp -a
  metrics_L1L2/mcgmark`) avrebbe copiato solo le 6 celle senza attacco: le celle con attacco stanno in
  `metrics_L1L2/attacked/<attacco>/<hash>/mcgmark/`. È stato usato il comando corretto.
- **Job SLURM** (tutti `COMPLETED`, account di Ivan): 829062 (T2.4k, partito alle 11:26:34, dopo
  la copia), 829066, 829067 e 829068 (le celle senza attacco e gli attacchi di L1), 829069.
  **Doppio invio di T2.4k:** 829069 è un secondo invio delle stesse 40 celle di T2.4k, partito alle
  11:37:16, dopo la fine di 829062 (nessuna sovrapposizione). Con `force=true` ha riscritto le 40
  celle già riscritte da 829062; i valori finali sono quelli di 829069 e il controllo v2 li trova
  identici a quelli di prima per le metriche che esistevano già.
- **Controlli.**
  - `$WMB/logs/mcgmark_metrics_recompute_check.txt` (11:26:38): **non valido**. È stato lanciato
    prima della fine dei job, quindi ha letto celle non ancora riscritte o in corso di
    riscrittura. Resta nei log com'è e non si usa.
  - `$WMB/logs/mcgmark_metrics_recompute_check_v2.txt`: controllo valido, a job finiti. 156 file di
    metriche confrontati con la copia, **0 valori cambiati**; `message_accuracy_detected` nuova in
    98 file (6 + 52 + 40); `message_accuracy`, `bit_accuracy` ed `embedding_success_rate` nuove in
    92 file (le celle con attacco).
- **Effetto.** Additivo: stesse celle, stessi percorsi, valori esistenti identici, righe nuove.
  I manifest delle 98 celle hanno ora il `repo_commit` del ramo (`docs/provenienza_risultati.md`).

## Test del report (28 novembre 2026)

- **075b7eb**, log `$WMB/logs/P7_report_tests_075b7eb.txt.tmp`. Il log è rimasto `.tmp` per la
  guardia del comando (exit 1: il file prende il nome definitivo solo se tutto passa) e non si
  rinomina: vale con questo nome. Esito: `ruff check` e `ruff format --check` puliti sui 6 file
  toccati; `mypy --strict` pulito (134 file); `pytest tests/unit` 1029 passati, 1 saltato,
  **1 fallito**: `test_attack_tools.py::test_external_tools[T1.2-javascript-eslint]`, che non
  riguarda il report (nessun file degli attacchi è cambiato fra 5d9184b e 075b7eb) ed è un
  difetto preesistente di T1.2 su JavaScript: con ESLint 9.14 e le regole `recommended` nessuna
  correzione si applica (anche sulle 164 canoniche, `M10_canonical_attacks.txt`: UNCHANGED
  164/164). Inoltre 16 `DeprecationWarning` di NumPy da matplotlib in `test_report.py`.
- **e8aa553**: barre d'errore passate come liste (niente avviso con una sola barra) e un test
  con gli avvisi trattati come errori. Log atteso: `$WMB/logs/P7_report_tests_e8aa553.txt`.
