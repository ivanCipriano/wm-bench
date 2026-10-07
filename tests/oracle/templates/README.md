# Template provvisori dell'oracle di MCGMark su codice lungo

Usati **solo** da `tests/oracle/make_mcgmark_long_inputs.py` per verificare l'integrazione di MCGMark su
codice lungo (decisione dell'utente del 7 ottobre 2026). Non sono i template di L2 e L3: quelli si
definiscono e si approvano nelle Milestone 9 (CodeNet) e 13 (ClassEval) in `configs/prompt/user/`.

- `classeval_python.j2`: stesso testo di `configs/prompt/user/humanevalplus_python.j2`, con lo
  scheletro della classe come codice da completare e il nome della classe come `entry_point`.
- `codenet_python.j2`: programma completo su stdin/stdout, con il testo della descrizione HTML del
  problema (tag rimossi).
