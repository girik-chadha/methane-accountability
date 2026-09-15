# Decisions

## 2026-09-15 — WSL2 Ubuntu over native Windows
Windows ARM64 Python is tier-3 experimental; Shapely/GDAL have no official
win_arm64 wheels. Linux aarch64 has full manylinux wheels. Verified working.

## 2026-09-15 — Two-tier data pipeline
Heavy geospatial ETL runs once offline and commits a compact normalised output.
Runtime service never imports geopandas. Keeps deploys small and reproducible.