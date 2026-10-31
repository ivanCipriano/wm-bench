# Piano di chiusura: M13, M14 e SWEET in un unico blocco

**Eccezione alla regola "una milestone alla volta"** (CLAUDE.md, SPEC §0): autorizzata dall'utente il
30 ottobre 2026 per M13, M14 e la parte di SWEET. Il codice rimasto si scrive in un unico blocco,
poi si fanno prove brevissime (smoke test) e infine si lanciano tutti i calcoli lunghi. Il
riepilogo §0 si consegna per ciascuna milestone alla fine.

Fasi: 0) verifiche e domande; 1) implementazione; 2) smoke test sul cluster; 3) stima complessiva,
lancio con dipendenze SLURM, comando di stato.

## Fase 0: verifiche delle fasi esistenti su L3 (30 ottobre 2026)

| Punto | Esito |
|---|---|
| Configurazione congelata | ok: il `CellPlanner` dà a ogni cella con metodo e `split=test` l'hash di `freeze/<metodo>.json` per (modello, linguaggio), senza guardare il livello |
| Soggetti di `detect` | ok: positivi, `neg_human` (nativi di L3, filtrati su `split=test`), `neg_llm`, `neg_llm_twin` per MCGMark; niente `neg_integration` (solo L2 dev) |
| Contesto vuoto D3 | ok: `_input` usa il prompt solo se il `problem_key` è fra i problemi del livello; i negativi di CodeSearchNet e The Stack che non sono prompt hanno contesto vuoto, i 500 prompt per linguaggio (e le 94 classi) il loro prompt |
| Soglie congelate | ok: le metriche leggono le soglie per (metodo, modello, linguaggio, livelli `[L1, L2]`), con il controllo dello sha256 congelato (ADR-013); nessuna ricalibrazione |
| Da adattare | metriche di L3 (Pass@1 solo ClassEval, validità per CodeSearchNet e The Stack, per dataset); ciclo di correttezza di PromptMark su L3 (la cella Python mescola ClassEval e CodeSearchNet, e l'adattatore rifiuta celle miste) |
| Scoperto | la fase 0 sul test è stata fatta solo su **L1**: nessun metodo ha campioni marcati di **L2 test**, che servono alla curva T2.4 in k su CodeNet e alle tabelle di L2 della M14 |

Decisioni dell'utente già date (30 ottobre 2026): validità del codice (tree-sitter principale,
`py_compile` e `node --check` controlli aggiuntivi, `javac` e `g++` solo informazione; ADR-016);
`main` e `__init__` contano come le altre funzioni nella curva in k (ADR-014 §2.3).

## Lista delle attività

Stato: `[ ]` da fare, `[~]` in corso, `[x]` fatto (con il commit).

### Fase 0
- [x] Verifiche delle fasi su L3 (`187cdb6`)
- [x] Risposte dell'utente (30 ottobre 2026): L2 test per tutti; D27 esteso a L3; avanzamento con
  uno script da rilanciare e un ciclo in tmux; divisione Ivan/Simone; rilevazione sempre su gpuq e
  generazione anche su aiq, GPU nel manifest; Tests4Py sui 5 progetti più piccoli, solo pilota;
  report in `$WMB/report/` e `report/`; smoke test di SWEET con congelamento finto su dati di
  sviluppo, scritture solo nella cartella dello smoke test

### Fase 1: M13
- [x] Hardware (nodo, partizione, GPU) nel manifest; rilevazione sempre su gpuq; D27 esteso a L3 (`7fc63ae`)
- [x] Metriche di L3 (`0ea4f6a`)
- [x] `--after-ok` in `bench submit`, `WMB_WRITE_ROOT` (`8634d1b`)
- [x] T2.4 in k (`T2.4k`, `649732d`)
- [x] Script principale delle catene (`bench/chains.py`, `scripts/chains.py`: metodi su L2 test e L3 con gemella e immagine L3, T2.4k, baseline; stato; ciclo per tmux) (`3d8c67f`, `22bbc5c`)
- [x] Pilota di Tests4Py (`bf333e9`, `1869224`)
- [ ] Stima dei costi complessiva (fase 3, dopo lo smoke test)

### Fase 1: SWEET
- [x] Pilota dei tempi su gpuq e aiq (`265c4b1`)
- [x] Catena: D31 (L1 e L2 dev) → calibrate → tune (`force`) → freeze → fase 0 su L1 test → T1–T4 con D34 → T3.1 → M12 → L2 test e L3 (in `bench/chains.py`)

