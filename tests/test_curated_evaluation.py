"""The curated routing and failure corpus is a checked CI gate."""

import pytest

from evals.run import evaluate_case, load_cases

CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_curated_case(case):
    passed, errors, _elapsed = evaluate_case(case)
    assert passed, errors


def test_corpus_has_required_scenarios():
    groups = {
        group: sum(case["group"] == group for case in CASES)
        for group in ("technical", "fulfillment", "ambiguous", "adversarial", "failure")
    }
    assert groups == {group: 20 for group in groups}
