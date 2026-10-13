# Template del messaggio utente (`<dataset>_<linguaggio>.j2`)

Testi approvati dall'utente (ADR-006). Il system prompt è unico e bloccato (`../system.txt`, SHA-256 in
`../default.yaml`). Lo SHA-256 di ogni template entra nei manifest di `generate_baseline` e `watermark`
(`PromptBuilder.prompt_hashes`, campo `prompt_hashes`): così ogni baseline e ogni campione marcato registra
i testi esatti usati.

## Livello 1
`humanevalplus_python`, `mbppplus_python`, `humanevalpack_{java,cpp,javascript}`:
- completamento di funzione;
- il prompt del dataset va in un blocco recintato;
- `{{ entry_point }}` è il nome chiamato dai test.

## Livello 2: CodeNet (`codenet_{python,java,cpp,javascript}`)
Approvati il 13 ottobre 2026.

**Struttura**, identica nei 4 linguaggi:
1. riga 1: "Write a complete <linguaggio> program that solves the following problem.";
2. riga 2: lettura da standard input e scrittura su standard output, nel formato del problema (frase
   identica);
3. riga 3: l'unica specifica del linguaggio, che riprende i comandi dell'executor `codenet`:
   - Python: `python3 main.py`;
   - Java: `javac Main.java` e `java Main`, quindi `public class Main`;
   - C++: C++17 con `g++ -std=c++17` e `int main()`;
   - JavaScript: Node.js, `node main.js`, lettura di tutto lo standard input;
4. riga 4: un solo blocco di codice con il tag `python`, `java`, `cpp` o `javascript`, cioè i tag che la
   regola di estrazione (`LANGUAGE_TAGS`, D4) accetta; c'è un test per ciascuno;
5. una riga vuota, poi `{{ prompt }}`: il testo del problema convertito da HTML
   (`bench.data.codenet.describe`), inserito parola per parola.

**Conversione del testo** (`describe`):
- per le pagine AtCoder solo la parte inglese (`span.lang-en`);
- le formule restano in TeX tra `$...$`;
- formati ed esempi di input/output in blocchi recintati, con spazi e a capo invariati.

**Riga "Score" tolta.** La riga iniziale `Score : $N$ points` delle pagine AtCoder si toglie in modo identico
da tutti i problemi: è un metadato della gara (il punteggio del problema), non fa parte del compito
(decisione dell'utente del 13 ottobre 2026).

**Minaccia alla validità.** Su L2 l'unico caso di test usato per Pass@1 coincide con un esempio visibile nel
testo del problema, per tutti i metodi (D28).
