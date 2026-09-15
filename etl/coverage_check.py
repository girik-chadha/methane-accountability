"""Go/no-go coverage check: how many MARS cases sit in a country with assets?

COVERAGE COUNTS ONLY. No normalisation beyond the five documented country
aliases, no spatial join, no matching. For each MARS case country this prints
the case count next to asset counts from every infrastructure snapshot in
data/raw/, then the fraction of cases in countries above stated thresholds.

OGIM is 2.9 GB and is never loaded: per-layer counts come from SQLite-side
"SELECT COUNTRY, COUNT(*) ... GROUP BY COUNTRY" through pyogrio.
"""

from __future__ import annotations

import sqlite3
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pyogrio
import shapely

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import (  # noqa: E402
    EXTRACTION_FIELD_SHEET,
    FEEDBACK_NO,
    GEM_EXTRACTION_XLSX,
    GEM_GAS_PIPELINES_GPKG,
    GEM_GOGPT_XLSX,
    GEM_LNG_TERMINALS_GPKG,
    GOGPT_UNITS_SHEET,
    MARS_DB_PATH,
    MARS_TO_GEM_COUNTRY_ALIASES,
    OGIM_GPKG,
    RAW_DIR,
    SOURCES_TABLE,
)
from etl.xlsx_stream import read_sheet_raw  # noqa: E402

TOP_COUNTRIES = 15
LIST_SEPARATOR = ", "
# "Usable" thresholds on upstream point assets per country, for the bottom line.
THRESHOLDS = (1, 10, 100)
OGIM_WELLS_LAYER = "Oil_and_Natural_Gas_Wells"
OGIM_FLARES_LAYER = "Natural_Gas_Flaring_Detections"
OGIM_CATALOG_LAYER = "Data_Catalog"
OGIM_CATALOG_GLOBAL = "VARIOUS"


def aliases(country: str) -> set[str]:
    return {country.casefold(), *(a.casefold() for a in MARS_TO_GEM_COUNTRY_ALIASES.get(country, ()))}


def count_lists(values: pd.Series, countries: list[str]) -> dict[str, int]:
    """Count rows whose comma-separated country list contains each country."""
    parts = values.fillna("").astype(str).str.split(LIST_SEPARATOR)
    exploded = parts.explode().str.strip().str.casefold()
    counts = exploded.value_counts()
    return {c: int(sum(counts.get(a, 0) for a in aliases(c))) for c in countries}


def count_weighted(distinct: pd.DataFrame, countries: list[str]) -> dict[str, int]:
    """Same as count_lists but from a (COUNTRY, n) group-by result."""
    out = {c: 0 for c in countries}
    for value, n in zip(distinct["COUNTRY"].fillna(""), distinct["n"]):
        members = {p.strip().casefold() for p in str(value).split(LIST_SEPARATOR)}
        for c in countries:
            if members & aliases(c):
                out[c] += int(n)
    return out


def mars_cases() -> pd.Series:
    with sqlite3.connect(MARS_DB_PATH) as connection:
        frame = pd.read_sql(
            f"SELECT country, COUNT(*) AS cases FROM {SOURCES_TABLE} "
            "WHERE feedback_government = ? AND feedback_operator = ? GROUP BY country",
            connection, params=(FEEDBACK_NO, FEEDBACK_NO),
        )
    return frame.set_index("country")["cases"].sort_values(ascending=False)


