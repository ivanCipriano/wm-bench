# Audit di SWEET (SPEC §9.0)

- **Stato:** approvato dall'utente il 6 ottobre 2026 (Milestone 6, fase A).
- **Repository:** `hongcheki/sweet-watermark`, paper "Who Wrote this Code? Watermarking for Code Generation"
  (arXiv 2305.15060v4, 3 luglio 2024).

## 1. Commit e data

- Commit `853b47eb` (submodule `third_party/sweet-watermark`).
- Il codice eseguito è la copia `build/patched/sweet/` (ADR-002) con la patch preesistente `0000`: solo
  `lm_eval/generation.py:82`, dove `args.rdfw or args.srdfw` diventa `args.exp`. È un `AttributeError` del baseline EXP
  e non tocca SWEET.
- Ambiente `sweet`: Python 3.10.21, torch 2.4.0+cu121. La versione di transformers è registrata dall'introspezione
  del worker.

## 2. Punto d'innesto

- **Inserimento:** `sweet.py::SweetLogitsProcessor`, che estende `watermark.py::WatermarkBase` con argomenti `vocab,
  gamma, delta, seeding_scheme="simple_1", hash_key, select_green_tokens, entropy_threshold`. Per ogni riga del batch:
  1. green list;
  2. entropia di `softmax(scores)`;
  3. `+δ` sui token verdi solo se l'entropia supera la soglia.
  - Lo shim lo costruisce come `lm_eval/generation.py:74-78`, con `vocab=list(tokenizer.get_vocab().values())`.
  - Lo passa a `model.generate` con il prompt chat del framework, il decoding neutro, il seme del problema e
    `num_return_sequences = n`.
- **Rilevazione:** i passi di `lm_eval/evaluator.py:130-170`.
  1. Forward del **modello** su contesto + codice (`lm_eval/utils.py::calculate_entropy`, copiata nello shim perché
     il modulo non si importa).
  2. Entropia spostata di una posizione (`[0] + entropy[:-1]`).
  3. `SweetDetector.detect(tokenized_text, tokenized_prefix, entropy)`, con `SweetDetector` costruito come in
     `evaluator.py:247-251`.
- `evaluator.py` non si usa: contiene `pdb.set_trace()` (righe 111-128 e `sweet.py:68-71`) che bloccherebbe un job.
  Anche `lm_eval/__init__` e `tasks` non si importano (Cython, `fcntl`, `mosestokenizer`). Lo shim importa solo
  `sweet.py` e `watermark.py`.

## 3. Iperparametri

| Protocollo | Nativo | Note |
|---|---|---|
| frazione della green list γ | `gamma` | |
| intensità del bias δ | `delta` | |
| soglia di entropia | `entropy_threshold` | |
| chiave | `hash_key` | intero `derive_seed(global_seed, "wm-key", "sweet", key_id) % 2**31` (SPEC §9.6); il repository usa il default 15485863 |

- **Fissi come nel repository:** `seeding_scheme="simple_1"` (contesto di 1 token), `select_green_tokens=True`,
  `z_threshold=4` (soglia nativa, solo diagnostica: D2).
