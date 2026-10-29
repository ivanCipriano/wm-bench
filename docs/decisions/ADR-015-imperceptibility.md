# ADR-015 — Impercettibilità e capacità (Milestone 12)

- **Stato:** in corso (Milestone 12). Decisioni dell'utente del 28 ottobre 2026.
- **Contesto:**
  - SPEC §13.4 (impercettibilità), §13.5 (capacità di MCGMark), §13.6 (incertezza), §22 (M12:
    "metriche con IC per L1 test; test di non-sovrapposizione dei problemi tra fold");
  - D9 (accoppiamento approssimato fra baseline e marcato), D37 (estensione di D9), TODO 10 di
    `cluster_info.md` (per MCGMark il riferimento è la baseline gemella);
  - la M11 non è ancora chiusa: lo sviluppo della M12 parte mentre finiscono i calcoli della M11
    (decisione dell'utente del 28 ottobre 2026).

## 1. Celle e riferimenti

Una cella per (metodo congelato, modello, linguaggio) su L1 test, con la configurazione congelata
(ADR-013). Positivi: i campioni marcati. Riferimento (negativi dei classificatori, termine dei
confronti): la baseline LLM dello stesso modello e linguaggio; per MCGMark la **gemella** come
principale e la baseline normale come confronto secondario (metriche con suffisso `_baseline`).
SWEET entra quando sarà congelato.

**Codice vuoto** (estrazione fallita, stringa vuota): escluso da perplexity, CodeBLEU e
classificatori; dalle medie per problema e, nelle coppie, cade la coppia. I campioni con
inserimento `FAILED` o `PARTIAL` che contengono codice restano in tutte le misure: è il codice che
l'utente riceverebbe (importante per MCGMark su L1). Per cella: `empty_fraction_marked` e
`empty_fraction_reference` (Wilson); il report segnala le celle in cui differiscono di più di 2
punti.

## 2. Perplexity (fase `perplexity`, GPU)

- Osservatore `bigcode/starcoder2-7b` (snapshot di `configs/model/starcoder2_7b.yaml`), bf16.
- Solo il codice, senza prompt: tokenizzato senza token speciali, con il token BOS del tokenizer
  davanti (così anche il primo token del codice ha un contesto ed è contato), troncato a 2.048
  token (al più 2.047 di codice). NLL medio per token; perplexity del campione = exp(NLL).
- Un output per sorgente: baseline (comune a tutti i metodi), marcati, gemella di MCGMark
  (`imperceptibility/ppl/...`), con il numero di campioni vuoti e troncati nel manifest.

**Metriche** (`metrics_imperceptibility`, D37):

- **principale per tutti i metodi:** per problema, media dei marcati − media del riferimento;
  `delta_nll_mean` e `delta_nll_median` sui problemi, con IC bootstrap per problema.
  **La misura principale è ΔNLL**; `delta_ppl_mean` e `delta_ppl_median` (stessa costruzione con
  la perplexity del singolo campione) si riportano accanto;
- **secondarie:** per STONE e SWEET l'accoppiamento per indice (`delta_nll_index`,
  `delta_ppl_index`: media per problema delle differenze per coppia); per ACW quello esatto con il
  campione d'origine (`delta_nll_exact`, `delta_ppl_exact`). Per ACW, senza campioni vuoti, la
  differenza delle medie per problema coincide con la media delle differenze esatte: cambia solo
  l'intervallo (test `test_acw_difference_of_means_equals_mean_of_exact_differences`). Lo stesso
  vale per l'accoppiamento per indice di STONE (6 coppie per problema, senza vuoti): nei risultati
  `delta_nll_index` = `delta_nll_mean` a 4 decimali in tutte le celle (`M12_stone.txt`); la
  secondaria per indice di ΔNLL/ΔPPL non aggiunge informazione, quella di CodeBLEU sì.

## 3. CodeBLEU (fase `metrics_imperceptibility`, CPU)

`codebleu==0.7.0`, un solo riferimento per confronto (i riferimenti multipli gonfiano il punteggio
e non sono confrontabili con un riferimento singolo).

- **principale per tutti i metodi (`codebleu`):** ogni campione marcato contro ciascuno dei
  campioni del riferimento dello stesso problema, media per campione e poi per problema;
- **variabilità naturale (`codebleu_natural`):** ogni campione del riferimento contro gli altri
  campioni del riferimento dello stesso problema (per MCGMark: della gemella);
