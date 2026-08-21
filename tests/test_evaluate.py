import pytest

from semtune.config import Case
from semtune.evaluate import distance_distribution, evaluate, sweep
from semtune.search import Hit, decide

TARGETS = ["market-research", "dev-support", "general"]
CATCHALL = "general"

CASES = [
    Case(id="a", query="q1", expected="market-research", difficulty="easy"),
    Case(id="b", query="q2", expected="dev-support", difficulty="easy"),
    Case(id="c", query="q3", expected="market-research", difficulty="boundary", note="n"),
    Case(id="d", query="q4", expected="general", difficulty="easy"),
]

HITS = {
    "a": [Hit("market-research", 0.20), Hit("dev-support", 0.35)],
    "b": [Hit("market-research", 0.30), Hit("dev-support", 0.33)],
    "c": [Hit("dev-support", 0.25), Hit("market-research", 0.28)],
    "d": [Hit("market-research", 0.90), Hit("dev-support", 0.95)],
}


def _decisions(threshold: float):
    return [decide(HITS[c.id], threshold, CATCHALL) for c in CASES]


def test_accuracy_counts_correct_selections():
    result = evaluate(CASES, _decisions(0.5), TARGETS)

    # a: market-research 正解 / b: market-research 誤り / c: dev-support 誤り
    # d: すべて threshold 超過で general にフォールバック → 正解
    assert result.accuracy == pytest.approx(0.5)


def test_boundary_accuracy_only_counts_boundary_cases():
    result = evaluate(CASES, _decisions(0.5), TARGETS)

    assert result.boundary_accuracy == pytest.approx(0.0)


def test_fallback_rate_counts_catchall_fallbacks():
    result = evaluate(CASES, _decisions(0.5), TARGETS)

    assert result.fallback_rate == pytest.approx(0.25)


def test_confusion_matrix_is_square_over_all_targets():
    result = evaluate(CASES, _decisions(0.5), TARGETS)

    assert list(result.confusion.index) == TARGETS
    assert list(result.confusion.columns) == TARGETS
    assert result.confusion.loc["market-research", "market-research"] == 1


def test_mistakes_include_margin_between_top_two():
    result = evaluate(CASES, _decisions(0.5), TARGETS)

    row = result.mistakes[result.mistakes["case_id"] == "b"].iloc[0]
    assert row["actual"] == "market-research"
    assert row["expected"] == "dev-support"
    assert row["margin"] == pytest.approx(0.03)


def test_sweep_reports_one_row_per_threshold():
    df = sweep(CASES, HITS, [0.1, 0.3, 0.5], CATCHALL, TARGETS)

    assert list(df["threshold"]) == [0.1, 0.3, 0.5]
    assert set(df.columns) == {
        "threshold",
        "accuracy",
        "boundary_accuracy",
        "fallback_rate",
    }


def test_sweep_fallback_rate_is_one_when_threshold_is_zero():
    df = sweep(CASES, HITS, [0.0], CATCHALL, TARGETS)

    assert df.iloc[0]["fallback_rate"] == pytest.approx(1.0)


def test_sweep_accepts_target_names_absent_from_golden_dataset():
    # "unseen-target" never appears as an `expected` in CASES, but decide()
    # could plausibly route to it if a new specialist target is added without
    # a golden case yet. sweep() must not derive target_names from the
    # dataset alone, or this raises a KeyError deep inside confusion.loc[...].
    extra_targets = TARGETS + ["unseen-target"]

    df = sweep(CASES, HITS, [0.5], CATCHALL, extra_targets)

    assert list(df["threshold"]) == [0.5]
    assert len(df) == 1


def test_evaluate_rejects_unknown_expected_label():
    bad_case = Case(id="z", query="qz", expected="not-a-target", difficulty="easy")
    decisions = [decide(HITS["a"], 0.5, CATCHALL)]

    with pytest.raises(ValueError, match="not-a-target"):
        evaluate([bad_case], decisions, TARGETS)


def test_evaluate_rejects_unknown_selected_label():
    from semtune.search import RoutingDecision

    decisions = [RoutingDecision(selected="not-a-target", hits=[], fell_back=False)]

    with pytest.raises(ValueError, match="not-a-target"):
        evaluate([CASES[0]], decisions, TARGETS)


def test_distance_distribution_flags_correct_target_rows():
    df = distance_distribution(CASES, HITS)

    assert len(df) == 8
    correct = df[df["is_correct_target"]]
    assert set(correct["case_id"]) == {"a", "b", "c"}
