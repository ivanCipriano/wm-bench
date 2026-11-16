# Esempi di inserimento di `mcss` e `multimark` nel benchmark (P2)

Generato da `scripts/posthoc_examples.py`: libreria `multimark` al commit `488fb5a769241e7887be2ad7f6596e21495ece82`, `global_seed` 20261001, profili degli adapter del benchmark (`mcss`, `multimark` con proponente statico). Messaggio e chiave per esempio da `derive_seed(global_seed, 'posthoc-examples-msg' | 'posthoc-examples-key', sample_id)`.

Colonne della tabella dei voti: *letto* è il valore del sito sul codice (forma del canale; per i nomi il bit della chiave con V2, il bit dello stile con V1), *voto* è il valore dopo la maschera (con `mcss` la maschera è spenta), *cw[pos]* è il bit della parola di codice del messaggio in quella posizione. Con V1 (`mcss`) una variabile porta due voti.

## Esempio 1: `HumanEval/1`

Origine: `tests/fixtures/tiny/humanevalplus.jsonl` (prompt + canonical_solution). Messaggio `101010011100110000110110`.

```python
from typing import List


def separate_paren_groups(paren_string: str) -> List[str]:
    """ Input to this function is a string containing multiple groups of nested parentheses. Your goal is to
    separate those group into separate strings and return the list of those.
    Separate groups are balanced (each open brace is properly closed) and not nested within each other
    Ignore any spaces in the input string.
    >>> separate_paren_groups('( ) (( )) (( )( ))')
    ['()', '(())', '(()())']
    """


    cnt, group, results = 0, "", []
    for ch in paren_string:
        if ch == "(": cnt += 1
        if ch == ")": cnt -= 1
        if ch != " ": group += ch
        if cnt == 0:
            if group != "": results.append(group)
            group = ""
    return results
```

#### `mcss`

- **Inserimento:** `PARTIAL`; N = 18 (B = 32); siti per canale: aug_assign 2, equality_dir 5, empty_coll 1, names 5; variabili rinominate 5.

Differenza:

```diff
--- prima
+++ dopo
@@ -3,3 +3,3 @@
 
-def separate_paren_groups(paren_string: str) -> List[str]:
+def separate_paren_groups(parenString: str) -> List[str]:
     """ Input to this function is a string containing multiple groups of nested parentheses. Your goal is to
@@ -13,11 +13,11 @@
 
-    cnt, group, results = 0, "", []
-    for ch in paren_string:
-        if ch == "(": cnt += 1
-        if ch == ")": cnt -= 1
-        if ch != " ": group += ch
-        if cnt == 0:
-            if group != "": results.append(group)
-            group = ""
-    return results
+    CNT_VAL, GROUP_VAL, resultsVal = 0, "", list()
+    for chVal in parenString:
+        if chVal == "(": CNT_VAL = CNT_VAL + 1
+        if ")" == chVal: CNT_VAL = CNT_VAL - 1
+        if chVal != " ": GROUP_VAL += chVal
+        if 0 == CNT_VAL:
+            if "" != GROUP_VAL: resultsVal.append(GROUP_VAL)
+            GROUP_VAL = ""
+    return resultsVal
 
```

Voti letti sul codice marcato:

| riga | funzione | canale | sito | posizione | letto | voto | cw[pos] | concorda |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 4 | `separate_paren_groups` | names | `parenString` | 23 | 1 | 0 | 0 | sì |
| 4 | `separate_paren_groups` | names | `parenString` | 30 | 1 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | names | `CNT_VAL` | 28 | 3 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | names | `CNT_VAL` | 12 | 3 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | names | `GROUP_VAL` | 29 | 3 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | names | `GROUP_VAL` | 26 | 3 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | names | `resultsVal` | 1 | 1 | 0 | 0 | sì |
| 14 | `separate_paren_groups` | names | `resultsVal` | 21 | 1 | 1 | 1 | sì |
| 15 | `separate_paren_groups` | names | `chVal` | 17 | 1 | 0 | 0 | sì |
| 15 | `separate_paren_groups` | names | `chVal` | 18 | 1 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | empty_coll | rango 0 | 24 | 1 | 1 | 1 | sì |
| 16 | `separate_paren_groups` | equality_dir | rango 0 | 4 | 1 | 1 | 1 | sì |
| 16 | `separate_paren_groups` | aug_assign | rango 0 | 10 | 0 | 0 | 0 | sì |
| 17 | `separate_paren_groups` | equality_dir | rango 1 | 6 | 0 | 0 | 0 | sì |
| 17 | `separate_paren_groups` | aug_assign | rango 1 | 15 | 0 | 0 | 0 | sì |
| 18 | `separate_paren_groups` | equality_dir | rango 2 | 0 | 1 | 1 | 1 | sì |
| 19 | `separate_paren_groups` | equality_dir | rango 3 | 11 | 0 | 0 | 0 | sì |
| 20 | `separate_paren_groups` | equality_dir | rango 4 | 31 | 0 | 0 | 0 | sì |

