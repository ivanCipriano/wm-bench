# Riepilogo della Milestone 7 — Rilevazione e calibrazione (SPEC §0, §22)

Data: 12 ottobre 2026. Stato: **quasi chiusa**. SWEET, STONE, MCGMark e PromptMark sono completi su L1 dev.
ACW è in rilevazione: il primo tentativo si è fermato il 10 ottobre alle 17:34, quando è scaduta la prova di
Sourcery (canarino dopo ogni lotto, `M7_acw_diag.txt`). Il token è stato sostituito e la ripresa riparte dai 150
positivi su 576 già scritti per modello. I numeri di ACW si aggiungono a questo riepilogo quando arrivano i log.
Su richiesta dell'utente si inizia la M8 senza aspettare ACW.

## Criteri di accettazione (SPEC §22)

| Criterio | Esito |
|---|---|
| Fase `detect` (positivi, negativi umani, negativi LLM) | fatta; per MCGMark anche i negativi della baseline gemella (`neg_llm_twin`, D25) |
| `ThresholdCalibrator`, soglie congelate per la configurazione di default | fatte, ma **provvisorie** (D24): calibrate sui soli negativi umani di L1 dev (2.000 per linguaggio), `provisional = true`; la fase `metrics` rifiuta il test |
| TPR@1%FPR, AUROC, Pass@1, ΔPass@1 con IC su L1 dev | 4 metodi su 5 (18 celle); ACW in corso |

## Cosa è stato fatto

- **Fasi nuove:** `detect` (GPU per SWEET, STONE e MCGMark, CPU per PromptMark e ACW), `calibrate` e `metrics`
  (CPU). Le soglie stanno in `thresholds/<metodo>/<modello>/<lang>/<cfg>.json`, con i campi `provisional`,
  `levels`, `underpowered` (meno di 1.000 negativi) e le soglie dei punteggi secondari. Una soglia
  irraggiungibile viene serializzata come `Infinity`.
- **Calibrazione (SPEC §13.2):** la soglia è il più piccolo punteggio negativo con FPR ≤ 1%. Una rilevazione
  FAILED vale come punteggio minimo, e lo stesso vale per un inserimento FAILED fra i positivi (I2).
- **Statistiche:**
  - TPR, Pass@1 e ΔPass@1: bootstrap per problema;
  - FPR: Clopper–Pearson;
  - AUROC: bootstrap dei positivi per problema più ricampionamento dei negativi;
  - Wilson per accuratezza del messaggio e inserimento riuscito (MCGMark).
- **Punteggi secondari:** lo z del codice ufficiale di STONE (`z_official`; dal 12 ottobre 2026 il principale è
  `z_nonsyntax`, D16) e `p_adaptive_paper` di PromptMark (come −log10 p), con
  soglia e metriche proprie.
- **Esecuzione:**
  - i campioni marcati di tutti i metodi (`execution/llm_watermarked/...`);
  - la baseline gemella di MCGMark (`execution/baseline_twin/...`);
  - un inserimento FAILED conserva il codice di partenza.
- **Job:** il nome include i metodi (`wmb-<fase>.<metodi>`), quindi job dello stesso tipo ma di metodi diversi
  non si bloccano a vicenda.
