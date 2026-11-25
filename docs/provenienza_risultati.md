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
D42 (D42 non cambia i lotti senza campioni lenti). Il numero dei campioni `sourcery timeout` è
ancora da contare e da riportare (D42).

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

**Celle con commit non registrato.** Riguardano soprattutto SWEET e PromptMark (i metodi di Simone),
più alcune celle di STONE e MCGMark. Tutte sono state scritte entro l'8 novembre, prima che il ramo
esistesse (primo commit del ramo: 12 novembre), quindi non vengono dal codice del ramo. Il commit
esatto però non è registrato. Causa probabile, da verificare: il processo girava con l'account
dell'altro utente sul clone condiviso e git ha rifiutato il repository per proprietà diversa
(«dubious ownership»: `rev-parse` esce con un errore e la provenienza registra `None`;
`scripts/oracle_method.sh` aggira lo stesso problema con `safe.directory`). Il manifest registra
comunque il job SLURM, il nodo e i commit delle copie patchate dei metodi, letti da file e non con git.