- **Rilevazione:** 18 voti, concordanza A = 18; punteggio principale (N_reg = 1) 5.419; con il registro: N_reg = 16 5.419, N_reg = 1024 5.419 (messaggio scelto giusto).
- **Decodifica aperta:** `ambiguous`, 14 posizioni cancellate, nessun messaggio; bit giusti 12/24; `native_decision` = False.

#### `multimark`

- **Inserimento:** `PARTIAL`; N = 12 (B = 32); siti per canale: aug_assign 2, equality_dir 5, empty_coll 1, names 4; variabili rinominate 3.

Differenza:

```diff
--- prima
+++ dopo
@@ -13,10 +13,10 @@
 
-    cnt, group, results = 0, "", []
-    for ch in paren_string:
-        if ch == "(": cnt += 1
-        if ch == ")": cnt -= 1
-        if ch != " ": group += ch
-        if cnt == 0:
-            if group != "": results.append(group)
-            group = ""
+    cnt_value, cluster, results = 0, "", list()
+    for char in paren_string:
+        if char == "(": cnt_value = cnt_value + 1
+        if ")" == char: cnt_value = cnt_value - 1
+        if " " != char: cluster += char
+        if 0 == cnt_value:
+            if "" != cluster: results.append(cluster)
+            cluster = ""
     return results
```

Voti letti sul codice marcato:

| riga | funzione | canale | sito | posizione | letto | voto | cw[pos] | concorda |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 16 | `separate_paren_groups` | aug_assign | rango 0 | 19 | 0 | 1 | 1 | sì |
| 17 | `separate_paren_groups` | aug_assign | rango 1 | 20 | 0 | 0 | 0 | sì |
| 16 | `separate_paren_groups` | equality_dir | rango 0 | 8 | 1 | 1 | 1 | sì |
| 17 | `separate_paren_groups` | equality_dir | rango 1 | 9 | 0 | 1 | 1 | sì |
| 18 | `separate_paren_groups` | equality_dir | rango 2 | 10 | 0 | 0 | 0 | sì |
| 19 | `separate_paren_groups` | equality_dir | rango 3 | 11 | 0 | 0 | 0 | sì |
| 20 | `separate_paren_groups` | equality_dir | rango 4 | 12 | 0 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | empty_coll | rango 0 | 3 | 1 | 0 | 0 | sì |
| 14 | `separate_paren_groups` | names | `cnt_value` | 22 | 1 | 1 | 1 | sì |
| 14 | `separate_paren_groups` | names | `cluster` | 23 | 0 | 0 | 0 | sì |
| 14 | `separate_paren_groups` | names | `results` | 24 | 0 | 0 | 0 | sì |
| 15 | `separate_paren_groups` | names | `char` | 25 | 1 | 1 | 1 | sì |

- **Rilevazione:** 12 voti, concordanza A = 12; punteggio principale (N_reg = 1) 3.612; con il registro: N_reg = 16 3.612, N_reg = 1024 3.612 (messaggio scelto giusto).
- **Decodifica aperta:** `ambiguous`, 20 posizioni cancellate, nessun messaggio; bit giusti 10/24; `native_decision` = True.

## Esempio 2: `HumanEval/2`

Origine: `tests/fixtures/tiny/humanevalplus.jsonl` (prompt + canonical_solution). Messaggio `111010001101111101010000`.