- **ACW, licenza:** lo shim ripete il canarino dopo ogni lotto. Se Sourcery smette di funzionare, il lotto non
  si scrive e il worker si ferma con `FatalShimError` (exit code 2). Basta cambiare il token e rilanciare
  (audit di ACW §6, decisione dell'utente del 10 ottobre 2026). Ha funzionato al primo tentativo.
- **Script:**
  - `submit_m7_l1.sh` (execute, detect, calibrate, metrics per metodo);
  - `report_metrics.py`;
  - `check_m7.py` (completezza delle celle e stati di MCGMark);
  - `show_mcgmark_errors.py`.
- **Deviazioni nuove:**
  - D24: soglie provvisorie su L1;
  - D25: per MCGMark FPR LLM e ΔPass@1 primari sulla gemella.

## Risultati su L1 dev (configurazione di default, soglie provvisorie)

Legenda delle colonne:
- **TPR e AUROC:** punteggio principale, con IC al 95%.
- **Pass@1:** percentuale dei marcati e della baseline di riferimento (la gemella per MCGMark).
- **ΔPass@1:** baseline − marcati, in punti percentuali; un valore positivo è una perdita.
- **Numerosità:** 576 positivi per Python, 144 per gli altri linguaggi; 2.000 negativi umani.

| Metodo | Modello | Ling. | Soglia | TPR@1%FPR % | AUROC | Pass@1 % | ΔPass@1 pp |
|---|---|---|---|---|---|---|---|
| SWEET | Qwen | python | 2,35 | 1,6 [0,0; 3,7] | 0,68 [0,63; 0,73] | 76,2 / 77,4 | 1,2 [−0,2; 2,6] |
| SWEET | Qwen | java | 2,29 | 2,8 [0,0; 8,3] | 0,53 [0,43; 0,64] | 87,5 / 86,1 | −1,4 [−4,2; 0,0] |
| SWEET | Qwen | cpp | 2,29 | 0,7 [0,0; 2,1] | 0,57 [0,47; 0,67] | 82,6 / 83,3 | 0,7 [0,0; 2,1] |
| SWEET | Qwen | javascript | 2,60 | 2,1 [0,0; 6,3] | 0,49 [0,38; 0,61] | 83,3 / 83,3 | 0,0 |
| SWEET | DeepSeek | python | 2,78 | 0,3 [0,0; 1,0] | 0,54 [0,49; 0,59] | 66,0 / 65,8 | −0,2 [−3,3; 3,3] |
| SWEET | DeepSeek | java | 2,85 | 0,0 | 0,49 [0,39; 0,59] | 72,9 / 64,6 | −8,3 [−16,7; 0,0] |
| SWEET | DeepSeek | cpp | 2,53 | 0,0 | 0,53 [0,43; 0,63] | 67,4 / 67,4 | 0,0 [−4,2; 5,6] |
| SWEET | DeepSeek | javascript | 2,89 | 0,0 | 0,46 [0,37; 0,56] | 73,6 / 73,6 | 0,0 [−2,8; 2,1] |
| STONE | Qwen | python | 3,61 | 4,9 [1,0; 9,6] | 0,67 [0,62; 0,72] | 78,1 / 77,4 | −0,7 [−3,5; 1,4] |
| STONE | Qwen | java | 3,76 | 10,4 [0,0; 22,9] | 0,54 [0,41; 0,67] | 86,8 / 86,1 | −0,7 [−2,1; 0,0] |
| STONE | Qwen | cpp | 3,72 | 4,2 [0,0; 12,5] | 0,63 [0,54; 0,73] | 82,6 / 83,3 | 0,7 [0,0; 2,1] |
| STONE | DeepSeek | python | 4,81 | 1,4 [0,0; 3,5] | 0,44 [0,39; 0,50] | 65,5 / 65,8 | 0,3 [−2,4; 3,0] |
| STONE | DeepSeek | java | 3,67 | 0,0 | 0,36 [0,27; 0,46] | 68,8 / 64,6 | −4,2 [−10,4; 0,7] |
| STONE | DeepSeek | cpp | 4,88 | 0,0 | 0,32 [0,22; 0,44] | 66,0 / 67,4 | 1,4 [−2,1; 6,3] |
| MCGMark | Qwen | python | 10 | 7,3 [4,5; 10,8] | 0,39 [0,36; 0,41] | 27,3 / 76,0 | 48,8 [40,5; 56,3] |
| MCGMark | DeepSeek | python | 10 | 1,2 [0,5; 2,1] | 0,41 [0,40; 0,42] | 31,3 / 65,3 | 34,0 [26,4; 41,2] |
| PromptMark | Qwen | python | 2,13 | 4,0 [0,7; 7,8] | 0,63 [0,57; 0,69] | 81,9 / 77,4 | −4,5 [−11,1; 1,6] |
| PromptMark | DeepSeek | python | 2,13 | 4,3 [1,7; 7,6] | 0,51 [0,45; 0,57] | 64,6 / 65,8 | 1,2 [−4,5; 6,8] |
| ACW | Qwen, DeepSeek | python | — | in corso | | | |

**Punteggi secondari.**

STONE, z del codice ufficiale (`z_official`, secondario dal 12 ottobre 2026, D16):

| Modello | Ling. | Soglia | TPR % | AUROC |
|---|---|---|---|---|
| Qwen | python | 31,1 | 1,0 [0,0; 3,1] | 0,32 [0,27; 0,37] |
| Qwen | java | 24,5 | 4,2 [0,0; 12,5] | 0,58 [0,46; 0,69] |
| Qwen | cpp | 29,2 | 0,0 | 0,25 [0,16; 0,35] |
| DeepSeek | python | 22,5 | 0,0 | 0,17 [0,14; 0,22] |
| DeepSeek | java | 16,5 | 0,0 | 0,12 [0,08; 0,17] |
| DeepSeek | cpp | 25,5 | 0,0 | 0,32 [0,23; 0,42] |

PromptMark `p_adaptive_paper`: soglia, TPR e AUROC praticamente uguali al principale (4,0% e 0,63 su Qwen; 4,3% e
0,51 su DeepSeek).

**FPR sui negativi LLM** (soglia calibrata sui negativi umani):
- quasi sempre ≤ 1%;
- sopra l'1%: PromptMark Qwen 3,1% [1,9; 4,9], SWEET Qwen javascript 1,4% [0,2; 4,9] e STONE con Qwen
  (Python 5,4% [3,7; 7,6], Java 10,4% [6,0; 16,6], C++ 4,2% [1,5; 8,9]). Per STONE con Qwen il TPR è uguale o
  quasi all'FPR sulla baseline LLM: con il default il watermark non si distingue dal codice LLM non marcato;
- MCGMark: gemella 0,0% (Qwen) e 0,2% (DeepSeek), uguali o quasi alla baseline normale.

## Risultati da ricordare

- **SWEET (TODO 8):** il default usa δ = 0,5, più debole del δ = 1,0 di STONE. TPR 0-3%, AUROC 0,46-0,68
  (sopra 0,6 solo con Qwen in Python). Un rilevamento così basso con questo δ non è un errore di integrazione.
  Nessun costo misurabile su Pass@1: l'unico valore grande, −8,3 pp in Java con DeepSeek, va a favore dei
  marcati e ha IC che arriva a 0.
- **STONE, punteggio e intensità (D16, D26; verifiche del 12 ottobre 2026 in M8):**
  - **Lo z ufficiale è invertito.** L'AUROC è sotto 0,5 in 5 celle su 6 (0,12-0,58) e le soglie sono enormi
    (z = 16-31). Sotto l'ipotesi nulla lo z ufficiale misura la composizione del codice (token non sintattici
    contro sintattici), e la sola composizione riproduce l'inversione; l'adattamento del vocabolario non c'entra.
  - **Il punteggio principale è ora `z_nonsyntax`** (secondo caso della regola delle discrepanze). Le soglie
    sono 3,6-4,9, l'AUROC è 0,54-0,67 su Qwen e resta sotto 0,5 su DeepSeek. La tabella sopra usa già il nuovo
    punteggio.
  - **Il default δ = 1,0 non inserisce un watermark visibile** sul nostro codice. Marcato e baseline LLM sono
    indistinguibili (AUROC 0,49-0,55) e la separazione dal codice umano viene dallo stile LLM.
    - Il processor lavora correttamente sui logit grezzi, prima di temperatura e top-p.
    - Quando il token più probabile è rosso, però, il miglior token verde è più di 3 punti di logit sotto nel
      98% dei passi: il codice è a bassissima entropia.
    - Il watermark si vede solo a δ = 10: frazione verde 0,83 contro 0,56 e AUROC contro la baseline 0,84, ma
      con risposte circa 3 volte più lunghe (`M8_stone_dose.txt`, `M8_stone_processor.txt`).
  - Con la configurazione degli autori l'AUROC resta lontano dallo 0,982 della letteratura (D26).
  - Nessun costo su Pass@1 con il default.
