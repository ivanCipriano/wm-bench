# ADR-013 — Congelamento dei metodi e fase 0 su L1 test

- **Stato:** accettata (Milestone 10, 16 ottobre 2026).
- **Contesto:**
  - SPEC §13.2 (soglie congelate, I3), §14 (scelta su dev), §15.1 (`tune → attack`), §22 (M10: attacchi su L1 test);
  - decisione dell'utente del 16 ottobre 2026 (piano della M10).

## Problema

Gli attacchi della M10 partono dai campioni marcati di **L1 test** con la configurazione definitiva. Fino alla M9:

- tutte le fasi usavano il default del metodo oppure una cella della griglia;
- nessun meccanismo garantiva che configurazione e soglie fossero fissate **prima** di guardare il test.

## Decisione

1. **Congelamento per metodo (fase `freeze`, `scripts/freeze_method.py METODO`).** È possibile solo se, per ogni (modello, linguaggio) supportato:
   - la scelta è quella definitiva su L1+L2 (`hpo/selected_L1L2/`, livelli `[L1, L2]`, non provvisoria);
   - la soglia L1+L2 della configurazione scelta non è provvisoria;
   - i candidati della scelta (`hpo/candidates_L1L2/`) coincidono con quelli di `configs/hpo/<metodo>.yaml`.

   Altrimenti la fase si ferma ed elenca **tutto** quello che manca. Il record `freeze/<metodo>.json` (con manifest) contiene:
   - data;
   - per cella: configurazione, iperparametri, soglia, sha256 della selezione e della soglia;
   - sha256 della griglia e hash dei candidati;
   - nel manifest, commit e stato del repository.

   Lo script lo stampa: il log `docs/milestone_logs/M10_freeze_<metodo>.txt` è la prova che le scelte sono precedenti al test.
2. **Dopo il congelamento.**
   - `calibrate` e `tune` rifiutano il metodo (`ConfigError`). Ricalibrare è possibile solo con `hpo.allow_frozen_override=true`: il manifest lo registra (`frozen_override`) e va annotato come deviazione in `docs/deviations.md`.
   - Un nuovo congelamento è rifiutato.
3. **Parte di test.** Con `hpo.use_selected` (default) e fuori da uno sweep, il `CellPlanner` dà a ogni cella con metodo e `split=test` l'hash congelato (`bench.hpo.freeze.selected_hash`).
   - Un metodo non congelato ferma la pianificazione.
   - I linguaggi non applicabili restano senza hash.
4. **Metriche sul test.** Usano le soglie definitive (`detection.threshold_levels=[L1, L2]`, cartella `metrics_L1L2/`). Prima di calcolare controllano che la configurazione sia quella congelata e che la soglia abbia lo sha256 congelato: una soglia riscritta dopo il congelamento è rifiutata.
5. **PromptMark sul test.** La configurazione congelata si **esegue direttamente**, senza traiettoria. La ricostruzione dell'ADR-009 serve solo a risparmiare lo sweep su dev; la sua equivalenza con l'esecuzione diretta è verificata su L1 e su L2 (`M8_promptmark_replay.txt`, `M9_promptmark_replay_l2.txt`).
6. **Nomi dei job.** Le celle di test hanno l'etichetta `_test` (`wmb-detect.stone_test`), quindi non vanno in conflitto con i job di sviluppo dello stesso metodo.

## Conseguenze

- Ogni metodo entra nella fase 0 e negli attacchi solo dopo il congelamento. Si parte da STONE e PromptMark; gli altri entrano alla chiusura della M9, con gli stessi controlli.
- Script:
  - `scripts/submit_test_l1.sh` (`twin`, `watermark`, `execute`, `detect`, `metrics`);
  - `scripts/check_test.py` (stato per passo);
  - `scripts/report_metrics.py --final --split test` (metriche).
- Test: `tests/unit/test_freeze.py`.
