"""Lista di frequenza delle iniziali di PromptMark (D8) e fase ``promptmark_freq``."""

from __future__ import annotations

from bench.config.schema import ExperimentConfig
from bench.data.promptmark_freq import LetterFrequencies, identifiers, initial, letter_frequencies
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stages.promptmark_freq import promptmark_freq_ref
from tests.datafix import build_tiny_datasets, tiny_config

CODE = """
class DataProcessor:
    LIMIT = 3

    def process_data(self, input_data):
        result = []
        for item in input_data:
            result.append(item * 2)
        self.cache = result
        return math.floor(len(result))
"""


def test_identifiers_follow_the_authors_procedure() -> None:
    found = identifiers(CODE)
    # Tutte le categorie, unici; esclusi builtin (len), nomi comuni (append, self) e dunder.
    assert found == {
        "DataProcessor",
        "LIMIT",
        "process_data",
        "input_data",
        "result",
        "item",
        "cache",
        "math",
        "floor",
    }
    assert identifiers("def broken(:") is None
    assert initial("_x1") == "x" and initial("__init") == "i" and initial("_1") is None


def test_letter_frequencies_counts_unique_identifiers_per_program() -> None:
    freqs = letter_frequencies([CODE, CODE, "def f(:", "", "x = 1\n"], source="test")
    assert freqs.n_programs == 3 and freqs.n_skipped == 2
    assert freqs.total_identifiers == 2 * 9 + 1
    assert freqs.letter_freqs["d"] == 2 and freqs.letter_freqs["x"] == 1
    assert sum(freqs.letter_freqs.values()) == freqs.total_identifiers
    assert set(freqs.letter_freqs) == set("abcdefghijklmnopqrstuvwxyz")


def test_stage_writes_the_python_list(cfg: ExperimentConfig) -> None:
    build_tiny_datasets(cfg.paths.datasets)
    facade = BenchmarkFacade(tiny_config(cfg))
    report = facade.run_stage("promptmark_freq")
    assert report.ran == 1
    ref = promptmark_freq_ref()
    freqs = facade.store.read_model(ref, LetterFrequencies)
    assert freqs.source == "codesearchnet/python/train/whole_func_string"
    assert freqs.n_programs > 0 and freqs.total_identifiers == sum(freqs.letter_freqs.values())
    manifest = facade.store.read_manifest(ref)
    assert manifest is not None and manifest.extra["n_programs"] == freqs.n_programs
    assert facade.run_stage("promptmark_freq").skipped == 1