- **differenza (`codebleu_minus_natural`):** principale − naturale per problema, con IC bootstrap
  per problema: ≈ 0 vuol dire che il marcato non si allontana dalla baseline più di quanto due
  campioni della baseline si allontanano fra loro;
- **secondarie:** per STONE e SWEET il confronto per indice (`codebleu_index`, stesso seme, come da
  SPEC: più alto per costruzione); per ACW il confronto con il campione d'origine
  (`codebleu_exact`), mostrato nel report accanto alla principale.

## 4. Classificatori avversari (fasi `adversarial_logreg`, CPU, e `adversarial_unixcoder`, GPU)

Come da SPEC §13.4, `StratifiedGroupKFold(n_splits=5, shuffle=True)` raggruppato per
`problem_key`, **stessi fold** per le due varianti (seme da `(global_seed, "adversarial-folds",
metodo, modello, linguaggio, riferimento)`). `check_folds` verifica a ogni lancio che nessun
problema stia sia nel training sia nel test di un fold e che ogni campione sia testato una volta
(il manifest registra `folds_disjoint_by_problem`; test unitario `test_folds_are_disjoint_by_problem`).

- `adversarial_logreg`: regressione logistica (liblinear) su
  - stile dei nomi (frazioni snake/camel/Pascal fra gli identificatori tree-sitter, lunghezza media,
    distribuzione delle iniziali),
  - istogramma dei tipi di nodo tree-sitter (frazioni),
  - n-grammi di token 1–3, TF-IDF, vocabolario ≤ 20.000 (token: identificatori, numeri, simboli);
  feature dense scalate con `MaxAbsScaler`, vettorizzatori stimati solo sul training del fold;
  `C ∈ {0,01; 0,1; 1; 10; 100}` scelto con una CV interna `StratifiedGroupKFold(3)` per problema
  sull'AUROC;
- `adversarial_unixcoder`: `microsoft/unixcoder-base` con testa di classificazione a 2 classi, max
  512 token, AdamW con lr 2e-5, 3 epoche, batch 16; early stopping: si tiene l'epoca con l'AUROC
  migliore su un 10% interno al training del fold, diviso per problema (`GroupShuffleSplit`).

**Metriche:** `adversarial_<variante>_auroc_oof` (AUROC sulle predizioni out-of-fold aggregate, IC
bootstrap per problema) e `adversarial_<variante>_auroc_fold_mean` (media fra i fold; come
intervallo media ± deviazione standard, `ci_method = mean_pm_sd_folds`). AUROC ≈ 0,5 =
impercettibile.

## 5. Capacità di MCGMark

`message_accuracy`, `bit_accuracy` ed `embedding_success_rate` sono già calcolate dalla fase
`metrics` (M7) con IC; `check_m12.py --report` le riporta accanto alle metriche
d'impercettibilità.

## 6. Esecuzione

`scripts/submit_m12.sh perplexity|logreg|unixcoder|metrics METODO [K/M]`; stato e report
`python scripts/check_m12.py METODO [--report]`. Ordine: perplexity, logreg e unixcoder insieme;
metrics dopo i tre. Risorse in `configs/resources/default.yaml` (`perplexity` e
`adversarial_unixcoder` su gpuq).

## 7. ACW al netto della formattazione (D38)

**Osservazione** (`M12_acw.txt`, `M12_acw_surface.txt`, `scripts/diag_m12_surface.py`): per ACW
UniXcoder ha AUROC **1,000** in tutti i fold e per entrambi i modelli, logreg 0,63–0,71. La causa è
una regola di ACW stessa, non la pipeline: la **regola 45** (`tab_convert_process` in
`third_party/ACW/source/one_rule_format.py`) converte ogni 4 spazi d'indentazione in un tab, è nel
sottoinsieme della chiave congelata e si applica a qualunque codice indentato: il 100% dei marcati
ha tab, contro lo 0% della baseline. Anche la **regola 44** (autopep8 E301/E302/E305/E306) lascia
una traccia: doppie righe vuote nel 24–28% dei marcati contro l'1–6% della baseline. Le feature di
logreg (token, nodi tree-sitter, stile dei nomi) non vedono l'indentazione.