```python


def truncate_number(number: float) -> float:
    """ Given a positive floating point number, it can be decomposed into
    and integer part (largest integer smaller than given number) and decimals
    (leftover part always smaller than 1).

    Return the decimal part of the number.
    >>> truncate_number(3.5)
    0.5
    """


    return number - int(number)
```

#### `mcss`

- **Inserimento:** `PARTIAL`; N = 2 (B = 32); siti per canale: names 1; variabili rinominate 1.

Differenza:

```diff
--- prima
+++ dopo
@@ -2,3 +2,3 @@
 
-def truncate_number(number: float) -> float:
+def truncate_number(NUMBER_VAL: float) -> float:
     """ Given a positive floating point number, it can be decomposed into
@@ -13,3 +13,3 @@
 
-    return number - int(number)
+    return NUMBER_VAL - int(NUMBER_VAL)
 
```

Voti letti sul codice marcato:

| riga | funzione | canale | sito | posizione | letto | voto | cw[pos] | concorda |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 3 | `truncate_number` | names | `NUMBER_VAL` | 0 | 3 | 1 | 1 | sì |
| 3 | `truncate_number` | names | `NUMBER_VAL` | 9 | 3 | 1 | 1 | sì |

- **Rilevazione:** 2 voti, concordanza A = 2; punteggio principale (N_reg = 1) 0.602; con il registro: N_reg = 16 0.602, N_reg = 1024 0.602 (messaggio scelto giusto).
- **Decodifica aperta:** `ambiguous`, 30 posizioni cancellate, nessun messaggio; bit giusti 2/24; `native_decision` = False.

#### `multimark`

- **Inserimento:** `FAILED` (`no_sites`); N = 0 (B = 32); siti per canale: nessuno; variabili rinominate 0.
- Il codice marcato resta quello d'ingresso (negativo per costruzione).

## Esempio 3: `synthetic-3`

Origine: `multimark.synthetic.generate(3, 8, 2)`. Messaggio `110111010110100000110100`.

```python
def func_0(n, data, flag):
    if flag is None:
        return 0
    indexPrice = n is None
    totalItem = range(0, n)
    nameScore = n is None
    keyWeight = n is None
    index_page = data[0:n]
    totalResult = n ** 2
    item_size = [weight_row * 2 for weight_row in data]
    wordCol = n * n
    return None


def func_1(n, data, flag):
    if flag is None:
        return 0
    scoreSize = list()
    page_weight = 2 * n
    wordName = 2 * n
    sizeResult = n
    word_point = n != None
    word_size = data[0:n]
    key_total = data[0:n]
    item_level = n < 3
    return None
```

#### `mcss`

- **Inserimento:** `OK`; N = 56 (B = 32); siti per canale: compare_dir 1, list_comp 1, operand_order 3, empty_coll 1, none_check 6, names 22; variabili rinominate 21.

Differenza:

```diff
--- prima
+++ dopo
@@ -1,12 +1,14 @@
-def func_0(n, data, flag):
-    if flag is None:
+def func_0(N_VAL, DATA_VAL, flagVal):
+    if flagVal == None:
         return 0
-    indexPrice = n is None
-    totalItem = range(0, n)
-    nameScore = n is None
-    keyWeight = n is None
-    index_page = data[0:n]
-    totalResult = n ** 2
-    item_size = [weight_row * 2 for weight_row in data]
-    wordCol = n * n
+    index_price = N_VAL is None
+    TotalItem = range(0, N_VAL)
+    NameScore = N_VAL is None
+    KEY_WEIGHT = N_VAL is None
+    IndexPage = DATA_VAL[0:N_VAL]
+    total_result = N_VAL ** 2
+    ItemSize = []
+    for weight_row in DATA_VAL:
+        ItemSize.append(2 * weight_row)
+    word_col = N_VAL * N_VAL
     return None
@@ -14,13 +16,13 @@
 
-def func_1(n, data, flag):
-    if flag is None:
+def func_1(nVal, DataVal, flagVal):
+    if flagVal is None:
         return 0
     scoreSize = list()
-    page_weight = 2 * n
-    wordName = 2 * n
-    sizeResult = n
-    word_point = n != None
-    word_size = data[0:n]
-    key_total = data[0:n]
-    item_level = n < 3
+    pageWeight = nVal * 2
+    WordName = 2 * nVal
+    SIZE_RESULT = nVal
+    WordPoint = nVal is not None
+    WORD_SIZE = DataVal[0:nVal]
+    keyTotal = DataVal[0:nVal]
+    ItemLevel = nVal < 3
     return None
```