### Fase 1: M14
- [x] Report dai soli artefatti (`bench/report.py`, `scripts/report.py`, `configs/report/annotations.yaml`) (`53eb043`)

### Fase 2
- [x] Smoke test: `scripts/smoke_seed.py`, `scripts/smoke_test.sh` (`fe7b7ce`, `22bbc5c`)
- [x] Lancio dello smoke test sul cluster ed esito (31 ottobre 2026; correzioni `37872b1`, `293f53a`, `e91919e`, `55a3c92`)

### Fase 3
- [~] Stima complessiva (ore GPU e CPU per metodo e livello, giorni reali): `scripts/estimate_closing_cost.py`, da lanciare sul cluster
- [x] Script principale con dipendenze SLURM, riprendibile, divisione fra i due utenti (`scripts/chains.py`)
- [x] Comando di stato per catena (`python scripts/chains.py status`)

## Esito del pilota dei tempi di SWEET (`M13_sweet_timing.txt`, 30 ottobre 2026)

Qwen, L2 dev Java, configurazione scelta su L1+L2, stesso nodo per tutto il job:

| | A100 (gpuq, gnode10) | H100 (aiq, ainode01) |
|---|---|---|
| generazione (s per problema, 6 campioni) | 20,4 | 26,1 |
| rilevazione (s per campione) | 4,37 | 4,56 |

- Su un nodo fisso i tempi stanno fra il minimo (1,0 s) e la mediana (16,4 s) dello sweep: la
  dispersione dello sweep veniva in buona parte dalle condizioni dei job (nodi, carico).
- La lentezza di Qwen rispetto a DeepSeek (0,27 s per campione sugli stessi dati) resta anche su un
  nodo fisso: è propria di SWEET con Qwen, non un effetto dei nodi.
- H100 non è più veloce di A100 per SWEET (la generazione è anzi più lenta): `aiq` serve solo
  quando la coda di `gpuq` è più lunga; la rilevazione resta comunque su `gpuq`.
- Stima della rilevazione di SWEET con Qwen su L3 (89.162 campioni): al più circa 110 h di GPU con i
  tempi di L2 Java (programmi lunghi), verosimilmente 30–40 h con funzioni della lunghezza di L3;
  DeepSeek circa 5 h. Generazione con Qwen circa 12 h. Nessuna riduzione necessaria.

## Esito dello smoke test (31 ottobre 2026)

Cartella `$WMB/smoke/` (un problema di sviluppo rietichettato `test` per dataset e linguaggio, un
job per passo; scritture solo nella cartella dello smoke test). Tutte le catene `DONE` tranne
ACW su L2 test e L3 (`HELD`: licenza di Sourcery da confermare), quindi:

- SWEET completo dopo il congelamento finto: fase 0 su L1 test, T1–T4 con il controllo di T2.2(b)
  (D34), T3.1 con la seconda chiave, perplessità della M12, L2 test, L3, T2.4k su L2 e L3;
- baseline e canoniche di L1, L2 e L3; PromptMark, STONE e MCGMark su L2 test e L3 con T2.4k;
- **non provati nello smoke test**: i passi di ACW su L2 test e L3 (si provano con
  `--with-acw --only acw/` dopo la conferma della licenza, oppure direttamente nel lancio vero) e
  i classificatori avversari della M12 di SWEET (5 fold per problema impossibili con 2 problemi
  di test per linguaggio; stesso codice già girato nella M12 sui dati veri degli altri metodi).

Problemi trovati e corretti durante lo smoke test:

| Problema | Correzione |
|---|---|
| `sweet/d31/tune` (passo `once`) girava nello smoke test e falliva senza le soglie della sua configurazione | i passi prima del congelamento si saltano nello smoke test (`37872b1`) |
| catene di un proprietario divise fra due account: rischio di invii doppi | file di stato per account, controllo dei job attivi degli altri account, `--only` (`293f53a`) |
| `squeue -j <id>` di un job uscito dalla memoria di SLURM risponde con un errore: il ciclo si fermava a ogni giro | il job conta come non attivo (`e91919e`) |
| classificatori della M12 di SWEET impossibili con i dati dello smoke test | saltati nello smoke test (`55a3c92`) |
