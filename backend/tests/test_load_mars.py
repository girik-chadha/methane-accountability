"""Tests for the MARS SQLite load.

Every asserted number is hand-checked against ONE pinned snapshot,
data/raw/unep_methanedata_detected_plumes_csv_2026-09-15.zip. MARS is
republished monthly, so these counts are properties of that file, not of the
dataset in general. When a newer snapshot is adopted, bump SNAPSHOT_DATE and
re-derive every expected value below from the new file by hand. Do not relax an
assertion to make it pass: a changed count means the data changed, which is
exactly what these tests exist to surface.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backend.app.config import (
    CH4_FLUXRATE_STD_UNIT,
    CH4_FLUXRATE_UNIT,
    FEEDBACK_NO,
    FEEDBACK_NOT_APPLICABLE,
    FEEDBACK_YES,
    KG_PER_TONNE,
    MARS_JOIN_KEY,
    MARS_PLUMES_MEMBER,
    MARS_SOURCES_MEMBER,
    PLUMES_TABLE,
    SOURCES_TABLE,
    TOTAL_EMISSION_UNIT,
)
from etl.load_mars import (
    check_join_integrity,
    find_snapshot,
    read_member,
    write_sqlite,
)

# The pinned snapshot these expectations were hand-checked against.
SNAPSHOT_DATE = date(2026, 9, 15)

# Hand-checked row counts.
EXPECTED_SOURCE_ROWS = 4_072
EXPECTED_PLUME_ROWS = 29_265

# Hand-checked feedback breakdown of the sources table.
EXPECTED_NOT_APPLICABLE = 2_369
EXPECTED_TRACKED = 1_703  # 4,072 - 2,369
EXPECTED_GOVERNMENT_NO = 1_430
EXPECTED_OPERATOR_NO = 1_426
EXPECTED_BOTH_NO = 1_394

# Hand-checked: plumes UNEP publishes without attributing them to a source.
EXPECTED_UNATTRIBUTED_PLUMES = 6_323

# Published column counts, to catch a silent schema change upstream.
EXPECTED_SOURCE_COLUMNS = 16
EXPECTED_PLUME_COLUMNS = 23


@pytest.fixture(scope="module")
def snapshot() -> Path:
    """The pinned raw snapshot. Fails loudly if absent; never fabricates data."""
    try:
        return find_snapshot(SNAPSHOT_DATE)
    except FileNotFoundError as error:
        pytest.fail(
            f"{error}\nThese tests assert hand-checked counts against the "
            f"{SNAPSHOT_DATE.isoformat()} snapshot and cannot run without it."
        )


@pytest.fixture(scope="module")
def sources(snapshot: Path) -> pd.DataFrame:
    return read_member(snapshot, MARS_SOURCES_MEMBER)


@pytest.fixture(scope="module")
def plumes(snapshot: Path) -> pd.DataFrame:
    return read_member(snapshot, MARS_PLUMES_MEMBER)


@pytest.fixture(scope="module")
def loaded_db(
    tmp_path_factory: pytest.TempPathFactory,
    sources: pd.DataFrame,
    plumes: pd.DataFrame,
) -> Path:
    """Run the real load into a throwaway database."""
    db_path = tmp_path_factory.mktemp("mars") / "mars.db"
    write_sqlite(sources, plumes, db_path)
    return db_path


# --- Row counts -------------------------------------------------------------


def test_source_row_count(sources: pd.DataFrame) -> None:
    assert len(sources) == EXPECTED_SOURCE_ROWS


def test_plume_row_count(plumes: pd.DataFrame) -> None:
    assert len(plumes) == EXPECTED_PLUME_ROWS


def test_column_counts_unchanged(
    sources: pd.DataFrame, plumes: pd.DataFrame
) -> None:
    """A changed column count means the publisher altered the schema."""
    assert len(sources.columns) == EXPECTED_SOURCE_COLUMNS
    assert len(plumes.columns) == EXPECTED_PLUME_COLUMNS


def test_published_column_names_are_preserved(loaded_db: Path) -> None:
    """The load must not rename anything; our tables mirror UNEP's CSVs."""
    with sqlite3.connect(loaded_db) as connection:
        loaded = pd.read_sql(f"SELECT * FROM {SOURCES_TABLE} LIMIT 1", connection)
    assert list(loaded.columns) == [
        "source_name", "lon", "lat", "country", "sector", "source_type",
        "persistency", "persistency_std", "persistency_category",
        "n_plumes_detected", "id_last_plume", "last_plume_date", "notified",
        "feedback", "feedback_operator", "feedback_government",
    ]


def test_sqlite_row_counts_match_the_csvs(loaded_db: Path) -> None:
    """The SQLite tables must not drop or duplicate rows."""
    with sqlite3.connect(loaded_db) as connection:
        source_rows = connection.execute(
            f"SELECT COUNT(*) FROM {SOURCES_TABLE}"
        ).fetchone()[0]
        plume_rows = connection.execute(
            f"SELECT COUNT(*) FROM {PLUMES_TABLE}"
        ).fetchone()[0]
    assert source_rows == EXPECTED_SOURCE_ROWS
    assert plume_rows == EXPECTED_PLUME_ROWS


# --- Feedback accounting ----------------------------------------------------


def test_both_no_count(sources: pd.DataFrame) -> None:
    """Sources where neither the government nor the operator replied."""
    both_no = (
        (sources["feedback_government"] == FEEDBACK_NO)
        & (sources["feedback_operator"] == FEEDBACK_NO)
    ).sum()
    assert both_no == EXPECTED_BOTH_NO