- **MCGMark (TODO 13, 15, 25):**
  - inserimento riuscito (almeno un ciclo di 24 posizioni): 8,0% con Qwen, 1,7% con DeepSeek;
  - siti per campione: media 10,7 (Qwen) e 7,8 (DeepSeek), mediana 7 e 6;
  - TPR 7,3% e 1,2%, accuratezza del messaggio 6,1% e 1,2%, AUROC sotto 0,5: quasi tutti i campioni non hanno
    un ciclo completo e prendono il punteggio minimo;
  - **le soglie provvisorie di MCGMark non sono significative finché non arriva L2**, dove il codice è più
    lungo.
  - **Calo di Pass@1:** 48,8 pp (Qwen) e 34,0 pp (DeepSeek) contro la gemella (stesso prefill, semi e ambiente),
    praticamente uguale contro la baseline normale (50,2 e 34,5).
    - I marcati hanno errori di sintassi nel 32% (Qwen) e nel 20% (DeepSeek) dei casi, contro lo 0% della gemella.
    - Gli errori aumentano con il numero di siti marcati.
    - Causa: con il bit 0 la green list riceve tutto lo scarto dei logit come penalità. Il token giusto viene
      così sostituito dal miglior token rosso, che può essere anche un token speciale o EOS: per questo
      spariscono token come `in` e `.` e alcune risposte si troncano.
    - È il comportamento del codice degli autori, che si segue senza patch (audit di MCGMark §2,
      `M7_mcgmark_errors.txt`).
