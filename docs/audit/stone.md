# Audit di STONE (SPEC §9.0)

- **Stato:** approvato dall'utente il 5 ottobre 2026 (Milestone 5), con le decisioni riportate nei punti 3, 4, 5, 7 e 9.
- **Repository:** `inistory/STONE-watermarking`, paper "Marking Code Without Breaking It" (arXiv 2502.18851v4).

## 1. Commit e data

- Commit `bb5d809c0c494a219411e861f2313cca2b9fd7b4` del 27 marzo 2026 (submodule `third_party/STONE-watermarking`).
- Il codice eseguito è la copia `build/patched/stone/` (ADR-002) con le patch:
  - `0000`, preesistente: solo `run.py` e `evaluation/stem.py` (formato dei `task_id` e metrica STEM), nessun effetto su
    inserimento e rilevazione;
  - `0001`, Milestone 5: decisione sintattica per riga (punto 9).
- Ambiente `stone`: Python 3.9.19, torch 2.4.1+cu121, transformers 4.45.1.

## 2. Punto d'innesto

Il codice sta in `stone_implementation/` e deriva da MarkLLM (THU-BPM, Apache-2.0).

- **Costruzione:** `watermark/auto_watermark.py::STONEAutoWatermark.load("STONE", transformers_config, skipping_rule,
  watermark_on_pl, gamma, delta, hash_key, z_threshold, prefix_length, language)` → `watermark/stone/stone.py::STONE`.
  `transformers_config` è un `utils/transformers_config.py::TransformersConfig(model, tokenizer, vocab_size, device)`.
- **Inserimento:** `STONE.logits_processor` (`STONELogitsProcessor`).
  - Lo shim lo passa a `model.generate` con il proprio prompt chat (chat template nativo, system prompt del framework) e
    il decoding neutro.
  - `STONE.generate_watermarked_text` non si usa: tokenizza il prompt grezzo senza chat template e decodifica anche il
    prompt insieme al testo generato.
- **Rilevazione:** `STONE.detect_watermark(code)` → `STONEUtils.score_sequence(input_ids)`. Il codice viene tokenizzato con
  `add_special_tokens=False`.
- **Un'istanza `STONE` per linguaggio**, perché `language` è un parametro del costruttore e sceglie le liste sintattiche.
  Il modello si carica una sola volta.
- **Dispositivo della rilevazione** (constatato in M5): `STONEUtils` crea `torch.Generator(device=config.device)` e la
  green list è `torch.randperm(vocab_size, generator=rng)` sul dispositivo. Con lo stesso seme, la permutazione su
  CUDA è diversa da quella su CPU.
  - La rilevazione usa solo il tokenizer, ma **deve girare su CUDA** come l'inserimento, altrimenti gli z-score non
    corrispondono.
  - ⇒ `gpu_for_detect=True` (`configs/resources/default.yaml`), diversamente dalla matrice provvisoria di SPEC §7.2.
    Il modello non viene caricato per `detect`.

## 3. Iperparametri

| Protocollo | Nativo | Note |
|---|---|---|
| frazione della green list γ | `gamma` | |
| intensità del bias δ | `delta` | |
| chiave | `hash_key` | intero `derive_seed(global_seed, "wm-key", "stone", key_id) % 2**31` (SPEC §9.6) |

- **Fissi, come in `run.py`:**
  - `skipping_rule="all_pl"`: parole chiave, operatori, delimitatori, spazi e tipi sono tutti sintattici;
  - `watermark_on_pl="False"`: il watermark va sui token non sintattici;
  - `prefix_length=0`;
  - `z_threshold=10.0`: soglia nativa, solo diagnostica (`native_decision`, D2).
- **Valori del repository** (`run.sh`): γ = 0,5 sempre; **δ = 0,5 per HumanEvalPack e δ = 1,0 per MBPP+**;
  `hash_key = 15485863`.
