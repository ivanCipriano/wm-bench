# Riepilogo della Milestone 6 — Metodi restanti (SPEC §0, §22)

Data: 10 ottobre 2026. Stato: **quasi chiusa**. Resta la generazione di ACW su L1 dev (licenza di Sourcery: alla
scadenza della prova si usa il token di un altro account, decisione del 10 ottobre 2026).

## Criteri di accettazione (SPEC §22)

| Criterio | Esito |
|---|---|
| Oracle verde per ciascun metodo | SWEET, MCGMark, PromptMark, ACW: verdi (log in `M6_cluster.txt`) |
| Audit approvati dall'utente | SWEET approvato; MCGMark, PromptMark, ACW: decisioni dell'utente applicate (da confermare come approvati) |
| ACW `NOT_APPLICABLE` fuori da Python | sì, senza invocare il worker (test unitari) |
| Patch documentate | `patches/<metodo>/README.md` per ogni patch nuova |

## Cosa è stato fatto

### Infrastruttura comune
- Rilevazione generica negli adapter (`Detector.detect_codes`), metodi che generano un campione per item con seme
  proprio, schema dei semi nel manifest (`seed_scheme`), colonne di inserimento per campione (`n_sites`,
  `n_generated_tokens`, iterazioni di PromptMark) e riepiloghi nel manifest (`embed_success_rate`, `n_sites`).
- Baseline gemella (fase `generate_baseline_twin`), metodi post-hoc (`CodeEmbedder`, ramo post-hoc della fase
  `watermark`), lotti nel runner degli shim (`ShimBase.batch_size`), fase `promptmark_freq`.
- Oracle divisibili in parti parallele fra i due utenti, anche dallo stesso clone (`oracle_method.sh METODO K/M`).

### Metodi

| Metodo | Patch nuove | Oracle | Generazione L1 dev |
|---|---|---|---|
| SWEET | nessuna | verde (shim = originale, test per riga) | 8 celle, tutti `OK` |
| MCGMark | 0001 γ fisso (D18), 0002 e 0003 solo prestazioni (3,11×) | verde, catena completa su codice lungo (4 cicli su 9, 12/12 bit) | Python: inserimento riuscito 8,0% (Qwen), 1,7% (DeepSeek); baseline gemella fatta |
| PromptMark | 0001 provider in-process, 0002 soglia e green list parametriche | verde (shim = originale, limiti sull'esecuzione degli esempi senza effetto) | Python: 576 campioni per modello; 2-3 identificatori liberi per campione |
| ACW | nessuna (0000 preesistente motivata) | verde (shim = originale, punteggio per regola) | **da lanciare** |

### Deviazioni nuove
D17 (MCGMark solo Python), D18 (γ fisso), D19 (SWEET anche su JavaScript), D20 (prefill della fence e baseline
gemella di MCGMark), D21 (verifica di MCGMark su CodeNet e ClassEval), D22 (PromptMark solo Python), D23 (ciclo di
PromptMark sui soli esempi del prompt). Aggiornate D1 (12 bit) e D9 (semi per campione).

## Risultati da ricordare

- **SWEET:** con la soglia di entropia di default sono pochi i token valutati (2-6 nei campioni dell'oracle); il δ
  di default (0,5) è più debole di quello di STONE (promemoria per la M7, TODO 8).
- **MCGMark:** il bias pari all'intero scarto dei logit degrada visibilmente il codice (nomi spezzati,
  ripetizioni fino al limite di token). Su L1 pochi campioni arrivano a un ciclo di 24 posizioni (TODO 13).
- **PromptMark:** integrazione verificata (semi, provider, esempi, limiti, rilevazione), ma Qwen 7B non segue
  l'istruzione sulle iniziali: 0/10 inserimenti nell'oracle, anche a temperatura 1,0, e iniziali verdi come la
  baseline (TODO 19).
- **ACW:** separazione perfetta sul piccolo campione dell'oracle (marcato 1,000, umano e baseline ≤ 0,977); la
  regola delle tabulazioni domina; la verifica è "cieca" con tutte le regole (TODO 22).

## Cosa resta aperto

1. **ACW:** generazione su L1 dev (`submit_watermark_l1.sh acw`, circa 2 ore su CPU). Licenza di Sourcery:
   decisione dell'utente del 10 ottobre 2026, alla scadenza della prova si usa il token di un altro account
   (audit di ACW §6); lo shim ripete il canarino dopo ogni lotto e si ferma se Sourcery smette di funzionare.
2. Confermare come approvati gli audit di MCGMark, PromptMark e ACW.
3. Confermare l'esclusione dalle 6 classi ClassEval usate nell'oracle di MCGMark (D21: oggi restano in L3).

## `TODO(user)` e decisioni per le prossime milestone

- SPEC §9.2 (rete dei nodi per Sourcery): **risolto**, Sourcery funziona su `defq` (`tnode01`).
- SPEC §17 (griglie HPO, `TODO(user)`): da proporre in M8; vincoli già decisi nei TODO 6, 7, 12, 16 (stima del
  costo prima di lanciare la griglia di PromptMark) e nell'audit di ACW (`num_transforms` ∈ {10, 20, 30, 43}).
- Promemoria per la M7: TODO 8 (δ di SWEET), 13 (MCGMark su L1), 19 (PromptMark), 20 (punteggio discreto di ACW e
  soglia all'1% di FPR).
- Per M9, M11, M12, M14, M15: TODO 9-11, 14, 15, 17, 18, 21, 22 di `cluster_info` §11.

## Verifica locale finale

ruff e mypy puliti (73 file sorgente); pytest 328 passati, 89 saltati (oracle e test del cluster).
