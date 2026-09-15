"""Hand-checked tests for the country counting in etl/coverage_check.py."""

from __future__ import annotations

import pandas as pd

from etl.coverage_check import aliases, count_lists, count_weighted


def test_aliases_are_casefolded_and_include_gem_spellings() -> None:
    assert aliases("Russian Federation") == {"russian federation", "russia"}
    assert aliases("Libya") == {"libya"}


def test_count_lists_splits_multi_country_values_and_applies_aliases() -> None:
    values = pd.Series(["United States, Canada", "Russia", "RUSSIAN FEDERATION", "Canada", None, ""])
    counts = count_lists(values, ["United States of America", "Russian Federation", "Canada", "Libya"])
    assert counts == {"United States of America": 1, "Russian Federation": 2, "Canada": 2, "Libya": 0}


def test_count_weighted_uses_group_by_counts() -> None:
    grouped = pd.DataFrame({"COUNTRY": ["LIBYA", "AFGHANISTAN, TURKMENISTAN", None], "n": [437, 70, 5]})
    counts = count_weighted(grouped, ["Libya", "Turkmenistan", "Afghanistan", "Egypt"])
    assert counts == {"Libya": 437, "Turkmenistan": 70, "Afghanistan": 70, "Egypt": 0}