- **Default del framework** (decisione dell'utente): γ = 0,5 e δ = 1,0 per tutti i linguaggi e dataset.
  - Serve **solo fino all'HPO**.
  - La griglia HPO di STONE (Milestone 8) deve contenere entrambi i valori del repository, δ = 0,5 e δ = 1,0, più
    eventuali valori intermedi o più alti, così che la configurazione finale la scelgano i dati.
- **Contesto della green list:**
  - con `prefix_length=0`, `STONEUtils._seed_rng` usa `prev_token = 1` (prodotto vuoto), quindi il generatore viene
    inizializzato con `hash_key` a ogni passo;
  - la green list è **la stessa per ogni posizione**, cioè non dipende dai token precedenti. È il comportamento del
    repository e si mantiene.

## 4. Linguaggi supportati

- Le liste di token sintattici esistono solo per `python`, `cpp` e `java`, scritte nel codice sia di `score_sequence`
  sia del processor.
- Con `language="javascript"`:
  - `score_sequence` va in errore (`keywords` e le altre liste non sono definite);
  - il processor ha liste vuote, quindi marca tutti i token come KGW.
- ⇒ **JavaScript: `NOT_APPLICABLE`** (decisione dell'utente). L'adapter non invoca il worker.

## 5. Punteggio di rilevazione e direzione

Più alto vuol dire più probabilmente marcato.

- **Paper** (§3.1, Algoritmo 2): `z = (N_GE − γ·N_E) / sqrt(γ(1−γ)·N_E)`.
  - N_E è il numero di token non sintattici ("etc tokens");
  - N_GE è il numero di token verdi fra questi.
- **README del repository:** `z = (|X|_G − γ|X|) / sqrt(γ(1−γ)|X|)`, con |X| descritto come "numero totale di token del
  codice".
- **Codice** (`watermark/stone/stone.py`, commit fissato):
  ```python
  183  target_num = 0
  ...
  192  else:                                    # watermark_on_pl == "False"
  193      if d in syntax_tokens or d.strip() in syntax_tokens: pass
  194      else: target_num += 1                # conta i token NON sintattici
  196  num_tokens_scored = (len(input_ids) - self.config.prefix_length - target_num)   # = token SINTATTICI
  227  green_token_count = sum(... green_token_flags[i] == 1 and weights[i] == 1)      # verdi fra i NON sintattici
  228  z_score = self._compute_z_score(green_token_count, num_tokens_scored)
   84      expected_count = self.config.gamma
   85      numer = observed_count - expected_count * T
   86      denom = sqrt(T * expected_count * (1 - expected_count))
  ```
- ⇒ Nel codice, T è il numero di token **sintattici**, sia in `γ·T` sia nel denominatore. Nel paper è N_E, il numero di
  token non sintattici. La discrepanza è confermata.
- **Decisione dell'utente:**
  - `score` è lo z-score del **codice ufficiale**, per fedeltà al metodo pubblicato;
  - lo shim salva anche, in `extra`:
    - `z_nonsyntax = (N_GE − γ·N_E) / sqrt(γ(1−γ)·N_E)`, la formula del paper, che vale `None` se N_E < 1;
    - `n_syntax`, `n_nonsyntax`, `n_green_nonsyntax`;
  - in M7 si confrontano AUROC e TPR con i due punteggi.

## 6. Decoding cablato da sovrascrivere

- `run.py` usa: nessun chat template, `min_length=200`, `no_repeat_ngram_size=4`, `do_sample=True`, `max_new_tokens`
  200 (MBPP+) o 512 (HumanEvalPack).
- Lo shim usa il **decoding neutro della baseline** (ADR-006, cluster_info §11).
  - Il dizionario completo arriva nel request, costruito da `bench.generation.decoding.neutral_settings`.
  - Dal modello si prendono solo eos e pad.
  - Si usano chat template e system prompt del framework, e lo **stesso seme per problema** della baseline
    (`derive_seed(global_seed, "gen", model_id, problem_key, language)`). I 6 campioni sono generati in una chiamata con
    `num_return_sequences=6`.

## 7. Dimensione della green list (adattamento al generatore)

- `run.py` passa `vocab_size=50272`, il vocabolario del modello usato dagli autori, non una proprietà del metodo.
- `TransformersConfig` (`utils/transformers_config.py:37`) usa `len(tokenizer)` se `vocab_size` è `None` o supera la
  dimensione del tokenizer.
- È quindi un **parametro**: lo shim passa `vocab_size=None`, cioè **`len(tokenizer)` del generatore**, senza patch.
  - Qwen2.5-Coder: circa 151.665 token invece di 50.272. Con 50.272, i token con id più alto non sarebbero mai verdi.
  - DeepSeek-Coder: 32.256. È uguale a quanto avrebbe fatto il repository, perché 50.272 > `len(tokenizer)`.
- Registrato come adattamento al generatore in `docs/deviations.md` (D15). L'oracle usa lo stesso vocabolario.

## 8. Dipendenze esterne

- Nessuna rete e nessun token nello shim. `run.py` legge `HF_ACCESS_TOKEN` e carica StarCoder2 per la perplexity, ma lo
  shim non li usa. `utils/openai_utils.py` non viene importato.
- La copia interna `bigcode-evaluation-harness/` del repository differisce da quella ufficiale (per esempio
  `bigcode_eval/tasks/apps.py`, `custom_metrics/code_eval.py`,
  `custom_metrics/multiple_metrics/containerized_eval.py`). Il framework usa il submodule ufficiale (cluster_info §9) e
  non la copia interna.
- Il repository contiene anche processori di altri metodi (`watermark/kgw`, `ewd`, `sweet`): **non** si usano
  (SPEC §9.3).

## 9. Patch

- **`0001-rowwise-syntax-mask.patch`** (`patches/stone/README.md`):
  - riga 279: `next_token = decode(torch.argmax(raw_probs))` prende l'argmax sull'intero tensore `[B, V]`;
  - righe 376–389: `pl_mask` ha forma `[[bool]]` e viene applicata a tutte le righe;
  - con più sequenze per volta ogni riga riceve quindi la decisione calcolata su un indice appiattito che non le
    appartiene;
  - la patch calcola argmax e decisione **per riga**; con una sola sequenza il comportamento è identico all'originale.
- Nessun'altra patch: il vocabolario si imposta da configurazione e il denominatore dello z-score resta quello
  ufficiale.

## 10. Definizioni operative degli stati

- **Embed:**
  - `FAILED`: eccezione durante la generazione;
  - `OK`: altrimenti, anche con testo vuoto. L'estrazione fallita è uno stato dell'orchestratore (`extraction_ok`).
  - `PARTIAL` non si usa; `NOT_APPLICABLE` per JavaScript.
- **Detect:**
  - `FAILED` se `score_sequence` solleva `ValueError` perché nessun token è sintattico (T < 1), o per un'altra eccezione.
    In M7 vale il punteggio minimo.
  - `z_nonsyntax` vale `None` se N_E < 1.
