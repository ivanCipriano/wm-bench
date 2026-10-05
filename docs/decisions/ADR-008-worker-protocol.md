# ADR-008 — Protocollo dei worker, adapter dei metodi e fase `watermark`

- **Stato:** accettata (Milestone 5, 5 ottobre 2026)
- **Contesto:** SPEC §7.2, §7.3, §8, §15.2; cluster_info §1 (Python ≥ 3.9 nei worker, ripresa obbligatoria), §11.

## Decisioni

1. **Shim** in `shims/bench_shims/` (Python ≥ 3.9: niente `match` né `zip(strict=)`, `from __future__ import annotations`
   ovunque; verificato da un test con `ast.parse(feature_version=(3, 9))`). Nessuna dipendenza da `bench`: solo stdlib,
   `bench_contracts` e le librerie dell'ambiente del metodo.
   - `_common/runner.py`: ciclo di vita di SPEC §8.2.
     - Exit code 3 per un `schema_version` diverso, 2 per un `setup` fallito, 0 altrimenti.
     - **Ripresa**: all'avvio tiene in `output_path` solo gli item completi (n righe per l'inserimento da prompt, una
       negli altri casi) e li salta. Un item scritto a metà, cioè con il worker ucciso tra due righe, si rifà.
     - Eccezione su un item ⇒ risultati `FAILED` con traceback, senza fermare il ciclo.
     - Ogni riga si scrive con `flush` + `fsync`.
   - `_common/decoding.py`: la `GenerationConfig` si costruisce **solo** dal dizionario del request
     (`bench.generation.decoding.neutral_settings` più `torch_dtype`), con eos e pad come nella baseline. È una sola fonte
     per baseline e metodi (cluster_info §11). Seme fissato prima di ogni `generate`.
   - `_common/chat.py`: chat template nativo sui messaggi del `PromptBuilder` (identici alla baseline); si decodificano
     solo i token nuovi.
   - `_common/introspect.py`: `--introspect` restituisce Python, `pip freeze` con il suo hash, torch, CUDA, transformers,
     il commit e l'hash delle patch della copia patchata.
   - `echo`: shim di prova senza modello, per i test del protocollo (ripresa, timeout, errori, I1).

2. **`WorkerClient`** (`src/bench/methods/worker_client.py`).
   - Scrive `items.jsonl` e `request.json` in una cartella di lavoro **stabile per cella**, quindi un rilancio riprende.
   - `PYTHONPATH` = `shims/` + la sottocartella della copia patchata indicata dall'audit (`patched_subdir`).
   - Ambiente ripulito: solo poche variabili di sistema (PATH, HOME, CUDA_VISIBLE_DEVICES, HF_HOME…) più le variabili
     fisse di SPEC §8.1 e `PYTHONIOENCODING=utf-8`. I segreti (`SOURCERY_TOKEN`, solo ACW) passano dall'ambiente
     dell'utente e non vengono mai scritti.
   - Timeout, o processo morto con un codice diverso da 2 e 3 ⇒ **un solo rilancio**. Al secondo fallimento
     `WorkerError` con le ultime 50 righe del log.
   - **I1:** n risultati per item nell'inserimento da prompt, uno negli altri casi. I mancanti diventano `FAILED` con
     `error="missing_result"` e vengono segnalati (`on_missing`).

3. **`MethodAdapter`** (`src/bench/methods/base.py`), Template Method di SPEC §7.2.
   - Fornisce: capacità, chiave `derive_seed(global_seed, "wm-key", metodo, key_id) % 2**31`,
     `config_hash(iperparametri nativi, contratto, commit del submodule)`, costruzione del request e invocazione del
     worker.
   - `PromptEmbedder.embed_from_prompts` produce N campioni per problema. Usa lo stesso seme per problema della
     baseline e la stessa regola di estrazione (D4). I linguaggi non supportati danno N righe `NOT_APPLICABLE`
     **senza** invocare il worker.
   - Registro `METHODS`, popolato da `bench.methods.adapters` con import espliciti.
   - `configs/method/<metodo>.yaml` acquisisce `patched_subdir`, `supported_languages` e `default_hparams`, tutti presi
     dall'audit.

4. **Fase `watermark`** (GPU).
   - Celle (metodo, modello, livello, linguaggio, parte), con la configurazione di default del metodo fino all'HPO.
   - Output `watermarked/<metodo>/<modello>/<config_hash>/<L>_<lang>_<split>.parquet`.
   - Il manifest registra: iperparametri nativi, chiave, conteggio degli stati, tasso di estrazione, configurazione di
     generazione effettiva, hash del request, introspezione del worker (`worker_env`, `worker_packages_sha256`).

5. **STONE** (audit `docs/audit/stone.md`).
   - Patch 0001 per l'uso con più sequenze per volta.
   - Vocabolario del generatore (D15).
   - Punteggio ufficiale, più `z_nonsyntax` in `extra` (D16).
   - JavaScript `NOT_APPLICABLE`.
   - Rilevazione su **CUDA**: la green list nasce da un `torch.Generator` sul dispositivo, e su CPU la permutazione è
     diversa.

## Conseguenze

- Il criterio "ripresa dopo l'uccisione del worker" è verificato in due modi: localmente, con lo shim `echo` ucciso a
  metà da un timeout e rilanciato senza duplicati; sul cluster, con un job `watermark` interrotto (`scancel`) e
  rilanciato.
- I processor degli altri metodi che modificano i logit (SWEET, MCGMark) vanno verificati con lo stesso test di
  equivalenza per riga nella M6 (cluster_info §11).