def test_individual_no_counts(sources: pd.DataFrame) -> None:
    government_no = (sources["feedback_government"] == FEEDBACK_NO).sum()
    operator_no = (sources["feedback_operator"] == FEEDBACK_NO).sum()
    assert government_no == EXPECTED_GOVERNMENT_NO
    assert operator_no == EXPECTED_OPERATOR_NO


def test_not_applicable_plus_tracked_equals_total(sources: pd.DataFrame) -> None:
    """The partition must be exhaustive: nothing falls between the buckets.

    This is the guard against quoting a non-response count without its
    denominator. If these do not sum to the total, some third state exists that
    we have not accounted for.
    """
    government = sources["feedback_government"]
    operator = sources["feedback_operator"]

    not_applicable = (
        (government == FEEDBACK_NOT_APPLICABLE)
        & (operator == FEEDBACK_NOT_APPLICABLE)
    ).sum()
    tracked = (
        government.isin([FEEDBACK_YES, FEEDBACK_NO])
        | operator.isin([FEEDBACK_YES, FEEDBACK_NO])
    ).sum()

    assert not_applicable == EXPECTED_NOT_APPLICABLE
    assert tracked == EXPECTED_TRACKED
    assert not_applicable + tracked == EXPECTED_SOURCE_ROWS == len(sources)


def test_feedback_columns_have_no_other_values(sources: pd.DataFrame) -> None:
    """Only the three known sentinels appear, and there are no nulls."""
    permitted = {FEEDBACK_YES, FEEDBACK_NO, FEEDBACK_NOT_APPLICABLE}
    for column in ("feedback", "feedback_government", "feedback_operator"):
        assert sources[column].isna().sum() == 0, column
        assert set(sources[column].unique()) <= permitted, column


def test_not_applicable_is_aligned_across_both_parties(
    sources: pd.DataFrame,
) -> None:
    """A source is either in the feedback programme or not, for both parties.

    Justifies treating "Not Applicable" as a single exclusion bucket rather
    than tracking it per party.
    """
    government_na = sources["feedback_government"] == FEEDBACK_NOT_APPLICABLE
    operator_na = sources["feedback_operator"] == FEEDBACK_NOT_APPLICABLE
    assert (government_na == operator_na).all()


# --- Join integrity ---------------------------------------------------------


def test_join_key_is_unique_on_sources(sources: pd.DataFrame) -> None:
    assert sources[MARS_JOIN_KEY].is_unique


def test_unattributed_plume_count(
    sources: pd.DataFrame, plumes: pd.DataFrame
) -> None:
    """Plumes with a NULL source_name are excluded from case logic."""
    unattributed = check_join_integrity(sources, plumes)
    assert unattributed == EXPECTED_UNATTRIBUTED_PLUMES
    assert plumes[MARS_JOIN_KEY].isna().sum() == EXPECTED_UNATTRIBUTED_PLUMES


def test_every_attributed_plume_resolves_to_a_source(
    sources: pd.DataFrame, plumes: pd.DataFrame
) -> None:
    attributed = plumes[MARS_JOIN_KEY].dropna()
    assert set(attributed) <= set(sources[MARS_JOIN_KEY])
    assert len(attributed) == EXPECTED_PLUME_ROWS - EXPECTED_UNATTRIBUTED_PLUMES


def test_sqlite_join_returns_only_attributed_plumes(loaded_db: Path) -> None:
    """The join a case query will actually run."""
    with sqlite3.connect(loaded_db) as connection:
        joined = connection.execute(
            f"SELECT COUNT(*) FROM {PLUMES_TABLE} p "
            f"JOIN {SOURCES_TABLE} s ON p.{MARS_JOIN_KEY} = s.{MARS_JOIN_KEY}"
        ).fetchone()[0]
    assert joined == EXPECTED_PLUME_ROWS - EXPECTED_UNATTRIBUTED_PLUMES


# --- Units ------------------------------------------------------------------


def test_flux_and_total_emission_use_different_units() -> None:
    """The 1000x trap, asserted so nobody 'tidies' the config into one unit."""
    assert CH4_FLUXRATE_UNIT == "kg/h"
    assert CH4_FLUXRATE_STD_UNIT == "kg/h"
    assert TOTAL_EMISSION_UNIT == "tonnes"
    assert CH4_FLUXRATE_UNIT != TOTAL_EMISSION_UNIT
    assert KG_PER_TONNE == 1000


def test_load_performs_no_unit_conversion(
    snapshot: Path, loaded_db: Path
) -> None:
    """Stored values must be byte-for-byte what UNEP published."""
    csv_plumes = read_member(snapshot, MARS_PLUMES_MEMBER)
    with sqlite3.connect(loaded_db) as connection:
        db_plumes = pd.read_sql(
            f"SELECT ch4_fluxrate, ch4_fluxrate_std, total_emission "
            f"FROM {PLUMES_TABLE}",
            connection,
        )
    for column in ("ch4_fluxrate", "ch4_fluxrate_std", "total_emission"):
        pd.testing.assert_series_equal(
            db_plumes[column], csv_plumes[column], check_names=False
        )


def test_no_derived_mass_columns_were_added(loaded_db: Path) -> None:
    """The load must not invent tonnes/kg columns of its own."""
    with sqlite3.connect(loaded_db) as connection:
        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({PLUMES_TABLE})")
        }
    forbidden = {c for c in columns if "_kg" in c or "_tonne" in c or "_t" == c[-2:]}
    assert not forbidden, f"derived mass columns leaked into the load: {forbidden}"