- **PromptMark (TODO 19):**
  - come previsto dall'oracle, i modelli da 7B seguono poco l'istruzione;
  - TPR circa 4% e AUROC 0,51-0,63;
  - Pass@1 invariato: ΔPass@1 −4,5 pp con Qwen e 1,2 pp con DeepSeek, entrambi con IC che contiene 0;
  - la variante del paper del p-value non cambia nulla;
  - con Qwen la baseline LLM supera l'1% di FPR (3,1%): il punteggio è un po' più alto sul codice LLM che su
    quello umano.
- **ACW (TODO 20, 22):** in corso. Il punteggio è discreto (frazione delle regole invariate), quindi la soglia
  all'1% potrebbe essere irraggiungibile: in quel caso il calibratore la serializza come `Infinity` e il
  manifest lo segnala (`unreachable`). Nella tesi va dichiarata la verifica "cieca" (TODO 22).
- **Positivi con inserimento FAILED:** contano come non rilevati (I2) e il loro codice resta nel Pass@1.

## Cosa resta aperto

1. **ACW:** detect → calibrate → metrics, poi `report_metrics.py` e `check_m7.py`. I suoi numeri vanno
   aggiunti a questo riepilogo.
2. Voci aperte della M6:
   - audit di MCGMark, PromptMark e ACW da confermare come approvati;
   - esclusione delle 6 classi ClassEval (D21).
3. **Smoke test della parte di test:** non c'è ancora una configurazione dedicata. L'opzione
   `detection.allow_provisional_test` è pronta per quando servirà.

## `TODO(user)` e decisioni per la M8

- **Griglie HPO** (SPEC §14 e §17, `TODO(user)`): da proporre all'inizio della M8. Vincoli già decisi:
  - TODO 6, 7, 12;
  - TODO 16: stima del costo prima della griglia di PromptMark;
  - ACW: `num_transforms` ∈ {10, 20, 30, 43}.
- **HPO provvisorio (TODO 23):** usa le soglie provvisorie di L1 e va rifatto in M9 dopo la ricalibrazione su
  L1+L2 (TODO 24); solo dopo si calcolano le metriche sul test.
- **`max_pass1_drop_pp` = 3,0** (default provvisorio di SPEC §14): con MCGMark (34-49 pp con la configurazione di
  default) è probabile che nessuna configurazione sia ammissibile. Il criterio allora sceglie la perdita minima
  e segnala il caso, come prevede SPEC §14.
- Per M9 e oltre: TODO 9-11, 14, 17, 18, 21, 22 di `cluster_info` §11 (invariati).

## Verifica locale finale

ruff e mypy puliti; pytest 347 passati, 88 saltati (oracle e test del cluster). `test_decoding.py` è escluso in
locale per un segfault dell'ambiente transformers/torch del PC, non legato alla M7.