Voti letti sul codice marcato:

| riga | funzione | canale | sito | posizione | letto | voto | cw[pos] | concorda |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | `func_0` | names | `N_VAL` | 3 | 3 | 1 | 1 | sì |
| 1 | `func_0` | names | `N_VAL` | 10 | 3 | 1 | 1 | sì |
| 1 | `func_0` | names | `DATA_VAL` | 1 | 3 | 1 | 1 | sì |
| 1 | `func_0` | names | `DATA_VAL` | 9 | 3 | 1 | 1 | sì |
| 1 | `func_0` | names | `flagVal` | 22 | 1 | 0 | 0 | sì |
| 1 | `func_0` | names | `flagVal` | 26 | 1 | 1 | 1 | sì |
| 4 | `func_0` | names | `index_price` | 25 | 0 | 0 | 0 | sì |
| 4 | `func_0` | names | `index_price` | 13 | 0 | 0 | 0 | sì |
| 5 | `func_0` | names | `TotalItem` | 7 | 2 | 1 | 1 | sì |
| 5 | `func_0` | names | `TotalItem` | 15 | 2 | 0 | 0 | sì |
| 6 | `func_0` | names | `NameScore` | 21 | 2 | 1 | 1 | sì |
| 6 | `func_0` | names | `NameScore` | 23 | 2 | 0 | 0 | sì |
| 7 | `func_0` | names | `KEY_WEIGHT` | 5 | 3 | 1 | 1 | sì |
| 7 | `func_0` | names | `KEY_WEIGHT` | 12 | 3 | 1 | 1 | sì |
| 8 | `func_0` | names | `IndexPage` | 24 | 2 | 1 | 1 | sì |
| 8 | `func_0` | names | `IndexPage` | 29 | 2 | 0 | 0 | sì |
| 9 | `func_0` | names | `total_result` | 20 | 0 | 0 | 0 | sì |
| 9 | `func_0` | names | `total_result` | 2 | 0 | 0 | 0 | sì |
| 10 | `func_0` | names | `ItemSize` | 0 | 2 | 1 | 1 | sì |
| 10 | `func_0` | names | `ItemSize` | 11 | 2 | 0 | 0 | sì |
| 13 | `func_0` | names | `word_col` | 17 | 0 | 0 | 0 | sì |
| 13 | `func_0` | names | `word_col` | 6 | 0 | 0 | 0 | sì |
| 17 | `func_1` | names | `nVal` | 27 | 1 | 0 | 0 | sì |
| 17 | `func_1` | names | `nVal` | 30 | 1 | 1 | 1 | sì |
| 17 | `func_1` | names | `DataVal` | 28 | 2 | 1 | 1 | sì |
| 17 | `func_1` | names | `DataVal` | 8 | 2 | 0 | 0 | sì |
| 17 | `func_1` | names | `flagVal` | 31 | 1 | 0 | 0 | sì |
| 17 | `func_1` | names | `flagVal` | 19 | 1 | 1 | 1 | sì |
| 20 | `func_1` | names | `scoreSize` | 14 | 1 | 0 | 0 | sì |
| 20 | `func_1` | names | `scoreSize` | 4 | 1 | 1 | 1 | sì |
| 21 | `func_1` | names | `pageWeight` | 16 | 1 | 0 | 0 | sì |
| 21 | `func_1` | names | `pageWeight` | 18 | 1 | 1 | 1 | sì |
| 22 | `func_1` | names | `WordName` | 1 | 2 | 1 | 1 | sì |
| 22 | `func_1` | names | `WordName` | 13 | 2 | 0 | 0 | sì |
| 23 | `func_1` | names | `SIZE_RESULT` | 0 | 3 | 1 | 1 | sì |
| 23 | `func_1` | names | `SIZE_RESULT` | 10 | 3 | 1 | 1 | sì |
| 24 | `func_1` | names | `WordPoint` | 28 | 2 | 1 | 1 | sì |
| 24 | `func_1` | names | `WordPoint` | 16 | 2 | 0 | 0 | sì |
| 25 | `func_1` | names | `WORD_SIZE` | 19 | 3 | 1 | 1 | sì |
| 25 | `func_1` | names | `WORD_SIZE` | 18 | 3 | 1 | 1 | sì |
| 26 | `func_1` | names | `keyTotal` | 2 | 1 | 0 | 0 | sì |
| 26 | `func_1` | names | `keyTotal` | 12 | 1 | 1 | 1 | sì |
| 27 | `func_1` | names | `ItemLevel` | 5 | 2 | 1 | 1 | sì |
| 27 | `func_1` | names | `ItemLevel` | 31 | 2 | 0 | 0 | sì |
| 2 | `func_0` | none_check | rango 0 | 24 | 1 | 1 | 1 | sì |
| 4 | `func_0` | none_check | rango 1 | 27 | 0 | 0 | 0 | sì |
| 6 | `func_0` | none_check | rango 2 | 29 | 0 | 0 | 0 | sì |
| 7 | `func_0` | none_check | rango 3 | 20 | 0 | 0 | 0 | sì |
| 10 | `func_0` | list_comp | rango 0 | 14 | 0 | 0 | 0 | sì |
| 12 | `func_0` | operand_order | rango 0 | 21 | 1 | 1 | 1 | sì |
| 18 | `func_1` | none_check | rango 0 | 23 | 0 | 0 | 0 | sì |
| 20 | `func_1` | empty_coll | rango 0 | 9 | 1 | 1 | 1 | sì |
| 21 | `func_1` | operand_order | rango 0 | 8 | 0 | 0 | 0 | sì |
| 22 | `func_1` | operand_order | rango 1 | 3 | 1 | 1 | 1 | sì |
| 24 | `func_1` | none_check | rango 1 | 22 | 0 | 0 | 0 | sì |
| 27 | `func_1` | compare_dir | rango 0 | 30 | 1 | 1 | 1 | sì |

