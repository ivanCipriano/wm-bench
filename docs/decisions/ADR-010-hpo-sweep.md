# ADR-010 — Sweep dell'HPO: una cella per configurazione, fase `tune`

- **Stato:** accettata (Milestone 8, 12 ottobre 2026).
- **Contesto:**
  - SPEC §14 (selezione degli iperparametri), §15 (fasi), §16.4 (sweep come array SLURM);
  - `docs/hpo_grids.md`;
  - ADR-009 (griglia di PromptMark ricostruita);
  - `cluster_info` §11, TODO 23 (HPO della M8 provvisorio).

## Decisione

1. **Griglie** in `configs/hpo/<metodo>.yaml`, caricate da `bench.hpo.grid`. Contengono:
   - `space`: un prodotto cartesiano, con i nomi del protocollo;
   - `include`: punti in più, come il default del codice;
   - `replay`: opzionale, una traiettoria per combinazione degli altri iperparametri (ADR-009).

   MCGMark ha il solo default, perché non ha iperparametri regolabili.
2. **Una cella per configurazione.** Con `hpo.grid=<metodo>` il `CellPlanner` dà alle fasi che partecipano allo
   sweep una cella per punto della griglia, con l'hash nel campo `Cell.config_hash`. Le fasi che partecipano sono
   quelle con `sweep_roles`: `watermark`, `execute` (solo i campioni marcati), `detect` e `calibrate`.
   - Le fasi ricavano gli iperparametri con `bench.hpo.grid.resolve`. Una cella senza hash usa il default, come
     prima, quindi nessun comando della M7 cambia.
   - I percorsi degli artefatti contengono già l'hash della configurazione, quindi le configurazioni coesistono
     e quella di default della M7 si riusa senza ricalcolo.
   - I job restano quelli di `bench submit`: celle divise fra `--jobs` job sequenziali, `--share` fra i due
     utenti, ripresa e rimessa in coda al timeout. Il "array SLURM" della SPEC §16.4 coincide con questi job.
3. **PromptMark (ADR-009).** `hpo.part=trajectories` genera le 3 traiettorie; `hpo.part=candidates` produce i
   candidati.
   - I 27 candidati ricostruiti leggono i risultati del worker della traiettoria (input della cella) e non
     generano nulla (`PromptMarkAdapter.embed_from_trajectory`).
   - Lo shim ora registra anche gli identificatori liberi per iterazione, che servono come siti idonei.
4. **Fase `tune`** (CPU, cella = metodo, modello, linguaggio). Per ogni candidato, sui livelli della
   configurazione (parte dev), calcola:
   - `tpr_dev`, alla soglia del candidato e con il punteggio principale del metodo;
   - `pass1_dev`;
   - `pass1_baseline_dev`, sulla gemella per MCGMark (D25) e sulla baseline LLM per gli altri metodi (per ACW è la
     baseline da cui deriva il codice);
   - `pass1_drop_pp`.

   Poi applica `ConstrainedTprCriterion` (`hpo.max_pass1_drop_pp` = 3,0, confermato come definitivo dall'utente il 20 ottobre 2026) e scrive:
   - `hpo/candidates/<metodo>/<modello>/<linguaggio>.parquet`, con tutti i candidati;
   - `hpo/selected/<metodo>/<modello>/<linguaggio>.json` (`SelectedConfig`), con `provisional` se le soglie lo
     sono e `constraint_satisfied = false` per i casi da segnalare.

## Conseguenze

- In M8 l'HPO usa solo L1 dev (soglie provvisorie, TODO 23). In M9 si aggiungono le celle di L2 per le stesse
  configurazioni e si rifanno `calibrate` e `tune` (TODO 24): i risultati di L1 restano validi.
- Le selezioni non sono ancora lette dalle altre fasi (`hpo.use_selected`): servono dalla M10 in poi, dopo la M9.
- La selezione è un file JSON, non YAML come in SPEC §14.5, per usare lo stesso formato e lo stesso manifest degli
  altri modelli dello store (differenza di implementazione registrata in `docs/deviations.md`).
- Comandi: `scripts/submit_hpo_l1.sh` (trajectories, watermark, execute, detect, calibrate, tune) e
  `scripts/report_hpo.py`.
- **Estensione locale della griglia** (20 ottobre 2026, D31): `configs/hpo/<metodo>.yaml` può avere punti
  `local` (`hparams` più `cells`, una lista di `model_id`/`language`). Valgono solo nelle celle elencate: lo
  sweep li espande solo lì, `tune` li confronta solo lì e `freeze` controlla per ogni cella i suoi candidati
  (il record elenca tutti gli hash). `scripts/report_hpo.py --curve delta` stampa la curva di un
  iperparametro con gli altri fissi a quelli della scelta.
