# Provenienza dei risultati dei cinque metodi

Da `$WMB/logs/provenance_five.txt` (25 novembre 2026): per ogni artefatto completo dei cinque metodi
(SWEET, ACW, STONE, PromptMark, MCGMark) il `repo_commit` registrato nel manifest
(`provenance.repo_commit`), raggruppato per metodo, livello, parte, fase e condizione (con o senza
attacco). Versione del codice:

- **main**: commit antenato di a64f4ea (la linea di `main` fino al punto di partenza del ramo);
- **main+D42**: ed59236 (a64f4ea + il submodule `third_party/multimark`, non usato dai cinque, + D42);
- **ramo**: commit di `posthoc-multimark` successivo a ed59236;
- **non registrato**: manifest con `repo_commit` vuoto (`none`): il comando git della provenienza è
  fallito in quel processo, quindi mancano sia il commit sia lo stato «dirty» (vedi sotto).

Le date sono quelle di fine scrittura degli artefatti (`finished_at`). «Commit misti» vuol dire che
gli artefatti del gruppo sono stati scritti con commit diversi: di solito celle diverse (modelli,
punti dell'attacco, configurazioni) calcolate in giorni diversi, non la stessa cella con due versioni
(ogni file ha un solo manifest).

## Tabella

| Metodo | Livello | Parte | Versione del codice | Date | Commit misti | Non registrato | Note |
|---|---|---|---|---|---|---|---|
| ACW | L1+L2 | dev (calibrazione, selezione, congelamento) | main | 12–18 ott; k2 28 ott | sì (f67b5d9, 94a7717, 6232940) | no | congelamento 9e4d1a1 (18 ott) |
| ACW | L1 | dev | main | 10–13 ott; k2 28 ott | sì | no | |
| ACW | L2 | dev | main | 16–17 ott; k2 28 ott | sì | no | |
| ACW | L1 | test | main | 18–29 ott | sì (per attacco e fase) | no | |
| ACW | L2 | test | main (9b3c6b4) | 5 nov | no | no | |
| ACW | L3 | test | **main + main+D42 + ramo** | 5–13 nov | **sì** | no | inserimento: un file a 9b3c6b4 (5 nov, prima del blocco) e uno a ed59236 (12 nov, con D42); execute, detect e metrics senza attacco a ed59236; T2.4k: attack ed execute a ed59236, detect a ed59236 (16 file) e al ramo (a8bd0fe, 843f569: 4 file), metrics al ramo (843f569: 20 file). Il codice del ramo fino a 843f569 è identico a ed59236 per i cinque metodi (vedi sotto) |
| MCGMark | L1+L2 | dev (calibrazione, selezione, congelamento) | main | 10–22 ott; k2 27 ott | sì (prima e dopo la patch 0004 degli outlier) | no | congelamenti 60f4f65 (17 ott) e e4a6d39 (22 ott, outlier); vale il secondo |
| MCGMark | L1 | dev | main | 7–22 ott; k2 27 ott | sì | no | |
| MCGMark | L2 | dev | main + non registrato | 15–22 ott; k2 27 ott | sì | detect 10 file, execute 4 file (16 ott) | |
| MCGMark | L1 | test | main + non registrato | 17–29 ott | sì (due configurazioni: prima e dopo gli outlier) | T4.1 (attack, execute, detect, metrics: 28 ott), execute senza attacco 4 file (18 ott), controllo di T2.2 (26 ott) | |
| MCGMark | L2 | test | main (205d8d0) | 4 nov | no | no | |
| MCGMark | L3 | test | main (9b3c6b4; gemella anche 205d8d0) | 4–5 nov | solo la gemella | no | |
| PromptMark | L1+L2 | dev (calibrazione, selezione, congelamento) | main + non registrato | 10–16 ott; k2 27 ott | sì | calibrate 54 file e tune 4 file (14 ott) | congelamento 23a9a44 (16 ott) |
| PromptMark | L1 | dev | main + non registrato | 7–13 ott | sì | watermark 60, execute 54, detect 162 file (12–13 ott); detect_k2 (27 ott) | |
| PromptMark | L2 | dev | quasi tutto non registrato (watermark anche main 07521df) | 14–16 ott; k2 27 ott | sì | detect 224, execute 56, watermark 6 file; detect_k2 | |
| PromptMark | L1 | test | main + non registrato | 18–29 ott | sì | watermark senza attacco (18 ott), T2.1 (26 ott), T3.1 (29 ott), detect_k2 senza attacco (28 ott) | |
| PromptMark | L2 | test | main (205d8d0) | 2–3 nov | no | no | |
| PromptMark | L3 | test | main (205d8d0) | 4 nov | no | no | |
| STONE | L1+L2 | dev (calibrazione, selezione, congelamento) | main | 12–16 ott; k2 27 ott | sì | no | congelamento 23a9a44 (16 ott) |
| STONE | L1 | dev | main | 5–13 ott; k2 27 ott | sì | no | |
| STONE | L2 | dev | main + non registrato | 14–15 ott; k2 27 ott | sì | watermark 108 file (14–15 ott) | |
| STONE | L1 | test | main + non registrato | 17–29 ott | sì | watermark senza attacco 6 file (17 ott), T2.4 (26 ott), metà di T3.1 (27 ott), T4.1 (28 ott), impercettibilità (perplexity, logreg, unixcoder, metrics: 29 ott) | |
| STONE | L2 | test | main (205d8d0) | 2–4 nov | no | no | |
| STONE | L3 | test | main (205d8d0) | 2–4 nov | no | no | |
| SWEET | L1+L2 | dev (calibrazione, selezione, congelamento) | main + non registrato | 10 ott–4 nov | sì | calibrate 500 file (28 ott–3 nov), tune 16 file, freeze e freeze_k2 (3–4 nov) | **commit del congelamento non registrato** |
| SWEET | L1 | dev | main + non registrato | 6 ott–2 nov | sì | detect 789, execute 492, watermark 492 file (12 ott–2 nov) | |
| SWEET | L2 | dev | main + non registrato | 16 ott–3 nov | sì | detect 1.736, execute 256, watermark 256 file | |
| SWEET | L1 | test | **tutto non registrato** | 3–4 nov | — | tutto | |
| SWEET | L2 | test | **tutto non registrato** | 4–7 nov | — | tutto | |
| SWEET | L3 | test | **tutto non registrato** | 4–8 nov | — | tutto | |

## Letture

**ACW su L3 è completo.** Ha gli stessi artefatti completi di MCGMark e PromptMark su L3, per i due
modelli: senza attacco watermark 2, execute 2, detect 6, metrics 2; con T2.4k (10 punti × 2 modelli)
attack, execute, detect e metrics 20 ciascuno. Le celle di DeepSeek sono state rifatte con D42 dopo
il blocco del 5–12 novembre; l'inserimento di Qwen era già finito il 5 novembre a 9b3c6b4, senza
D42 (D42 non cambia i lotti senza campioni lenti).

**Campioni `sourcery timeout` di ACW su L3 test (D42)**: inserimento da
`$WMB/logs/acw_l3_sourcery_timeouts.txt`, rilevazione da `acw_l3_sourcery_timeouts_v2.txt` (25
novembre 2026; il primo log aveva saltato la rilevazione per un errore nel percorso del manifest).
Tutte le tabelle sono complete.

| Modello | Insieme | Fase | Campioni | `FAILED` | `sourcery timeout` |
|---|---|---|---|---|---|
| DeepSeek-Coder 6.7B | positivi | inserimento | 3.564 | 6 | 2 |
| DeepSeek-Coder 6.7B | positivi | rilevazione | 3.564 | 5 | 2 |
| DeepSeek-Coder 6.7B | negativi umani (`neg_human`) | rilevazione | 22.267 | 0 | 0 |
| DeepSeek-Coder 6.7B | negativi LLM (`neg_llm`, baseline non marcata) | rilevazione | 3.564 | 5 | 2 |
| DeepSeek-Coder 6.7B | attacchi T2.4k (10 punti × 3.564) | rilevazione | 35.640 | 50 (5 per punto) | 20 (2 per punto) |
| Qwen2.5-Coder 7B | positivi | inserimento | 3.564 | 0 | 0 |
| Qwen2.5-Coder 7B | positivi | rilevazione | 3.564 | 0 | 0 |
| Qwen2.5-Coder 7B | negativi umani + LLM | rilevazione | 22.267 + 3.564 | 0 | 0 |
| Qwen2.5-Coder 7B | attacchi T2.4k (10 punti × 3.564) | rilevazione | 35.640 | 0 | 0 |

I timeout ci sono solo con DeepSeek, sempre 2 per tabella, e mai sui negativi umani.

**Cause dei `FAILED` di DeepSeek**, da `$WMB/logs/acw_l3_deepseek_failed.txt` (25 novembre 2026:
risultati del worker dell'inserimento e tabelle della rilevazione; tutti i campioni coinvolti sono
di CodeSearchNet):

| Campione della baseline | Caratteri | Inserimento | Rilevazione (positivi, baseline non marcata, T2.4k) |
|---|---|---|---|
| dde8709c0fb0418f71eb33f023e6f0e3 | 24.717 | `sourcery timeout` (D42) | `sourcery timeout` |
| a8181923305c9af7d8adda97de225390 | 24.717 | `sourcery timeout` (D42) | `sourcery timeout` |
| fd443b274a549312d00314bbbee77638 | 0 | `empty code` | `FAILED`: nessuna delle 30 regole cambia il codice (`joint_unchanged`) |
| 590a0d10b7b500aaf0c80b79bfdf0246 | 0 | `empty code` | come sopra |
| 5f0acf25b48fe82e46d05c7f86c6f976 | 0 | `empty code` | come sopra |
| 9ed92ac4974029e0524555cef54c3786 | 187 | `no transformation applied` (nessuna regola applicabile) | rilevato senza errori |

- **I due timeout sono i due campioni lunghi di D42.** Gli identificativi coincidono: sono i due
  codici più lunghi della baseline di DeepSeek (24.717 caratteri ciascuno; il terzo ne ha 5.263),
  nell'inserimento e nella rilevazione dei positivi, della baseline non marcata e del primo punto di
  T2.4k (1504a65ed908). Per gli altri 9 punti di T2.4k gli identificativi non sono stati letti; i
  conteggi (5 `FAILED`, 2 timeout per punto) sono gli stessi.
- **Le 3 rilevazioni `FAILED` per tabella che non sono timeout** sono i 3 campioni con codice vuoto,
  gli stessi in ogni tabella.
- **I 4 inserimenti `FAILED` che non sono timeout:** 3 campioni con codice vuoto (la generazione della
  baseline non ha prodotto codice; non dipende da ACW) e 1 campione corto (187 caratteri) su cui
  nessuna delle 30 regole di Sourcery è applicabile.
- **Inserimento riuscito di ACW su L3 test:** Qwen 3.564 su 3.564 (100%); DeepSeek 3.558 su 3.564
  (99,83%): 2 per il limite di tempo di D42, 3 per codice vuoto della baseline, 1 senza regole
  applicabili.

**Risultati calcolati sul ramo.** Solo ACW su L3, T2.4k: 4 file di rilevazione (a8bd0fe, 843f569) e
le 20 celle di metriche (843f569), tutti del 13 novembre. Fra ed59236 e 843f569 il codice cambia solo
in `src/bench/doctor.py` (una riga, controllo degli ambienti), `configs/envs/envs.yaml` (ambiente
`posthoc`), `configs/method/mcss.yaml` e `multimark.yaml` (fuori dal catalogo) e
`patches/multimark/README.md`: niente che `detect` o `metrics` di ACW leggano, quindi i risultati
sono quelli della versione main+D42. Le modifiche comuni di ADR-017 (16 novembre in poi) sono tutte
successive: nessun risultato dei cinque metodi le usa. Per lo stesso motivo **nessuna** cella di
MCGMark ha ancora `message_accuracy_detected` né la capacità sulle celle attaccate.

**Risultati su commit di main precedenti ad a64f4ea.** Tutti gli altri risultati con commit
registrato sono su commit di `main` fra il 5 ottobre e il 5 novembre. Fra 205d8d0 (2 novembre) e
a64f4ea cambiano solo `chains.py`, `report.py` e `configs/report/annotations.yaml` (catene e report,
nessun calcolo), quindi L2 e L3 test sono equivalenti al codice di a64f4ea. Le celle di ottobre (dev
e L1 test) seguono le versioni del benchmark delle milestone M7–M12, con le correzioni e le
riesecuzioni registrate in `docs/deviations.md` e nei log delle milestone.

**Celle «dirty».** Nessun artefatto con commit registrato è stato scritto con modifiche al codice non
committate (`repo_dirty` falso ovunque; i file di documentazione e i log non contano, come da
`NON_CODE_PREFIXES`). Per le celle **non registrate** lo stato è **sconosciuto**, non pulito:
`repo_dirty` è vuoto quanto `repo_commit`, e la colonna «dirty 0» del log le conta come pulite solo
perché il conteggio tratta il valore vuoto come falso.

**Celle con commit non registrato** (da `$WMB/logs/provenance_none.txt`, 25 novembre 2026). Sono
7.172 file scritti da 327 job SLURM fra il 3 ottobre e l'8 novembre, **tutti lanciati dall'account
di Simone**:

| Metodo | File senza commit |
|---|---|
| SWEET | 6.235 |
| PromptMark | 674 |
| STONE | 217 |
| MCGMark | 38 |
| nessun metodo (baseline di L1 dev e test, 3 ottobre; perplessità della baseline, 29 ottobre e 4 novembre) | 8 |

Nessuno viene dal codice del ramo, che comincia il 12 novembre. Lettura:

- **Causa.** Nessun job dell'account di Ivan ha il commit vuoto, e tutti i job senza commit sono
  dell'account di Simone. Simone ha confermato che quei job li ha lanciati lui dal clone di Ivan sul
  cluster. Un processo che lavora su un clone di un altro utente si vede rifiutare il repository da
  git per proprietà diversa («dubious ownership»): `rev-parse` esce con un errore e la provenienza
  registra `None` (`scripts/oracle_method.sh` aggira lo stesso problema con `safe.directory`).
- **Librerie dei metodi.** I commit delle copie patchate registrati nei manifest (letti da file,
  non con git) sono quelli dei submodule di `main`, fissati il 1° ottobre (abf71a4) e mai cambiati
  fino ad a64f4ea: SWEET 853b47eb064c, PromptMark c04c8f61db1f, STONE bb5d809c0c49, MCGMark
  eefa27b68747.
- **Codice del benchmark.** Simone non modificava né committava il codice: i commit li faceva solo
  Ivan. Il codice di queste celle è quindi quello del clone di Ivan in quel momento, cioè lo stesso
  dei job di Ivan negli stessi giorni (per esempio 205d8d0 e 9b3c6b4 fra il 2 e il 5 novembre,
  quando sono state scritte le celle di test di SWEET). Il commit esatto di ogni cella non è
  registrato.
- **«Dirty».** Non registrato. Però (a) Simone non modificava il codice e (b) tutti i job di Ivan
  degli stessi giorni, sullo stesso clone, risultano puliti. È quindi molto probabile che anche i
  job di Simone siano partiti da codice pulito, senza che lo si possa verificare cella per cella.

Da dichiarare nella tesi: per queste celle la provenienza registrata comprende il job, il nodo, le
date e il commit della libreria del metodo, ma non il commit del benchmark.