- **Rilevazione:** 56 voti, concordanza A = 56; punteggio principale (N_reg = 1) 16.858; con il registro: N_reg = 16 16.858, N_reg = 1024 16.858 (messaggio scelto giusto).
- **Decodifica aperta:** `ok`, 0 posizioni cancellate, uguale al messaggio; bit giusti 24/24; `native_decision` = True.

#### `multimark`

- **Inserimento:** `OK`; N = 38 (B = 32); siti per canale: compare_dir 1, list_comp 1, operand_order 3, empty_coll 1, none_check 6, range_zero 1, slice_lower 3, else_return 2, square 2, return_none 2, names 16; variabili rinominate 5.

Differenza:

```diff
--- prima
+++ dopo
@@ -3,10 +3,10 @@
         return 0
-    indexPrice = n is None
-    totalItem = range(0, n)
+    indexPrice = n == None
+    totalItem = range(n)
     nameScore = n is None
-    keyWeight = n is None
+    key_weight_value = n is None
     index_page = data[0:n]
-    totalResult = n ** 2
-    item_size = [weight_row * 2 for weight_row in data]
-    wordCol = n * n
+    total_result_value = n ** 2
+    item_size = [2 * weight_row for weight_row in data]
+    wordCol = n ** 2
     return None
@@ -17,4 +17,4 @@
         return 0
-    scoreSize = list()
-    page_weight = 2 * n
+    scoreSize = []
+    page_weight_value = n * 2
     wordName = 2 * n
@@ -22,4 +22,4 @@
     word_point = n != None
-    word_size = data[0:n]
-    key_total = data[0:n]
+    word_size_item = data[0:n]
+    key_total_value = data[0:n]
     item_level = n < 3
```

Voti letti sul codice marcato:

