"""Fetch and inspect the UNEP IMEO MARS 'sources and plumes' dataset.

INSPECTION ONLY. This module downloads the published snapshot into data/raw/
and prints the real schema. It deliberately performs no parsing, no
normalisation, no unit conversion and no database writes: the point of this
slice is to see exactly what UNEP publishes before we design a data model.

Data source
-----------
UNEP IMEO Eye on Methane data platform, methanedata.unep.org, "Download
dataset" page, link labelled "MARS sources and plumes ... CSV". The download
page links directly to the Azure blob URL in MARS_CSV_ZIP_URL below; no form,
no registration and no API key are involved.

Licence: CC BY-NC-SA 4.0. UNEP IMEO must be attributed wherever this data is
displayed. The licence text ships inside the archive itself.

Publication lag is 30-75 days, so this is NOT real-time data.

Units: the flux rate column is published in kg/h. This module does not convert
it. Any conversion belongs downstream, at the presentation boundary.

Usage:
    python etl/fetch_mars.py
"""

from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

# --- Constants (each with its source) ---------------------------------------

# Source: methanedata.unep.org/download-dataset, anchor labelled "CSV" under
# the heading "MARS sources and plumes". Verified 2026-09-15.
MARS_CSV_ZIP_URL = (
    "https://unepazeconomyadlsstorage.blob.core.windows.net/public/"
    "unep_methanedata_detected_plumes_csv.zip"
)

# Source: methanedata.unep.org/dict-mars-plumes (MARS methane plumes data
# dictionary), linked from the same download page. Recorded here so a reader
# can check our field interpretation against the publisher's own definitions.
MARS_DATA_DICTIONARY_URL = "https://methanedata.unep.org/dict-mars-plumes"

# Stem of the archive as published, used to build the dated local filename.
ARCHIVE_STEM = "unep_methanedata_detected_plumes_csv"

# Members of the published archive, as observed on 2026-09-15. We do not assume
# these are the only members; the script lists whatever it actually finds.
PLUMES_MEMBER = "unep_methanedata_detected_plumes.csv"
SOURCES_MEMBER = "unep_methanedata_detected_sources.csv"

# Repository layout: this file is etl/fetch_mars.py, so the project root is its
# grandparent directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

# The methanedata.unep.org WAF rejects clients that send no browser-like
# User-Agent (observed: HTTP 403 for curl's default UA, HTTP 200 with a browser
# UA). The Azure blob itself does not require this, but we send a descriptive
# UA so our traffic is identifiable in UNEP's logs.
USER_AGENT = (
    "methane-accountability-etl/0.1 (research prototype; "
    "contact via repository) httpx"
)

# Network read timeout in seconds. The archive is ~10 MB; a generous timeout
# avoids spurious failures on slow links.
DOWNLOAD_TIMEOUT_S = 300.0

# How many sample rows to print transposed, so every field is readable.
SAMPLE_ROWS = 5

# Substrings used to LOCATE the feedback and persistency columns. We match on
# these rather than hard-coding full column names, because the exact published
# names are what this slice is trying to discover.
FEEDBACK_COLUMN_HINT = "feedback"
PERSISTENCY_COLUMN_HINTS = ("persistenc", "persistanc", "persist")


# --- Download ---------------------------------------------------------------


def dated_archive_path(download_date: date, raw_dir: Path = RAW_DIR) -> Path:
    """Return the local path for the snapshot downloaded on `download_date`."""
    return raw_dir / f"{ARCHIVE_STEM}_{download_date.isoformat()}.zip"


def download_mars_archive(
    download_date: date | None = None,
    raw_dir: Path = RAW_DIR,
) -> Path:
    """Download the MARS archive into `raw_dir`, named with the download date.

    An existing snapshot for the same date is never overwritten: the existing
    file is reused and returned. Raw snapshots are immutable by convention.

    Returns the path to the local archive.
    """
    download_date = download_date or date.today()
    target = dated_archive_path(download_date, raw_dir)

    if target.exists():
        print(
            f"Snapshot for {download_date.isoformat()} already exists, "
            f"not overwriting: {target.relative_to(PROJECT_ROOT)} "
            f"({target.stat().st_size:,} bytes)"
        )
        return target

    raw_dir.mkdir(parents=True, exist_ok=True)

    # Download to a temporary sibling first, then rename, so an interrupted
    # download can never leave a truncated file posing as a raw snapshot.
    partial = target.with_suffix(".zip.partial")
    print(f"Downloading {MARS_CSV_ZIP_URL}")
    with httpx.stream(
        "GET",
        MARS_CSV_ZIP_URL,
        timeout=DOWNLOAD_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)

    if not zipfile.is_zipfile(partial):
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded file from {MARS_CSV_ZIP_URL} is not a zip archive. "
            "Refusing to keep it. The publisher may have changed the download."
        )

    partial.rename(target)
    print(
        f"Saved {target.relative_to(PROJECT_ROOT)} "
        f"({target.stat().st_size:,} bytes)"
    )
    return target


# --- Inspection helpers -----------------------------------------------------