**Misure** (decisione dell'utente del 29 ottobre 2026, secondarie; le principali restano quelle
delle §2–4, con l'AUROC 1,000 e questa causa):

- **classificatori sul codice normalizzato** (fasi `adversarial_logreg_fmtfree`, CPU, e
  `adversarial_unixcoder_fmtfree`, GPU; `imp.format_free`): a marcati e baseline si applica la
  stessa normalizzazione, i tab dell'indentazione diventano 4 spazi (inverso della regola 45) e le
  sequenze di righe vuote si riducono a una (regola 44); stessi fold, stessi iperparametri.
  Metriche `adversarial_<variante>_fmtfree_auroc_oof` e `..._auroc_fold_mean`;
- **rilevamento senza le regole 44 e 45** (fase `metrics_acw_format`, CPU, nessuna nuova
  rilevazione): punteggio = frazione delle regole di k1 diverse da 44 e 45 rimaste invariate, da
  `rule_unchanged` in `DetectionRecord.extra`. Soglia ricalibrata all'1% di FPR sui negativi umani
  di sviluppo L1+L2 (stesse parti e stesso calibratore della fase `calibrate`; la fase ricalcola
  anche la soglia ufficiale sugli stessi negativi, `threshold_official_recomputed`, che deve
  coincidere con quella congelata). Metriche, accanto alle ufficiali ricalcolate con la soglia
  congelata: `tpr_at_fpr_<official|format_free>|<clean|T1.1>`, `auroc_<...>|<...>`,
  `fpr_human_test_<...>`, `threshold_format_free`, `n_rules_k1`, `n_format_rules_in_k1`.

**Limite:** la soglia della variante è calibrata dopo il test, ma solo sui negativi di sviluppo
(I3): è una misura descrittiva, non una configurazione congelata.

**Esecuzione:** `scripts/submit_m12.sh logreg_fmtfree|unixcoder_fmtfree acw 1/1`, poi
`scripts/submit_m12.sh acw_format acw` (dopo i due); report in `check_m12.py acw --report`.

**Per la tesi** (decisione dell'utente): con la chiave congelata ACW è riconoscibile
dall'indentazione (regola 45), e lo stesso tratto lo rende fragile alla riformattazione.

**Esito** (`M12_acw_format.txt`; Qwen / DeepSeek, Python, L1 test; k1 ha 30 regole, fra cui 44 e
45):

| misura | ufficiale | al netto della formattazione |
|---|---|---|
| UniXcoder AUROC OOF | 1,000 / 1,000 | **0,741** [0,715; 0,768] / **0,686** [0,660; 0,711] |
| logreg AUROC OOF | 0,706 / 0,634 | 0,706 / 0,634 (invariato) |
| soglia (1% FPR, dev L1+L2, 2.000 negativi) | 1,0 (ricalcolata = congelata) | **+∞** |
| TPR pulito | 0,998 / 1,000 | 0 / 0 |
| AUROC pulito | 1,000 / 1,000 | 0,952 / 0,953 |
| TPR dopo T1.1 | 0 / 0 | 0 / 0 |
| AUROC dopo T1.1 | 0,949 / 0,948 | 0,952 / 0,952 |
| FPR sui negativi umani di test (928) | 0 / 0 | 0 / 0 |

- **Percettibilità:** tolti tab e righe vuote multiple, UniXcoder scende da 1,000 a 0,74 / 0,69:
  quasi tutta la separazione perfetta viene dalla formattazione. Il resto (0,19–0,24 sopra il caso)
  viene dalle altre 28 regole, coerente con CodeBLEU −0,06 / −0,11 rispetto alla variabilità
  naturale. Logreg non cambia: le sue feature non vedono l'indentazione (attesa, conferma la causa).
- **Rilevamento:** senza 44 e 45 l'ordinamento resta buono (AUROC 0,95) e non risente di T1.1
  (0,952 pulito e dopo T1.1), ma **nessuna soglia finita** rispetta l'1% di FPR: più del 1% dei
  negativi umani di sviluppo (> 20 su 2.000) ha punteggio massimo, cioè è già un punto fisso di
  tutte le altre 28 regole; la soglia diventa +∞ e il TPR 0. Le regole di formattazione quindi non
  servono a riconoscere i marcati (lo fanno anche le altre), ma a **escludere il codice umano**: il
  codice indentato con spazi cambia sempre sotto la regola 45, e questo porta i negativi sotto 1,0.
  Lo stesso tratto spiega la fragilità: T1.1 riformatta, 44 e 45 tornano a cambiare e il punteggio
  ufficiale scende sotto la soglia 1,0 su tutti i marcati (TPR 0, AUROC ancora 0,95).