| riga | funzione | canale | sito | posizione | letto | voto | cw[pos] | concorda |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 10 | `func_0` | list_comp | rango 0 | 29 | 1 | 0 | 0 | sì |
| 10 | `func_0` | operand_order | rango 0 | 16 | 1 | 0 | 0 | sì |
| 2 | `func_0` | none_check | rango 0 | 15 | 0 | 0 | 0 | sì |
| 4 | `func_0` | none_check | rango 1 | 16 | 1 | 0 | 0 | sì |
| 6 | `func_0` | none_check | rango 2 | 17 | 0 | 0 | 0 | sì |
| 7 | `func_0` | none_check | rango 3 | 18 | 0 | 1 | 1 | sì |
| 5 | `func_0` | range_zero | rango 0 | 28 | 1 | 0 | 0 | sì |
| 8 | `func_0` | slice_lower | rango 0 | 14 | 0 | 0 | 0 | sì |
| 2 | `func_0` | else_return | rango 0 | 3 | 1 | 1 | 1 | sì |
| 9 | `func_0` | square | rango 0 | 30 | 1 | 0 | 0 | sì |
| 11 | `func_0` | square | rango 1 | 31 | 1 | 0 | 0 | sì |
| 12 | `func_0` | return_none | rango 0 | 11 | 0 | 0 | 0 | sì |
| 25 | `func_1` | compare_dir | rango 0 | 9 | 1 | 1 | 1 | sì |
| 19 | `func_1` | operand_order | rango 0 | 15 | 0 | 0 | 0 | sì |
| 20 | `func_1` | operand_order | rango 1 | 16 | 1 | 0 | 0 | sì |
| 18 | `func_1` | empty_coll | rango 0 | 14 | 0 | 0 | 0 | sì |
| 16 | `func_1` | none_check | rango 0 | 16 | 0 | 0 | 0 | sì |
| 22 | `func_1` | none_check | rango 1 | 17 | 1 | 0 | 0 | sì |
| 23 | `func_1` | slice_lower | rango 0 | 26 | 0 | 1 | 1 | sì |
| 24 | `func_1` | slice_lower | rango 1 | 27 | 0 | 1 | 1 | sì |
| 16 | `func_1` | else_return | rango 0 | 26 | 1 | 1 | 1 | sì |
| 26 | `func_1` | return_none | rango 0 | 21 | 0 | 1 | 1 | sì |
| 4 | `func_0` | names | `indexPrice` | 10 | 1 | 1 | 1 | sì |
| 5 | `func_0` | names | `totalItem` | 11 | 0 | 0 | 0 | sì |
| 6 | `func_0` | names | `nameScore` | 12 | 1 | 1 | 1 | sì |
| 7 | `func_0` | names | `key_weight_value` | 13 | 0 | 0 | 0 | sì |
| 8 | `func_0` | names | `index_page` | 14 | 0 | 0 | 0 | sì |
| 9 | `func_0` | names | `total_result_value` | 15 | 0 | 0 | 0 | sì |
| 10 | `func_0` | names | `item_size` | 16 | 0 | 0 | 0 | sì |
| 11 | `func_0` | names | `wordCol` | 17 | 0 | 0 | 0 | sì |
| 18 | `func_1` | names | `scoreSize` | 0 | 1 | 1 | 1 | sì |
| 19 | `func_1` | names | `page_weight_value` | 1 | 1 | 1 | 1 | sì |
| 20 | `func_1` | names | `wordName` | 2 | 0 | 0 | 0 | sì |
| 21 | `func_1` | names | `sizeResult` | 3 | 1 | 1 | 1 | sì |
| 22 | `func_1` | names | `word_point` | 4 | 1 | 1 | 1 | sì |
| 23 | `func_1` | names | `word_size_item` | 5 | 1 | 1 | 1 | sì |
| 24 | `func_1` | names | `key_total_value` | 6 | 0 | 0 | 0 | sì |
| 25 | `func_1` | names | `item_level` | 7 | 1 | 1 | 1 | sì |

- **Rilevazione:** 38 voti, concordanza A = 38; punteggio principale (N_reg = 1) 11.439; con il registro: N_reg = 16 11.439, N_reg = 1024 11.439 (messaggio scelto giusto).
- **Decodifica aperta:** `ambiguous`, 7 posizioni cancellate, nessun messaggio; bit giusti 19/24; `native_decision` = True.

