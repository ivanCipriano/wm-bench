# ADR-014 — Attacchi T2–T4: definizioni operative

- **Stato:** in corso (Milestone 11). Sezione 1 (T2.1) accettata il 26 ottobre 2026; le sezioni
  degli altri attacchi si aggiungono man mano.
- **Contesto:**
  - SPEC §12 (attacchi), §18 (semi), §21.3 (proprietà: semantica conservata sulle canoniche);
  - ADR-012 (infrastruttura degli attacchi T1: griglie, `ToolRunner`, `TOOL_ERROR`);
  - D33 (T2.2), D34 (controllo negativo di T2.2(b)), D35 (regole Ruff di T2.1).

## 1. T2.1 refactoring a regole

Decisioni dell'utente del 26 ottobre 2026. Codice in `src/bench/attacks/t2/refactor.py`, griglia
in `configs/attack/t2_1_refactor.yaml`. È la definizione di T2.1 da riportare nella tesi.

### 1.1 Il parametro b

Lo SPEC dà la griglia `b ∈ {1, 2, 3, all}` senza precisarla. Definizione:

- **b = numero di tipi di regola** estratti con l'RNG dell'attacco (seme di SPEC §18) fra i tipi
  **applicabili al campione**; ogni tipo estratto si applica in **tutti** i punti in cui vale, come
  fanno ACW e i tool a regole. `all` = tutti i tipi applicabili;
- se i tipi applicabili sono meno di b, si applicano tutti quelli disponibili;
- se nessun tipo è applicabile, il campione è `UNCHANGED` e resta nelle metriche (I1);
- il dettaglio di ogni campione registra tipi applicabili (`n_types_applicable`), tipi estratti
  (`chosen`), tipi e punti effettivamente trasformati (`applied`, `n_types`, `points`,
  `n_points`) e tipi scartati (`rejected`);
- il report (`check_attacks.py --report`) dà per ogni valore di b, metodo, modello e linguaggio il
  numero medio di tipi effettivamente applicati e la frazione di campioni `UNCHANGED`.

### 1.2 Catalogo e restrizioni

Ogni restrizione evita un cambio di semantica; la colonna "motivo" va riportata nella tesi.

