# Audit di `mcss` (SPEC §9.0; PIPELINE_metodo.md di multimark)

`mcss` è la reimplementazione di MCSS nella libreria `multimark` (submodule
`third_party/multimark`). L'audit completo (punti 1–9 di SPEC §9.0, con il punto 2 che descrive
gli algoritmi del paper invece del codice) si scrive in P2 (PIPELINE §2, file di P2). Per
ora il file contiene solo la sezione «Deviazioni dal paper», che va scritta quando si prende
ciascuna decisione (PIPELINE §1 punto 7). La fonte delle deviazioni è
`third_party/multimark/docs/mcss_deviations.md`, con gli stessi ID MD<n>.

## Deviazioni dal paper

### MD19 — Variabili di una lettera (`names.single_letter`; PIPELINE v1.2.7 §4.2 punto 9)

**Il problema.** Al passo 2 di p. 5 il paper estende i nomi di una parola con un suffisso e come
esempi porta `n → n_count` e `s → s_str`, cioè proprio nomi di una lettera. Nella stessa frase
dice però che le variabili di un carattere «come `i`» non possono portare uno stile. Il testo si
contraddice, e il codice ufficiale non è disponibile. Il caso rientra fra le discrepanze in cui
un componente non è specificato del tutto, quindi la lettura si sceglie con i dati e lo scarto si
dichiara.

**Le tre letture**, opzione `names.single_letter` della libreria:

- `exclude`: regola (c) di §3.6.1, `len(n) ≥ 2`. Era la lettura usata fino a v1.2.6.
- `extend_except_loop`: i nomi di una lettera diventano siti e si estendono con il suffisso di
  §4.2 punto 4. Fanno eccezione i nomi che nell'unità sono bersaglio di un `for` o di una
  comprehension, anche una sola volta.
- `extend_all`: tutti i nomi di una lettera diventano siti.

**La regola di scelta** è fissata prima di vedere i dati (§4.2 punto 9):

- vince la lettura il cui rapporto fra siti strutturali e variabili, sui file della fascia
  32–39 bit, è più vicino a 0,92 (Tabella 3 di MCSS: 10,9 / 11,9);
- se due letture distano da 0,92 meno di 0,05 l'una dall'altra, vince quella con la media di
  variabili più vicina a 11,9;
- si usano file mai di test.

**Dati.** I dati sono:

- log `docs/milestone_logs/P1_single_letter.txt`, libreria `fb31176`;
- pool allargato `L2_codenet_pool_extra`;
- 27.040 file ammessi con gli stessi filtri della selezione: niente `split == "test"`, niente
  problemi nello stesso cluster di un problema di test, niente errori di parsing;
- profilo `mcss_mod`: forme canoniche del paper, parametri e variabili di modulo inclusi, canali
  1–8.

La fascia si ricalcola su ogni lettura, perché `n_bits_v1` = strutturali + 2 · variabili cambia
con la lettura. Il rapporto è il rapporto delle medie.

| sui file della fascia 32–39 bit | `exclude` | **`extend_except_loop`** | `extend_all` | MCSS T3 |
|---|---:|---:|---:|---:|
| file in fascia | 237 | **693** | 982 | — |
| problemi in fascia | 101 | **169** | 195 | — |
| media `n_bits_v1` | 34,80 | **34,93** | 35,13 | 34,6 |
| media variabili | 10,32 | **12,88** | 13,37 | 11,9 |
| di cui variabili di modulo | 5,16 | **7,22** | 8,25 | — |
| media siti strutturali | 14,15 | **9,17** | 8,40 | 10,9 |
| rapporto strutturali / variabili | 1,37 | **0,71** | 0,63 | 0,92 |
| distanza dal rapporto 0,92 | 0,45 | **0,21** | 0,29 | — |
| distanza dalla media di variabili 11,9 | 1,58 | **0,98** | 1,47 | — |

Sugli stessi 27.040 file ammessi, senza limitarsi alla fascia, il rapporto vale 1,58, 0,64 e
0,54. La media di variabili per file vale 2,01, 4,99 e 5,86.

**Esito.** La lettura scelta per `mcss` è **`extend_except_loop`**. Ha il rapporto più vicino a
0,92, e nessun'altra lettura le sta alla pari entro 0,05: la seconda, `extend_all`, è a 0,29,
cioè 0,08 più lontana. Il criterio della Tabella 3 sui suoi file in fascia è superato: la media di
`n_bits_v1` è 34,93 contro 34,6 (+1,0%, entro ±10%).

La libreria `488fb5a` (pipeline v1.2.7) mette `names_single_letter = "extend_except_loop"` nel
profilo `mcss_profile()`. La base di `multimark` resta su `exclude`; per quella decide la
decisione R di §7 in P5.

**Scarto residuo dal paper**, sui 693 file in fascia con `extend_except_loop`:

| quantità | nostra | paper | scarto |
|---|---:|---:|---:|
| bit | 34,93 | 34,6 | +1,0% |
| variabili | 12,88 | 11,9 | +8,2% |
| siti strutturali | 9,17 | 10,9 | −15,9% |
| rapporto strutturali/variabili | 0,71 | 0,92 | −0,21 (−23%) |

Il paper sta fra `exclude` (rapporto 1,37) ed `extend_except_loop` (0,71). La spiegazione più
probabile è che il paper estenda solo i nomi di una lettera di cui riesce a dedurre il tipo
(«context-inferred suffixes»). Questa regola il testo non la specifica, quindi non si ricostruisce
(regola di integrazione per i componenti non specificati del tutto). Lo scarto si dichiara come
limite della reimplementazione.

Un controllo: con la libreria `fb31176` la rilettura con `exclude` dà la stessa tabella ottenuta
con `1712e38` (stesso sha256, `2fa36ef5…`). Le modifiche alla libreria fra i due commit non
cambiano il conteggio.

**Selezione per l'autovalidazione.** La selezione si rifà sulla tabella `extend_except_loop` già
calcolata: stessa fascia 32–39, stessi filtri, una sottomissione per problema. Ne risultano 169
file attesi (`docs/milestone_logs/P1_mcss_selftest_eel.txt`, selezione
`mcss_selftest_eel_fb311767f65c.jsonl`). La selezione precedente con `exclude` (101 file,
`P1_mcss_selftest_extra.txt`) resta com'è. Le medie sui file scelti si aggiungono qui quando il
log è disponibile.