## Cosa mostrano gli esempi

*Sezione scritta a mano dopo la generazione; il resto del file è l'uscita dello script.*

- **Codice corto, il caso di L1 (esempio 1).**
  - **`mcss`:** 18 bit potenziali. Rinomina tutte e cinque le variabili, compreso il parametro
    (`paren_string` → `parenString`, `names.include_params = true`), con gli stili V1: due voti
    per variabile. Restano 14 posizioni senza voto: la decodifica è `ambiguous` e non dà il
    messaggio, quindi `native_decision` = False. Il punteggio di confronto vale comunque 5,42 (P ≈
    4·10⁻⁶).
  - **`multimark`:** 12 siti. Non tocca il parametro e rinomina 3 variabili su 4 con sinonimi del
    proponente statico (`group` → `cluster`, `ch` → `char`, `cnt` → `cnt_value`). La decodifica è
    ambigua anche qui (20 cancellazioni). Il punteggio con il registro, 3,61 con P ≤ 0,01, rileva
    il watermark senza decodificare (`native_decision` = True). È la scelta 1A: con N < B conta il
    punteggio, non la decodifica (criterio (b) di P2 per fasce di siti).
- **Legame per funzione e canale (2A).** Nella tabella di `multimark` i cinque `equality_dir`
  della stessa funzione cadono su posizioni consecutive (8–12): `off(fid, canale) + rango`. Con
  `mcss` le posizioni vengono dalla permutazione globale (legame posizionale).
- **Nessun sito (esempio 2).**
  - **`mcss`:** porta 2 bit solo perché rinomina il parametro (`number` → `NUMBER_VAL`), cosa che
    cambia la firma della funzione: una chiamata per parola chiave (`truncate_number(number=…)`)
    non funzionerebbe più. I test di HumanEval chiamano per posizione.
  - **`multimark`:** esclude i parametri e in `number - int(number)` non c'è nessun sito
    strutturale: inserimento `FAILED` con `no_sites`, codice invariato. È il caso del criterio (c)
    di P2: su L1 dev è il 22–45% dei campioni, e `embedding_success_rate` si misura sui campioni
    con almeno un sito.
- **Codice lungo (esempio 3, sintetico: non c'è codice lungo del benchmark nei dati locali).**
  - **`mcss`:** 56 voti che coprono tutte le 32 posizioni (cicli completi della permutazione): la
    decodifica dà il messaggio.
  - **`multimark`:** 38 voti, ma 7 posizioni restano senza voto, e la decodifica aperta è
    `ambiguous`. È il costo dichiarato del legame per funzione (P1: 8,9% di casi ambigui con N ≥
    B). Il punteggio principale (11,44) e il registro (messaggio giusto fra 1024) non ne
    risentono. Su L2 dev questi numeri li misurano `report_capacity.py` e
    `mcss_paper_attacks.py`.
- **Impercettibilità.**
  - **Canali strutturali, comuni ai due metodi:** `x += 1` diventa `x = x + 1`, `[]` diventa
    `list()` (e viceversa), `a == "("` diventa `"(" == a`, `a is None` diventa `a == None`
    (esempio 3), `range(0, n)` diventa `range(n)` (solo `multimark`, canale `range_zero`).
  - **Nomi, `mcss`:** gli stili V1 danno nomi come `CNT_VAL` o `GROUP_VAL` per variabili locali.
  - **Nomi, `multimark`:** con V2 i nomi restano in `snake_case` e cambiano meno variabili, perché
    un nome porta il bit giusto con probabilità 1/2 senza essere toccato (esempio 3: 16 variabili
    lette, 5 rinominate, come `keyWeight` → `key_weight_value`).
- **Senza attacchi tutti i voti concordano** (colonna *concorda*), e il registro sceglie sempre il
  messaggio atteso: i punteggi con N_reg = 16 e 1024 coincidono con quello principale.
- **Rigenerare gli esempi con codice reale di L2 dev**, sul cluster, nell'ambiente posthoc:
  `scripts/posthoc_examples.py --input <JSONL con sample_id e code> --ids <sample_id> ... --out
  <nuovo file>`.