| Tipo | Linguaggi | Trasformazione | Restrizione | Motivo |
|---|---|---|---|---|
| `while_to_for` | Java, C++, JS | `while (c) b` → `for (; c; ) b` | niente condizioni-dichiarazione (C++) | `for (; int x = f(); )` non è sempre equivalente |
| `for_to_while` | Java, C++, JS | `for (i; c; u) b` → `{ i while (c) { b u; } }` | niente `continue` che riguarda il ciclo | salterebbe l'aggiornamento `u` |
| | | | niente cicli con etichetta | `continue etichetta` salterebbe l'aggiornamento |
| | | | niente `for (; c; )` senza inizializzazione né aggiornamento | è già un `while`: trasformarlo annullerebbe `while_to_for` |
| | | | Java: niente corpi che finiscono con `return`, `break` o `throw` (se c'è un aggiornamento) | l'aggiornamento in coda sarebbe irraggiungibile: `javac` rifiuta le istruzioni irraggiungibili |
| | | | niente condizioni-dichiarazione (C++) | non esprimibili nel `while` |
| | | | JS: niente `let`/`const` nell'inizializzazione con funzioni nel corpo | il `for` crea un legame per iterazione che le chiusure vedono; il `while` uno solo |
| | | | l'inizializzazione va in un blocco `{ }` | le variabili restano visibili solo nel ciclo |
| `for_to_while` | Python | `for v in range(a, b, s)` → `v = a` / `while v < b:` / `v += s` | solo `range` con passo letterale non nullo (`>` se negativo) | il confronto dipende dal segno del passo |
| | | | niente `else` del ciclo | la trasformazione non lo riporta (andrebbe spostato sul `while` con la stessa semantica di `break`); escluso per semplicità |
| | | | niente `continue` che riguarda il ciclo | salterebbe `v += s` |
| | | | `v` mai riassegnata nel corpo | il `for` la reimposta a ogni giro, il `while` no |
| | | | `v` non nominata altrove nella funzione (nemmeno negli argomenti di `range`) | dopo il ciclo `v` avrebbe un altro valore (`b` invece dell'ultimo); prima del ciclo il `for` su un intervallo vuoto non la tocca |
| | | | `b` in una temporanea se non è un letterale o un nome mai riassegnato nel corpo | `range` valuta `b` una sola volta |
| | | | corpo su righe proprie | serve l'indentazione del corpo per aggiungere `v += s` |
| `if_else_inversion` | tutti | `if c A else B` → `if !(c) B else A` (Python `not (c)`) | niente catene `else if`/`elif` | l'inversione della catena cambierebbe l'ordine dei test |
| | | | C++: niente `if constexpr` né `if (init; c)` / `if (T x = …)` | la condizione non è un'espressione da negare |
| | | | rami senza graffe racchiusi fra graffe | `else` pendente: `if (!c) if (x) y; else A` legherebbe l'`else` all'`if` interno |
| | | | Python: blocchi con la stessa indentazione, su righe proprie | lo scambio dei testi richiede la stessa colonna |
| `compound_assignment` | tutti | `x op= e` → `x = x op (e)` | `x` identificatore semplice | `a[f()] += 1` valuterebbe `f()` due volte |
| | | | solo come istruzione o aggiornamento di un `for` (non dentro un'espressione) | il valore dell'espressione non cambia, ma si limita la trasformazione ai casi ovvi |
| | | | Python: `+=`, `-=` solo con `e` letterale numerico; mai `*=`, `\|=`, `&=`, `^=`, `@=` | su liste, insiemi e array la forma composta modifica l'oggetto (visibile da altri riferimenti), quella espansa ne crea uno nuovo; `l *= 2` vale anche per le liste |
| | | | Java: solo locali o parametri `double` (`+ - * / %`), `String` (`+=`), `int`/`long` con `e` letterale intero, `boolean` (`& \| ^`) | la forma composta ha un cast implicito: `short s; s += 1` → `s = s + (1)` non compila, `int k; k += 1.5` nemmeno |
| | | | C++ (catalogo versione 2, §1.6): solo locali o parametri con tipo dichiarato aritmetico (`primitive_type` o `sized_type_specifier`: `int`, `long long`, `double`, `char`, `bool`, `size_t`, …), dichiaratore semplice; niente `string`, `auto`, puntatori, riferimenti, array, tipi non trovati | `operator+=` di una classe può accettare conversioni che `operator+` non accetta: `string s; s += 'a' + k` (int → char) compila, `s = s + ('a' + k)` no |
| | | | JS: mai `&&=`, `\|\|=`, `??=` | assegnano solo a volte |
| `commutative_swap` | tutti | `a * b`, `a == b`, `a != b`, `===`, `!==` → operandi scambiati | operandi identificatori o letterali numerici, almeno un identificatore | nessun effetto collaterale né ordine di valutazione da conservare |
| `comparison_flip` | tutti | `a < b` → `b > a`, `a <= b` → `b >= a` | come sopra | come sopra |

Regole comuni:

- **ordine fisso** dei tipi AST (`while_to_for` prima di `for_to_while`; i `for (; c; )` creati da
  `while_to_for` non si ritrasformano); in Python le correzioni di Ruff vengono prima dei tipi AST;
- **annidamento:** in un tipo, le occorrenze annidate si trasformano insieme (riscrittura
  ricorsiva dell'albero);
- **controllo sintattico:** un tipo si scarta (`rejected`) se il risultato non si analizza più
  (tree-sitter; in Python anche `ast.parse`) mentre l'originale sì;
- **nomi nuovi** (temporanea di `stop` in Python): `NameFactory` di T1, senza collisioni.

### 1.3 Python: regole Ruff equivalenti a quelle di ACW

D35: lo SPEC parla di "regole Ruff estese usate da ACW", ma ACW usa Sourcery. Si usano le regole
di Ruff 0.7.4 equivalenti alle regole Sourcery che ACW usa: 15 tipi, che coprono 16 delle 33
regole (tabella in `docs/attacks/t2_1_ruff_sourcery.md`). Versione e lista sono fissate nella
configurazione (l'attacco si ferma se la versione installata è diversa). Un tipo Ruff è
applicabile se `ruff check` trova violazioni correggibili; si applica con `ruff check --fix
--unsafe-fixes --preview` sui soli codici dei tipi estratti.

### 1.4 Sovrapposizione con ACW

Su ACW l'attacco è **in parte mirato**: i tipi Ruff sono per costruzione equivalenti a regole
Sourcery di ACW, e due tipi AST ai suoi riordini (37 `a * b`; 38 `<`/`>`; 39 `<=`/`>=`). Ogni
campione registra le regole ACW equivalenti a ogni tipo applicato (`acw_equivalent`); il report
dà per ACW la quota dei tipi applicati equivalenti a una delle 30 regole della configurazione
congelata (lette dalla rilevazione dei positivi). Va dichiarata nella tesi accanto ai risultati
di ACW.

### 1.5 Verifiche prima del lancio

- canoniche di L1 Python con `b = all`: i test devono continuare a passare; ogni canonica rotta si
  attribuisce al tipo che la rompe (un tipo alla volta). Le regole Ruff che rompono codice si
  riportano all'utente prima di toglierle;
- baseline di L1 dev, entrambi i modelli, tutti i linguaggi, `b = all`: frazione dei campioni che
  passavano e passano ancora, per linguaggio, attesa **≥ 99%**. Per ogni campione rotto, il tipo
  responsabile: un caso non previsto diventa una nuova restrizione e il controllo si ripete.

Comando: `scripts/check_attacks_canonical.sh --attacks T2.1 --params b=all [--baseline-dev]`.

**Esito (26 ottobre 2026):** canoniche 541/542 (Python) e 164/164 negli altri linguaggi; baseline di L1 dev 99,52% (Python), 100% (Java, C++, JavaScript). I 5 fallimenti sono `TIMEOUT` di programmi al limite di tempo, non errori di semantica (si ripetono anche con il solo `comparison_flip`, che non cambia il costo): nessuna restrizione aggiunta. Log: `M11_t2_1_canonical.txt`, `M11_t2_1_baseline_dev.txt`.

### 1.6 Correzione dopo il primo lancio: catalogo C++ versione 2 (27 ottobre 2026)

**Caso.** Sul primo lancio di L1 test, T2.1 ha rotto la compilazione di 2–5 campioni C++ di STONE
su 840 per modello (ΔPass@1 rispetto al marcato 0,24–0,60 pp; `M11_attacks_t2_1_stone.txt`),
tutti con `compound_assignment` (`M11_t2_1_stone_cpp_regressions.txt`):

- humaneval/89 (Qwen, campioni 0, 1, 3, 4, 5): `result += 'a' + (c - 'a' + 4) % 26` →
  `result = result + ('a' + (c - 'a' + 4) % 26)`;
- humaneval/11 (DeepSeek, campioni 0, 1, 3, 4): `result += ((a[i] - '0') ^ (b[i] - '0')) + '0'` →
  `result = result + (((a[i] - '0') ^ (b[i] - '0')) + '0')`.

Su `std::string`, `operator+=(char)` accetta l'`int` con una conversione implicita, mentre
`operator+(string, …)` è un template e la deduzione fallisce (`string + int` non compila). Le
verifiche di §1.5 non lo avevano visto perché né le canoniche né la baseline di dev contenevano
questo schema.

**Decisione dell'utente.** La soglia del 99% serviva a cercare casi non previsti, non ad
autorizzarli: ogni rottura dovuta a una trasformazione si chiude con una restrizione. In C++
`compound_assignment` si applica solo a variabili locali o parametri con tipo dichiarato
aritmetico (stessa logica di Java; riga C++ della tabella di §1.2).

**Versione del catalogo per linguaggio.** `configs/attack/t2_1_refactor.yaml` ha
`catalog_version: {python: 1, java: 1, cpp: 2, javascript: 1}`. Nell'`attack_params_hash` di una
cella entra solo la versione del suo linguaggio, e solo se diversa da 1
(`src/bench/attacks/base.py`): cambiano solo le 8 celle C++ di STONE (4 valori di `b` × 2
modelli), che si rilanciano; Python, Java e JavaScript tengono hash e risultati. Il codice
conserva il comportamento della versione 1 (la restrizione vale da `catalog_version` 2), quindi
le celle vecchie restano riproducibili. Con il nuovo hash cambia anche l'RNG dei campioni
(SPEC §18): con `b` < all i tipi estratti in C++ possono essere diversi da quelli del primo lancio.

**Risultati superati.** Le celle C++ della versione 1 restano con il loro hash; nel manifest di
attacco, esecuzione, rilevazione e metriche c'è `extra.superseded` (hash nuovo, versioni, motivo),
scritto da `scripts/supersede_attack_cells.py`. I report (`check_attacks.py`) leggono solo le
celle con l'hash corrente.

**Verifica ripetuta.** Canoniche e baseline di L1 dev in C++ con `b = all`: nessun campione che
passava deve fallire per T2.1. **Esito (27 ottobre 2026, commit 52ef9b1):** canoniche 164/164 (151 modificate, 0 rotte); baseline di L1 dev 217/217 (Qwen 120/120, DeepSeek 97/97). Log: `M11_t2_1_cpp_v2_canonical.txt`, `M11_t2_1_cpp_v2_baseline_dev.txt`. Rilancio delle 8 celle C++ di STONE autorizzato.

**Rilancio (27 ottobre 2026).** Le 8 celle C++ della versione 2 sono complete: 0 campioni con esito cambiato rispetto al marcato per ogni `b` e modello (`M11_t2_1_stone_cpp_regressions_v2.txt`), ΔPass@1 rispetto al marcato 0,00 pp (prima 0,24–0,60). `compound_assignment` scende da 294 a 219 punti (Qwen, `b = all`) e da 240 a 190 (DeepSeek); campioni `UNCHANGED` da 20,1% a 21,6% (Qwen) e da 12,5% a 12,9% (DeepSeek); TPR invariato (0,0107 e 0,0024). Le celle Python e Java del report sono identiche a prima (`M11_attacks_t2_1_stone.txt`).

## 2. T2.4 troncamento

Decisioni dell'utente del 26 ottobre 2026. Codice in `src/bench/attacks/t2/truncate.py`, griglia
in `configs/attack/t2_4_truncate.yaml` (`p ∈ {0.75, 0.5}`).

### 2.1 Istruzione, conteggio, arrotondamento

- **Corpo bersaglio:** con la funzione d'ingresso del problema (`entry_point`, L1) il corpo di quella
  funzione (la prima con quel nome; in JavaScript anche `const f = (…) => {…}`). Senza funzione
  d'ingresso (programmi completi di CodeNet, L2): il corpo di `main` se esiste una funzione con
  quel nome; altrimenti, per Python e JavaScript, le istruzioni di **primo livello del file**,
  escluse le definizioni di funzioni (anche `const f = () => …` e le funzioni decorate) e le
  importazioni (`import`, `from … import`, `require(…)`). In Java e C++ senza `main` l'attacco
  non si applica.
- **Istruzione:** figlio diretto con nome del corpo, esclusi i commenti e la docstring di Python
  (che resta). Più istruzioni sulla stessa riga contano separatamente (`int n; cin >> n;` = 2).
- **Quante se ne tengono:** le prime `max(1, ⌊p·n⌋)`. Il corpo non resta mai vuoto (in Python sarebbe
  un errore di sintassi). Con `p = 0.5`: n = 1, 2, 3, 4, 5 → 1, 1, 1, 2, 2; con `p = 0.75`: 1, 1, 2, 3, 3.
- **Taglio:** prefisso puro, senza aggiungere nulla. Nel corpo di una funzione si toglie il tratto
  dalla fine dell'ultima istruzione tenuta alla fine dell'ultima istruzione (commenti compresi;
  la graffa di chiusura resta). A livello di file si tolgono le singole istruzioni contate oltre
  la soglia; importazioni e definizioni restano dove sono. Il codice può non compilare (Java senza
  `return`) o fallire i test: lo SPEC lo prevede.

Esempi (`p = 0.5`, n = 4 → si tengono 2 istruzioni):

| Linguaggio | Prima | Dopo |
|---|---|---|
| Python (L1) | `def f(a):` / `"""doc"""` / `s = 0` / `s += a` / `s *= 2` / `return s` | `def f(a):` / `"""doc"""` / `s = 0` / `s += a` |
| Java (L1) | `int f(int a) { int s = 0; s += a; s *= 2; return s; }` | `int f(int a) { int s = 0; s += a; }` (non compila: manca `return`) |
| C++ (L1) | `int f(int a) { int s = 0; s += a; s *= 2; return s; }` | `int f(int a) { int s = 0; s += a; }` |
| JavaScript (L1) | `const f = (a) => { let s = 0; s += a; s *= 2; return s; };` | `const f = (a) => { let s = 0; s += a; };` |
| C++ (CodeNet) | `int main() { int n; cin >> n; n *= 2; cout << n; return 0; }` (n = 5) | `int main() { int n; cin >> n; }` |
| Java (CodeNet) | corpo di `public static void main(String[] args)` | prime ⌊p·n⌋ istruzioni di `main` |
| Python (CodeNet) | `import sys` / `def g(x): …` / `n = int(input())` / `m = g(n)` / `print(m)` / `print(m + 1)` (n = 4) | `import sys` / `def g(x): …` / `n = int(input())` / `m = g(n)` |
| JavaScript (CodeNet) | `const fs = require("fs")` / `const lines = …` / `function g(x) {…}` / `const n = …` / `console.log(…)` / `console.log(…)` (n = 4) | `const fs = require("fs")` / `const lines = …` / `function g(x) {…}` / `const n = …` |

### 2.2 Stati e report

- `UNCHANGED`: si tengono tutte le istruzioni (corpo di una sola istruzione, o freccia con
  un'espressione); il campione resta nelle metriche.
- `NOT_APPLICABLE` **per campione** quando l'attacco non si può applicare: codice vuoto (estrazione
  fallita, `reason: extraction_failed`), funzione d'ingresso non trovata (`entry_not_found`),
  `main` assente in Java e C++ (`main_not_found`). È diverso da "applicato ma codice invariato". Il
  campione resta nelle metriche con il codice originale (I1): esecuzione e rilevazione lo trattano
  come ogni altro campione, quindi lo stato per campione non cambia il modello dei dati e nessun
  altro attacco ne ha bisogno (negli altri l'assenza di punti è `UNCHANGED`).
- Report (`check_attacks.py --report`, per ogni metodo, modello, livello e linguaggio):
  - frazioni di `UNCHANGED`, `NOT_APPLICABLE` e `TOOL_ERROR` su tutti i campioni, con i motivi dei
    `NOT_APPLICABLE`;
  - distribuzione di n (minimo, quartili, massimo, quota con n = 1) e istruzioni tolte in media;
  - esecuzione dei campioni modificati: non compilano / compilano ma falliscono / passano;
  - rilevazioni `FAILED` (rilevatore in errore sul codice troncato), per metodo;
  - TPR secondario sui soli campioni modificati (`APPLIED`; esclusi `UNCHANGED`, `NOT_APPLICABLE`,
    `TOOL_ERROR`) accanto al TPR principale, che li comprende tutti. Le ultime tre righe valgono
    per tutti gli attacchi.

### 2.3 Variante in k funzioni (rinviata alla Milestone 13)

Fissata ora, prima di vedere i dati (TODO 32 in `docs/cluster_info.md`):

- si applica solo a codice a livello di file: ClassEval (L3) e programmi CodeNet di L2 test con
  **almeno 2 funzioni**, per tutti i metodi applicabili;
- le k funzioni da **tenere** si estraggono con l'RNG dell'attacco (`derive_seed`, SPEC §18) fra le
  funzioni del file; k va da 1 al numero totale di funzioni (curva in k);
- ClassEval: resta la classe con i soli k metodi scelti;
- prima del lancio, stima dei costi della curva.

Due punti da confermare con l'utente prima dell'implementazione (lettura letterale provvisoria:
contano come le altre funzioni): se `main` dei programmi CodeNet e `__init__` delle classi di
ClassEval rientrano fra le funzioni estraibili.

## 3. T3.1 ri-marcatura con una chiave diversa

Decisioni dell'utente del 27 ottobre 2026. SPEC §12: "ACW: riapplicazione con `k2`. Metodi in
generazione: rigenerazione con stesso modello e metodo e chiave `k2`"; rilevazione con entrambe le
chiavi e quale watermark viene rilevato (k1, k2, entrambi, nessuno).

### 3.1 Che cosa si rigenera (interpretazione dello SPEC)

Il codice finale deve **derivare da quello marcato con k1**: lo SPEC chiede quale watermark viene
rilevato, e rigenerare da zero dal prompt del problema darebbe un codice che non contiene k1 per
costruzione. Così T3.1 è anche coerente con ACW, dove k2 si applica al codice marcato con k1.

- **Metodi in generazione** (STONE, MCGMark, PromptMark; SWEET quando sarà congelato): il
  generatore dello stesso metodo, con la configurazione congelata e `key_id = k2`, **riscrive** il
  campione marcato con k1 usando i testi già approvati di T2.2(a): system prompt
  `configs/prompt/attacks/system.txt` e prompt `conservative.txt` (SHA-256 fra i parametri della
  griglia, come T2.2). Decoding di generazione del metodo e del livello (L1: temperatura 0,2,
  top-p 0,95, `max_new_tokens` 512), un campione per campione marcato, non il greedy
  dell'attaccante di T2.2. Seme del campione da `derive_seed(global_seed, "attack", "T3.1",
  params_hash, sample_id)` (SPEC §18). Codice estratto con la regola D4.
  - MCGMark: regole già adottate (prefill della fence, D20; solo Python); messaggio atteso del
    campione marcato (non dipende dalla chiave).
  - PromptMark: il ciclo di feedback usa la green list di k2 (la chiave della richiesta).
- **ACW** (post-hoc): ACW riapplicato con k2 al codice marcato con k1, stesse 30 regole
  congelate; cambia solo la chiave (k2 sceglie un altro sottoinsieme). Con 30 regole su 43 la
  sovrapposizione attesa fra i due sottoinsiemi è 30 · 30 / 43 ≈ 21 regole. Il lancio di ACW,
  sospeso in attesa della conferma sulla licenza di Sourcery, è stato sbloccato dall'utente il
  27 ottobre 2026.
- **Stati per campione:** `APPLIED` se il codice cambia, `UNCHANGED` se resta uguale (o, per
  ACW, se nessuna regola di k2 lo modifica), `TOOL_ERROR` con il codice marcato se la generazione o
  l'estrazione falliscono (I1, I5), `NOT_APPLICABLE` per i linguaggi non supportati.
- **Variante aggiuntiva** (solo se avanza tempo): rigenerazione da zero dal prompt con k2, come
  misura della specificità della chiave.

### 3.2 Soglia di k2

La simmetria fra chiavi vale solo in media (nel codice pochi token frequentissimi possono essere
verdi con una chiave e rossi con l'altra; per ACW e PromptMark la chiave cambia direttamente cosa
si misura). Quindi:

1. rilevazione con k2 dei negativi umani di sviluppo (stessi soggetti della calibrazione
   definitiva di k1: nativi di L1 e di L2, integrazione combinata L1+L2);
2. calibrazione della soglia di k2 con la stessa procedura di k1 (FPR obiettivo, punteggio
   principale del metodo, punteggi secondari), output
   `thresholds/<metodo>/<modello>/<lang>/L1L2/k2/<cfg>.json`;
3. **congelamento** della soglia di k2 prima di rilevare il test (I3): `freeze/<metodo>.k2.json`
   con lo sha256 di ogni soglia; dopo il congelamento la calibrazione di k2 rifiuta il metodo;
4. rilevazione con k2 dei negativi umani di **test**, solo come verifica: FPR effettivo con k2.

Le rilevazioni con k2 stanno in `detection/<metodo>/<modello>/<cfg>/k2/<soggetto>/...` (quelle di
k1 non cambiano percorso). Il contesto di condizionamento è quello del problema, come per tutti gli
attaccati (SPEC §9.1).

### 3.3 Report per cella

Rilevazione di ogni campione attaccato sia con k1 (fase `detect` normale, soglia congelata di k1)
sia con k2 (soglia congelata di k2). Per cella, tutte con intervallo di confidenza (I7):

- **TPR con k1** (metrica principale, come per gli altri attacchi) e AUROC con k1, Pass@1 e
  ΔPass@1 rispetto alla baseline e al marcato (fase `metrics`);
- le quattro categorie per campione: **solo k1**, **solo k2**, **entrambi**, **nessuno**;
- **TPR con k2** e **AUROC con k2** (contro i negativi umani di test rilevati con k2);
- **FPR effettivo con k2** sui negativi umani di test (Clopper-Pearson);
- frazioni di `UNCHANGED`, `NOT_APPLICABLE` e `TOOL_ERROR`;
- le due soglie (k1 e k2) e i due FPR di test (k1 dal protocollo, k2 dalla verifica) per metodo e
  modello; se le soglie di k1 e k2 differiscono molto, va segnalato (risultato sulla dipendenza
  dalla chiave da riportare nella tesi);
- ACW: le quattro categorie anche in funzione della sovrapposizione fra i sottoinsiemi di regole
  di k1 e k2.

Prima del lancio: stima del costo per metodo e modello (SWEET compreso, quando sarà congelato).

### 3.4 Implementazione e ordine dei passi

- Attacco `T3.1` (`src/bench/attacks/t3/remark.py`, `configs/attack/t3_1_remark.yaml`): la fase
  `attack` passa all'attacco il generatore del metodo (`MethodSetup`: adapter, configurazione
  congelata, decoding del livello, cartella `_runs/attack/T3.1/...` per la ripresa). Risorse: quelle
  della fase `watermark` del metodo (ACW su CPU, gli altri su GPU).
- Fasi della seconda chiave (`src/bench/pipeline/stages/remark.py`): `detect_k2`, `calibrate_k2`,
  `freeze_k2`, `metrics_remark`; la configurazione è sempre quella congelata di k1, anche su dev.
- ACW, sovrapposizione: oltre alle regole in comune fra i due sottoinsiemi (un numero per metodo:
  il sottoinsieme dipende solo dalla chiave), per ogni campione il numero di regole **applicate da
  k2** (`applicable_rules` del worker) che stanno anche nel sottoinsieme di k1; le quattro
  categorie si riportano per 0, 1, 2 e 3+ di queste regole (definizione approvata dall'utente il
  27 ottobre 2026).
- Ordine (`scripts/submit_attacks_l1.sh`): `k2_detect_dev` → `k2_calibrate` → `k2_freeze` →
  `k2_detect_test`; poi `attack` T3.1 → `execute`, `detect` e `k2_detect` → `metrics` →
  `k2_metrics`. Report: `python scripts/check_attacks.py METODO T3.1 --report`.
- Stima dei costi prima del lancio: `scripts/estimate_t3_1_cost.py` (tempi dei lanci già fatti con
  la configurazione congelata).
