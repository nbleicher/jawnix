from difflib import SequenceMatcher

from jawnix import keyword_generation as generation


def test_unrelated_history_does_not_run_full_fuzzy_comparison(monkeypatch):
    calls = 0
    original = SequenceMatcher.ratio

    def counted(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(SequenceMatcher, "ratio", counted)
    selected = generation.validated_keyword_terms(
        ["Zinc kiln calibration"],
        [f"Bathroom faucet repair {index}" for index in range(200)],
        limit=25,
    )

    assert selected == ["Zinc kiln calibration"]
    assert calls == 0


def test_fast_filter_preserves_exact_token_overlap_and_fuzzy_exclusions():
    excluded = ["Plumbers", "Roof Repairs", "Fire door inspection", "Medical supplies warehouse"]
    candidates = ["plumbers", "roof repair", "fire door inspection service", "Medical supplies warehous", "Zinc kiln calibration"]
    assert generation.validated_keyword_terms(candidates, excluded, limit=25) == ["Zinc kiln calibration"]