def banner(text: str) -> None:
    """Print a visually distinct section header."""
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def find_columns(columns: list[str], hints: tuple[str, ...]) -> list[str]:
    """Return columns whose lowercased name contains any of `hints`."""
    lowered = {column: column.lower() for column in columns}
    return [
        column
        for column, low in lowered.items()
        if any(hint in low for hint in hints)
    ]


def describe_frame(name: str, frame: pd.DataFrame) -> None:
    """Print the full schema picture for one CSV: columns, dtypes, nulls."""
    banner(f"{name} — shape")
    print(f"rows:    {len(frame):,}")
    print(f"columns: {len(frame.columns)}")

    banner(f"{name} — full column list")
    for position, column in enumerate(frame.columns):
        print(f"{position:>3}  {column}")

    banner(f"{name} — dtypes, null counts, distinct counts")
    summary = pd.DataFrame(
        {
            "dtype": frame.dtypes.astype(str),
            "nulls": frame.isna().sum(),
            "null_pct": (frame.isna().sum() / len(frame) * 100).round(1),
            "distinct": frame.nunique(dropna=True),
        }
    )
    with pd.option_context(
        "display.max_rows", None,
        "display.max_columns", None,
        "display.width", 200,
        "display.max_colwidth", 60,
    ):
        print(summary)


def describe_categoricals(name: str, frame: pd.DataFrame) -> None:
    """Print value counts for the feedback and persistency columns.

    The real column names are discovered from the data, not assumed. If a
    category of column cannot be found, that is a finding, so say so loudly
    rather than silently skipping it.
    """
    groups: list[tuple[str, list[str]]] = [
        ("feedback", find_columns(list(frame.columns), (FEEDBACK_COLUMN_HINT,))),
        ("persistency", find_columns(list(frame.columns), PERSISTENCY_COLUMN_HINTS)),
    ]

    for label, matched in groups:
        banner(f"{name} — {label} columns: value counts")
        if not matched:
            print(f"!!! NO {label.upper()} COLUMN FOUND in {name}.")
            print("!!! Do not assume the CLAUDE.md name exists. Columns are:")
            print(f"!!! {list(frame.columns)}")
            continue
        print(f"matched columns: {matched}\n")
        for column in matched:
            counts = frame[column].value_counts(dropna=False)
            print(f"--- {column} ---")
            with pd.option_context("display.max_rows", None):
                print(counts)
            print(f"(null/NaN: {frame[column].isna().sum():,})")
            print()


def print_sample_transposed(
    name: str, frame: pd.DataFrame, rows: int = SAMPLE_ROWS
) -> None:
    """Print the first `rows` rows transposed, so every field is readable."""
    banner(f"{name} — first {rows} rows, transposed")
    with pd.option_context(
        "display.max_rows", None,
        "display.max_columns", None,
        "display.width", 200,
        "display.max_colwidth", 80,
    ):
        print(frame.head(rows).T)


def load_member(archive: Path, member: str) -> pd.DataFrame:
    """Load one CSV member of the archive with pandas' default type inference.

    No dtype overrides, no date parsing, no renaming: we want to see what the
    file actually contains and what pandas natively makes of it.
    """
    with zipfile.ZipFile(archive) as bundle:
        if member not in bundle.namelist():
            raise FileNotFoundError(
                f"{member!r} is not in {archive.name}. "
                f"Members present: {bundle.namelist()}"
            )
        with bundle.open(member) as handle:
            return pd.read_csv(handle)


def inspect_archive(archive: Path) -> None:
    """Print the schema of every CSV in the downloaded archive."""
    if not archive.exists():
        raise FileNotFoundError(f"No MARS snapshot at {archive}")

    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()

    banner(f"ARCHIVE CONTENTS — {archive.name}")
    for info in members:
        stamp = "%04d-%02d-%02d %02d:%02d:%02d" % info.date_time
        print(f"{info.filename:<45} {info.file_size:>12,} bytes   {stamp}")
    print(f"\nData dictionary: {MARS_DATA_DICTIONARY_URL}")
    print("Licence: CC BY-NC-SA 4.0 — UNEP IMEO must be attributed in the UI.")
    print("Publication lag 30-75 days: this is NOT real-time data.")

    csv_members = [
        info.filename
        for info in members
        if info.filename.lower().endswith(".csv")
    ]
    if not csv_members:
        raise RuntimeError(
            f"No CSV members in {archive}. Members: "
            f"{[i.filename for i in members]}"
        )

    # Inspect the sources file first: it is the smaller, one-row-per-source
    # table, and it frames what the per-plume file is keyed against.
    ordered = sorted(csv_members, key=lambda n: n != SOURCES_MEMBER)

    for member in ordered:
        frame = load_member(archive, member)
        describe_frame(member, frame)
        describe_categoricals(member, frame)
        print_sample_transposed(member, frame)


def main() -> int:
    """Download today's snapshot if absent, then print its schema."""
    archive = download_mars_archive()
    inspect_archive(archive)
    banner("DONE — inspection only, nothing parsed, nothing stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
