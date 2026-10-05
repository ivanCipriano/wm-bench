"""Shim dei metodi di watermarking (SPEC §8): girano negli ambienti dei metodi (Python >= 3.9).

Nessuna dipendenza dal framework ``bench``: solo stdlib, ``bench_contracts`` e le librerie
dell'ambiente del metodo. Si lanciano con ``<python> -m bench_shims.<metodo> --request ...``.
"""
