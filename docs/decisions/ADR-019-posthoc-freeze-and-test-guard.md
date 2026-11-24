# ADR-019 — Congelamento dei metodi post-hoc e controllo sulla parte di test (P6)

**Stato:** accettato (decisioni dell'utente del 24 novembre 2026; PIPELINE di multimark v1.3.16,
punti 1–8). **Contesto:** ADR-013 (congelamento e fase 0), ADR-014 §3 (T3.1), PIPELINE §5.10
(rilevazione aggregata), §6.5 (test).

## Problema

Il congelamento di ADR-013 prevede **una** configurazione per (metodo, modello, linguaggio), presa
dalla scelta di `tune` su L1+L2 (`hpo/selected_L1L2/`). Per `mcss` e `multimark` `tune` non si è mai
lanciato (le configurazioni sono state fissate con il criterio della pipeline, §6.4 e v1.3.10) e
`multimark` ha **due** configurazioni da portare sul test (prudente e alta capacità). Inoltre
`CellPlanner._selected` salta il congelamento quando c'è `hpo.grid` (lo sweep espande le celle di
test nei punti della griglia, anche con `hpo.use_selected=false`): un comando sbagliato potrebbe
lanciare sul test una configurazione non congelata.

## Decisione

1. **Registro** `configs/freeze/posthoc.yaml`: commit della libreria (b2b6c18) e, per metodo,
   etichetta → `config_hash` (`mcss`: `main` = 1d3087c73439; `multimark`: `prudent` =
   e52b040770df, `high_capacity` = 3fed1f1c1e24). Il submodule resta a b2b6c18; tag `p6-freeze`
   su wm-bench (commit del congelamento) e su multimark (b2b6c18).
2. **Fase `freeze` per i metodi del registro** (`FreezeStage._run_posthoc`): senza `tune`; per ogni
   configurazione l'hash ricalcolato dagli iperparametri (default o punto della griglia) deve
   coincidere con il registro e con la libreria; soglia L1+L2 non provvisoria e non
   `underpowered`; soglie della rilevazione aggregata presenti. Il record `freeze/<metodo>.json`
   ha una `FrozenCell` **con etichetta** per (modello, linguaggio, configurazione) e i campi nuovi
   `library_commit`, `library_patches_sha256`, `files_sha256` (metodo, griglia, registro, tutti
   gli attacchi) e `aggregated_sha256`. I record vecchi restano validi (campi con default);
   `FreezeRecord.cell(model, lang)` restituisce come prima la prima cella, con `config_hash` quella
   della configurazione.
3. **Controllo sul test solo per i metodi del registro** (`bench.hpo.posthoc_freeze.plan_cells`,
   chiamato da `CellPlanner.cells_for` dopo lo sweep e la griglia degli attacchi): per ogni cella
   `split=test` di `mcss` o `multimark` la pianificazione si ferma, con l'elenco di tutti i problemi,
   se c'è `hpo.grid`, `hpo.points` o `hpo.use_selected=false`, se il metodo non è congelato con il
   registro, se un `config_hash` non è nel record, se la copia patchata è a un altro commit o con
   altre patch, se uno dei file di configurazione o delle soglie aggregate ha uno sha256 diverso
   da quello del record. Le celle senza hash si espandono nelle configurazioni congelate. Per gli
   altri metodi niente cambia: la falla di `_selected` con una griglia resta (registrata in
   `docs/deviations.md`). Le stime in sola lettura (`estimate_closing_cost.py`, `check_test.py
   --plan`) usano `CellPlanner(..., posthoc_guard=False)` e gli hash del registro.
4. **Seconda chiave prima del congelamento** (punto 6): le fasi con `frozen_config` (`detect_k2`,
   `calibrate_k2`) espandono le celle dei metodi del registro nelle configurazioni registrate anche
   su dev e prima del congelamento; `freeze_k2` dopo il congelamento, come per gli altri metodi.
   T3.1 dei post-hoc gira su CPU (proponente statico).
5. **Opzioni nuove della configurazione** (retrocompatibili, `null` = comportamento di prima):
   `attack_points` (sottoinsieme degli `attack_params_hash`: un attacco con punti nella lista tiene
   solo quelli; un hash che non è un punto degli attacchi richiesti ferma la pianificazione) per
   l'insieme S su L2 test e i punti di ClassEval; `posthoc_datasets` (la fase `watermark` dei
   post-hoc marca solo i campioni della baseline di quei dataset) per L3 = solo ClassEval.
6. **Catene** (`bench.chains.posthoc_steps`): solo i passi sul test dei due metodi (fase 0 su L1, L2
   e ClassEval; attacchi per livello; controllo negativo di T2.2(b); T3.1; impercettibilità su L1),
   che restano «held» finché il metodo non è congelato. Congelamento, seconda chiave su dev e
   `freeze_k2` sono in `scripts/run_p6.sh`.
7. **Rilevazione aggregata** (`scripts/report_aggregated.py`, nessuna modifica a `src/bench` oltre al
   percorso delle soglie): `verify` e `calibrate` solo su dev, `evaluate` sul test solo con le
   soglie congelate (sha256 nel record).

## Conseguenze

- Una configurazione dei post-hoc non registrata, una griglia o un file cambiato non possono più
  produrre celle di test; cambiare il registro, un attacco o la griglia dopo il congelamento
  blocca tutta la parte di test dei due metodi (è voluto: ogni modifica è una deviazione).
- Il controllo legge pochi file a ogni pianificazione; il costo è trascurabile.
- `metrics` sul test controlla la soglia della **configurazione** della cella (più celle per
  (modello, linguaggio)); `check_test.py` confronta lo sha256 della soglia negli input di ogni
  manifest di `metrics` con quello congelato.
