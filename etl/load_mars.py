"""Load the MARS sources and plumes CSVs from a dated snapshot into SQLite.

Reads both CSVs in place from the zip under data/raw/ and writes them to
data/processed/mars.db as two tables, `sources` and `plumes`, joined on
`source_name`.

Deliberate non-goals
--------------------
No unit conversion. No derived mass columns. No normalisation or renaming:
published column names are preserved exactly, so anyone can diff our tables
against UNEP's CSVs without a translation layer. Interpretation belongs
downstream, where it can be tested.

Units warning: `ch4_fluxrate` and `ch4_fluxrate_std` are kg/h, while
`total_emission` and `total_emission_std` are TONNES. Both are stored here
verbatim, in their published units. See backend/app/config.py.

Usage:
    python etl/load_mars.py                # newest snapshot in data/raw/
    python etl/load_mars.py 2026-09-15     # a specific snapshot date
"""

from __future__ import annotations

import sqlite3
import sys
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

# This is a one-off ETL script, not part of the runtime service, so it may
# reach into the backend package for shared constants. The reverse direction
# (backend importing from etl) is forbidden.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import (  # noqa: E402
    FEEDBACK_NO,
    FEEDBACK_NOT_APPLICABLE,
    MARS_ARCHIVE_STEM,
    MARS_DB_PATH,
    MARS_JOIN_KEY,
    MARS_PLUMES_MEMBER,
    MARS_SOURCES_MEMBER,
    PLUMES_TABLE,
    PROCESSED_DIR,
    RAW_DIR,
    SOURCES_TABLE,
)


# --- Snapshot selection -----------------------------------------------------


def find_snapshot(
    snapshot_date: date | None = None, raw_dir: Path = RAW_DIR
) -> Path:
    """Return the raw snapshot for `snapshot_date`, or the newest one.

    Raises if no snapshot exists. We never download here and never fall back to
    sample data: a missing dataset is a hard failure.
    """
    if snapshot_date is not None:
        path = raw_dir / f"{MARS_ARCHIVE_STEM}_{snapshot_date.isoformat()}.zip"
        if not path.exists():
            raise FileNotFoundError(
                f"No MARS snapshot for {snapshot_date.isoformat()} at {path}. "
                "Run etl/fetch_mars.py first."
            )
        return path

    # Filenames end in an ISO date, so lexicographic order is date order.
    candidates = sorted(raw_dir.glob(f"{MARS_ARCHIVE_STEM}_*.zip"))
    if not candidates:
        raise FileNotFoundError(
            f"No MARS snapshot found in {raw_dir}. Run etl/fetch_mars.py first."
        )
    return candidates[-1]


def read_member(archive: Path, member: str) -> pd.DataFrame:
    """Read one CSV member of the zip in place, with no dtype overrides."""
    with zipfile.ZipFile(archive) as bundle:
        if member not in bundle.namelist():
            raise FileNotFoundError(
                f"{member!r} missing from {archive.name}. "
                f"Members: {bundle.namelist()}"
            )
        with bundle.open(member) as handle:
            return pd.read_csv(handle)


# --- Join integrity ---------------------------------------------------------


def check_join_integrity(sources: pd.DataFrame, plumes: pd.DataFrame) -> int:
    """Validate the sources-to-plumes join. Returns the orphan-plume count.

    Raises if the join key is not unique on sources, or if a plume references a
    source that does not exist. Plumes with a NULL source_name are counted and
    returned, not treated as an error: UNEP publishes them as unattributed
    detections.
    """
    duplicated = sources[MARS_JOIN_KEY].duplicated().sum()
    if duplicated:
        raise ValueError(
            f"{MARS_JOIN_KEY} is not unique on the sources table "
            f"({duplicated} duplicates). The join assumption is broken."
        )

    unattributed = int(plumes[MARS_JOIN_KEY].isna().sum())
    known = set(sources[MARS_JOIN_KEY])
    dangling = plumes[MARS_JOIN_KEY].dropna()
    missing = sorted(set(dangling) - known)
    if missing:
        raise ValueError(
            f"{len(missing)} plume {MARS_JOIN_KEY} values have no matching "
            f"source row, e.g. {missing[:5]}. Refusing to load a broken join."
        )
    return unattributed