- **Default del framework** (decisione dell'utente): i valori di `main.py`, cioè γ = 0,5, δ = 0,5, soglia 0,5. Valgono
  solo fino all'HPO. Lo script `scripts/main/run_sweet_generation.sh` lascia i tre valori come segnaposto.
- **Valori del paper** (arXiv 2305.15060v4):
  - App. D.2, ricerca per HumanEval, MBPP e DS-1000: γ ∈ {0,1; 0,25; 0,5}, δ ∈ {0,5; 1,0; 2,0; 3,0; 4,0},
    soglia ∈ {0,3; 0,6; 0,9; 1,2};
  - configurazione mostrata nelle figure: Fig. 4 e Fig. 5 "γ = 0,25 e δ = 3,0"; Fig. 3 "soglia 1,2";
  - criterio di scelta (§5.1): frontiera di Pareto, miglior AUROC intorno al 90% del Pass@1; in alternativa,
    configurazioni con AUROC ≥ 0,9;
  - App. E.1 (HumanEvalPack C++/Java): δ ∈ {1,0; 2,0; 3,0; 4,0};
  - App. E.2 (ClassEval): soglia ∈ {0,01; 0,03; 0,05; 0,1; 0,2}, δ ∈ {2; 3; 4; 5; 10; 15; 20};
  - App. H: soglia calibrata su CodeSearchNet nell'intervallo [0,820; 0,871].
- **Compito per la M8** (decisione dell'utente): la griglia di SWEET deve includere:
  - i valori del paper (γ ∈ {0,1; 0,25; 0,5}, δ ∈ {0,5; 1; 2; 3; 4}, in particolare γ = 0,25 e δ = 3,0);
  - la soglia di entropia in {0,3; 0,6; 0,9; 1,2}, l'intervallo esplorato su HumanEval e MBPP;
  - il default del codice.
- **Promemoria per la M7** (decisione dell'utente): il default di SWEET usa δ = 0,5, più debole del δ = 1,0 di STONE.
  Un rilevamento più basso non va letto come un errore di integrazione.

## 4. Linguaggi

Il watermark non contiene alcuna logica legata al linguaggio: niente parser, AST o insiemi di token. Funziona quindi
per **tutti e quattro** i linguaggi. Nel repository il linguaggio conta solo nell'harness di valutazione.

## 5. Punteggio di rilevazione e direzione

- z-score sui soli token con entropia sopra la soglia: `(verdi − γ·T) / sqrt(T·γ(1−γ))`, con T = token del codice con
  entropia > soglia (`sweet.py:73-75`, `watermark.py:149-155`). Più alto vuol dire più marcato.
- **Contesto** (SPEC §9.1), cioè gli id del prefisso che condizionano l'entropia ed escono dal conteggio:
  - per positivi, negativi LLM e negativi umani con prompt: gli id del prompt chat del problema
    (`apply_chat_template(..., add_generation_prompt=True)`, gli stessi della generazione);
  - per i negativi senza prompt (CodeSearchNet, The Stack): contesto vuoto (D3). In quel caso, con
    `min_prefix_len = 1`, il primo token non viene valutato, come nel repository.
- **Codice valutato:** quello estratto (D4), tokenizzato senza token speciali e accodato al contesto.

## 6. Decoding cablato da sovrascrivere

- `main.py`: temperature 0,2, top_p 0,95, top_k 0, `max_length_generation` su prompt + generazione, e un criterio di
  arresto con le parole del task.
- Lo shim usa il decoding neutro del request (ADR-006) e nessun criterio di arresto aggiuntivo, come la baseline.

## 7. Generatore casuale e dispositivo

- **Seme e green list:** sia nel processor sia nel detector, `torch.Generator()` è su **CPU**. Il seme è
  `hash_key × token precedente` e la green list è `randperm(len(vocab))` su CPU. Inserimento e rilevazione sono quindi
  coerenti indipendentemente dal dispositivo dei tensori; il test di equivalenza per riga gira su CPU e su GPU.
- **Dispositivo della rilevazione:** richiede comunque la GPU, perché il forward del modello serve per l'entropia
  (`gpu_for_detect=True`).
- **Vocabolario della green list:** `len(tokenizer.get_vocab())`, cioè il vocabolario del generatore. Per Qwen la
  larghezza dei logit (152.064) è maggiore: i token oltre il vocabolario non vengono mai generati.
- **Coerenza dell'entropia:** in transformers 4.45/4.46 i processor dell'utente si applicano prima di temperature e
  top_p. L'entropia in generazione è quindi calcolata sui logit grezzi, come in rilevazione. L'oracle lo verifica:
  stesso testo generato e stessi punteggi.

## 8. Dipendenze esterne

- Il repository non ha file di requisiti.
- L'import di `lm_eval` richiede Cython/pyximport con un compilatore C, `fcntl` (solo POSIX), `mosestokenizer` e
  `datasets`, e il download di dataset e metriche dall'Hub.
- Lo shim importa solo `sweet.py` e `watermark.py`, quindi servono soltanto torch, transformers, scipy e nltk. Nessuna
  rete e nessun token.

## 9. Patch

**Nessuna.** Il processor gestisce correttamente più sequenze per volta:
- green list, maschera ed entropia sono per riga (`sweet.py:18-28`);
- non c'è stato scalare per sequenza.

Il test di equivalenza per riga (`tests/oracle/sweet_rowwise.py`, decisione dell'utente) lo verifica con 6 sequenze,
alcune ad alta e alcune a bassa entropia.

## 10. Definizioni operative degli stati

- **Embed:** `FAILED` solo su eccezione; `PARTIAL` non si usa.
- **Detect:** `FAILED` in tre casi:
  - nessun token del codice;
  - nessun token generato da valutare (`{"invalid": True}`);
  - nessun token sopra la soglia di entropia. Il repository restituisce il segnale `z = −100.0`, che lo shim traduce in
    `FAILED` (SPEC §9.1) conservando il valore grezzo in `extra["z_score_raw"]`. In M7 vale il punteggio minimo.