def main() -> int:
    cases = mars_cases()
    countries = list(cases.index)
    table = pd.DataFrame({"cases": cases})

    fields = read_sheet_raw(RAW_DIR / GEM_EXTRACTION_XLSX, EXTRACTION_FIELD_SHEET)
    has_xy = pd.to_numeric(fields["Latitude"], errors="coerce").notna() & pd.to_numeric(fields["Longitude"], errors="coerce").notna()
    table["GEM extraction (xy)"] = pd.Series(count_lists(fields.loc[has_xy, "Country/Area"], countries))

    pipes = pyogrio.read_dataframe(RAW_DIR / GEM_GAS_PIPELINES_GPKG, columns=["CountriesOrAreas"])
    routed = ~shapely.is_empty(pipes.geometry.values)
    table["GEM pipelines (routed)"] = pd.Series(count_lists(pipes.loc[routed, "CountriesOrAreas"], countries))

    lng = pyogrio.read_dataframe(RAW_DIR / GEM_LNG_TERMINALS_GPKG, columns=["Country/Area"], read_geometry=False)
    table["GEM LNG units"] = pd.Series(count_lists(lng["Country/Area"], countries))

    plants = read_sheet_raw(RAW_DIR / GEM_GOGPT_XLSX, GOGPT_UNITS_SHEET)
    per_plant = plants.drop_duplicates("GEM location ID")
    table["GEM power plants"] = pd.Series(count_lists(per_plant["Country/Area"], countries))

    ogim = RAW_DIR / OGIM_GPKG
    facilities = {c: 0 for c in countries}
    polygons = {c: 0 for c in countries}
    lines = {c: 0 for c in countries}
    for layer, gtype in pyogrio.list_layers(ogim):
        if layer == OGIM_CATALOG_LAYER:
            continue
        grouped = pyogrio.read_dataframe(
            ogim, sql=f'SELECT COUNTRY, COUNT(*) AS n FROM "{layer}" GROUP BY COUNTRY',
            sql_dialect="SQLITE", read_geometry=False,
        )
        counts = count_weighted(grouped, countries)
        if layer == OGIM_WELLS_LAYER:
            table["OGIM wells"] = pd.Series(counts)
        elif layer == OGIM_FLARES_LAYER:
            table["OGIM flare detections"] = pd.Series(counts)
        elif gtype == "Point":
            facilities = {c: facilities[c] + counts[c] for c in countries}
        elif "Pipelines" in layer:
            lines = {c: lines[c] + counts[c] for c in countries}
        else:
            polygons = {c: polygons[c] + counts[c] for c in countries}
    table["OGIM facilities (points)"] = pd.Series(facilities)
    table["OGIM pipelines"] = pd.Series(lines)
    table["OGIM fields/basins/blocks"] = pd.Series(polygons)

    catalog = pyogrio.read_dataframe(ogim, layer=OGIM_CATALOG_LAYER, read_geometry=False)
    dedicated = count_lists(catalog["COUNTRY"], countries)
    table["OGIM dedicated sources"] = pd.Series(dedicated)

    table["upstream point assets"] = (
        table["GEM extraction (xy)"] + table["OGIM facilities (points)"] + table["OGIM wells"]
    )

    print(f"MARS cases (both feedback columns == {FEEDBACK_NO!r}): {int(cases.sum()):,} in {len(countries)} countries")
    print(f"Top {TOP_COUNTRIES} shown; every ratio below is over all {int(cases.sum()):,} cases.\n")
    with pd.option_context("display.max_columns", None, "display.width", 250):
        print(table.head(TOP_COUNTRIES).to_string())

    print("\nBOTTOM LINE — cases in a country with at least N upstream point assets")
    print("(GEM extraction units with coordinates + OGIM facility points + OGIM wells; power plants,")
    print(" pipelines, polygons and flare detections excluded from the threshold):")
    total = int(cases.sum())
    for n in THRESHOLDS:
        covered = int(table.loc[table["upstream point assets"] >= n, "cases"].sum())
        print(f"  >= {n:>3}: {covered:>5,} / {total:,} = {covered / total * 100:5.1f}% covered,  {total - covered:>5,} = {(total - covered) / total * 100:5.1f}% not")
    sparse = table["upstream point assets"] < table["cases"]
    starved = int(table.loc[sparse, "cases"].sum())
    print(f"  threshold-free: cases in countries with FEWER upstream point assets than cases: "
          f"{starved:,} / {total:,} = {starved / total * 100:5.1f}%  "
          f"({', '.join(table.index[sparse])})")
    without_ogim = table["GEM extraction (xy)"]
    for n in THRESHOLDS:
        covered = int(table.loc[without_ogim >= n, "cases"].sum())
        print(f"  (GEM extraction alone, >= {n:>3}: {covered / total * 100:5.1f}%)")
    print("\nOGIM Data_Catalog: countries with at least one dedicated (non-'VARIOUS') source dataset:",
          ", ".join(c for c in countries if dedicated[c]) or "none of the case countries")
    global_sources = int((catalog["COUNTRY"].fillna("").str.upper() == OGIM_CATALOG_GLOBAL).sum())
    print(f"'{OGIM_CATALOG_GLOBAL}' (global, multi-country) sources in the catalog: {global_sources} of {len(catalog)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