# --- SQLite -----------------------------------------------------------------


def write_sqlite(
    sources: pd.DataFrame,
    plumes: pd.DataFrame,
    db_path: Path = MARS_DB_PATH,
) -> None:
    """Write both frames to SQLite, replacing any existing tables.

    The database is a derived artefact, fully reproducible from the immutable
    raw snapshot, so replacing it wholesale is safe.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        sources.to_sql(SOURCES_TABLE, connection, if_exists="replace", index=False)
        plumes.to_sql(PLUMES_TABLE, connection, if_exists="replace", index=False)
        connection.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_key "
            f"ON {SOURCES_TABLE}({MARS_JOIN_KEY})"
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_plumes_key "
            f"ON {PLUMES_TABLE}({MARS_JOIN_KEY})"
        )


# --- Reporting --------------------------------------------------------------


def report_feedback(sources: pd.DataFrame) -> None:
    """Print non-response counts, always against a shown denominator.

    A bare count here is misleading, because most sources were never in the
    feedback programme at all. Every figure is printed as n/denominator so the
    "Not Applicable" majority can never be silently folded into "ignored us".
    """
    total = len(sources)
    government = sources["feedback_government"]
    operator = sources["feedback_operator"]

    not_applicable = int(
        (
            (government == FEEDBACK_NOT_APPLICABLE)
            & (operator == FEEDBACK_NOT_APPLICABLE)
        ).sum()
    )
    tracked = total - not_applicable

    government_no = int((government == FEEDBACK_NO).sum())
    operator_no = int((operator == FEEDBACK_NO).sum())
    both_no = int(((government == FEEDBACK_NO) & (operator == FEEDBACK_NO)).sum())

    def line(label: str, count: int, denominator: int, note: str) -> str:
        share = count / denominator * 100 if denominator else 0.0
        return f"  {label:<34} {count:>6,} / {denominator:>6,}  ({share:5.1f}%)  {note}"

    print()
    print("=" * 78)
    print("FEEDBACK STATUS BY SOURCE")
    print("=" * 78)
    print(f"  {'total sources in snapshot':<34} {total:>6,}")
    print()
    print(line("excluded: Not Applicable", not_applicable, total,
               "never solicited; NOT a non-response"))
    print(line("tracked (Yes or No)", tracked, total,
               "the real accountability denominator"))
    print()
    print(line("feedback_government == No", government_no, tracked,
               "of tracked sources"))
    print(line("feedback_operator   == No", operator_no, tracked,
               "of tracked sources"))
    print(line("BOTH == No", both_no, tracked,
               "nobody replied: the case backlog"))
    print()
    print(f"  Same three figures against ALL {total:,} sources, for contrast:")
    print(line("feedback_government == No", government_no, total, "of all sources"))
    print(line("feedback_operator   == No", operator_no, total, "of all sources"))
    print(line("BOTH == No", both_no, total, "of all sources"))
    print()
    print("  Quoting any of these without its denominator overstates the")
    print("  problem. 'Not Applicable' means UNEP never asked.")


def main(argv: list[str] | None = None) -> int:
    """Load the snapshot into SQLite and report feedback status."""
    argv = sys.argv[1:] if argv is None else argv
    snapshot_date = date.fromisoformat(argv[0]) if argv else None

    archive = find_snapshot(snapshot_date)
    print(f"Snapshot: {archive.relative_to(PROJECT_ROOT)}")

    sources = read_member(archive, MARS_SOURCES_MEMBER)
    plumes = read_member(archive, MARS_PLUMES_MEMBER)
    unattributed = check_join_integrity(sources, plumes)

    write_sqlite(sources, plumes)

    print(f"Wrote {MARS_DB_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  {SOURCES_TABLE:<8} {len(sources):>7,} rows x {len(sources.columns)} cols")
    print(f"  {PLUMES_TABLE:<8} {len(plumes):>7,} rows x {len(plumes.columns)} cols")
    print(
        f"  plumes with NULL {MARS_JOIN_KEY}: {unattributed:,} / {len(plumes):,} "
        f"({unattributed / len(plumes) * 100:.1f}%) — stored, but outside case logic"
    )
    print("  units stored as published: ch4_fluxrate* kg/h, total_emission* TONNES")

    report_feedback(sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
