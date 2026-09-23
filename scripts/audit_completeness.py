#!/usr/bin/env python3
"""Reconcile sensors.csv / measurements.csv per country to check for silent data loss.

Three checks, per sensor:
  1. zero-measurement: sensor exists in sensors.csv but has no rows at all in
     measurements.csv (strongest signal of a fully-dropped sensor).
  2. day-coverage ratio: for daily aggregation, expected one row per calendar
     day between the sensor's active window (bounded by the location's
     datetime_first and the sensor's last_datetime) and the spec's date_range,
     vs the actual distinct days present. Flags sensors far below expectation
     (real-world sensor downtime is normal, so this is a "look closer" signal,
     not pass/fail).
  3. date-span check: whether the sensor's actual first/last measurement dates
     land near its expected window, or stop suspiciously early / start
     suspiciously late.

Run on a compute node, not a login node -- India's measurements.csv is ~3M rows.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from openaq_extractor.config import load_spec  # noqa: E402

ALL_COUNTRIES = ["bhutan", "bangladesh", "nepal", "pakistan", "india"]

DAY_RATIO_FLAG_THRESHOLD = 0.5  # actual_days / expected_days below this -> flag
SPAN_TOLERANCE_DAYS = 30  # actual start/end more than this far from expected -> flag


def audit_country(country: str, output_base: Path, specs_dir: Path) -> pd.DataFrame:
    spec_path = specs_dir / f"{country}.yaml"
    if not spec_path.exists():
        print(f"[{country}] spec not found at {spec_path}, skipping", file=sys.stderr)
        return pd.DataFrame()
    spec = load_spec(spec_path)

    out_dir = Path(spec.output.directory)
    if not out_dir.is_absolute():
        out_dir = (specs_dir.parent / out_dir).resolve()

    locations_path = out_dir / "locations.csv"
    sensors_path = out_dir / "sensors.csv"
    measurements_path = out_dir / "measurements.csv"
    skipped_locations_path = out_dir / "skipped_locations.csv"

    if not sensors_path.exists() or not measurements_path.exists():
        print(f"[{country}] missing sensors.csv or measurements.csv in {out_dir}, skipping", file=sys.stderr)
        return pd.DataFrame()

    locations = pd.read_csv(locations_path) if locations_path.exists() else pd.DataFrame()
    sensors = pd.read_csv(sensors_path)
    measurements = pd.read_csv(measurements_path)
    skipped_locations = pd.read_csv(skipped_locations_path) if skipped_locations_path.exists() else pd.DataFrame()

    spec_from = pd.Timestamp(spec.date_range.from_, tz="UTC")
    spec_to = pd.Timestamp(spec.date_range.to, tz="UTC")

    # --- join sensor metadata with location metadata for active-window bounds ---
    if not locations.empty:
        loc_bounds = locations[["location_id", "datetime_first", "datetime_last"]].copy()
        loc_bounds["datetime_first"] = pd.to_datetime(loc_bounds["datetime_first"], utc=True, errors="coerce")
        loc_bounds["datetime_last"] = pd.to_datetime(loc_bounds["datetime_last"], utc=True, errors="coerce")
        merged = sensors.merge(loc_bounds, on="location_id", how="left")
    else:
        merged = sensors.copy()
        merged["datetime_first"] = pd.NaT
        merged["datetime_last"] = pd.NaT

    merged["last_datetime"] = pd.to_datetime(merged.get("last_datetime"), utc=True, errors="coerce")

    def expected_start(row):
        return max(spec_from, row["datetime_first"]) if pd.notna(row["datetime_first"]) else spec_from

    def expected_end(row):
        candidates = [spec_to]
        if pd.notna(row["last_datetime"]):
            candidates.append(row["last_datetime"])
        if pd.notna(row["datetime_last"]):
            candidates.append(row["datetime_last"])
        return min(candidates)

    merged["expected_start"] = merged.apply(expected_start, axis=1)
    merged["expected_end"] = merged.apply(expected_end, axis=1)
    merged["expected_days"] = (merged["expected_end"] - merged["expected_start"]).dt.days.clip(lower=0) + 1

    # --- per-sensor actuals from measurements.csv ---
    measurements["date_from"] = pd.to_datetime(measurements["datetime_from_utc"], utc=True, errors="coerce")
    measurements["date_to"] = pd.to_datetime(measurements["datetime_to_utc"], utc=True, errors="coerce")

    actuals = measurements.groupby("sensor_id").agg(
        actual_days=("date_from", lambda s: s.dt.date.nunique()),
        actual_start=("date_from", "min"),
        actual_end=("date_to", "max"),
        mean_coverage_percent=("coverage_percent", "mean"),
        row_count=("sensor_id", "count"),
    ).reset_index()

    merged = merged.merge(actuals, on="sensor_id", how="left")
    merged["actual_days"] = merged["actual_days"].fillna(0).astype(int)
    merged["row_count"] = merged["row_count"].fillna(0).astype(int)

    # --- checks ---
    merged["flag_zero_measurements"] = merged["row_count"] == 0

    with pd.option_context("mode.use_inf_as_na", True):
        ratio = merged["actual_days"] / merged["expected_days"].replace(0, pd.NA)
    merged["day_ratio"] = ratio
    merged["flag_low_day_ratio"] = (merged["expected_days"] > 0) & (
        merged["day_ratio"].fillna(0) < DAY_RATIO_FLAG_THRESHOLD
    )

    start_gap = (merged["actual_start"] - merged["expected_start"]).dt.days
    end_gap = (merged["expected_end"] - merged["actual_end"]).dt.days
    merged["flag_late_start"] = (~merged["flag_zero_measurements"]) & (start_gap > SPAN_TOLERANCE_DAYS)
    merged["flag_early_end"] = (~merged["flag_zero_measurements"]) & (end_gap > SPAN_TOLERANCE_DAYS)

    merged["any_flag"] = (
        merged["flag_zero_measurements"]
        | merged["flag_low_day_ratio"]
        | merged["flag_late_start"]
        | merged["flag_early_end"]
    )

    flagged = merged[merged["any_flag"]].copy()

    n_sensors = len(merged)
    n_zero = int(merged["flag_zero_measurements"].sum())
    n_low_ratio = int(merged["flag_low_day_ratio"].sum())
    n_late_start = int(merged["flag_late_start"].sum())
    n_early_end = int(merged["flag_early_end"].sum())
    n_skipped_locations = len(skipped_locations)

    print(f"[{country}] sensors={n_sensors} skipped_locations={n_skipped_locations} "
          f"zero_measurements={n_zero} low_day_ratio={n_low_ratio} "
          f"late_start={n_late_start} early_end={n_early_end} "
          f"flagged_total={len(flagged)}")

    if not flagged.empty:
        cols = [
            "sensor_id", "location_id", "parameter_name",
            "expected_start", "expected_end", "expected_days",
            "actual_start", "actual_end", "actual_days", "day_ratio",
            "mean_coverage_percent", "row_count",
            "flag_zero_measurements", "flag_low_day_ratio", "flag_late_start", "flag_early_end",
        ]
        out_path = out_dir / "completeness_audit.csv"
        flagged[cols].to_csv(out_path, index=False)
        print(f"  -> wrote {len(flagged)} flagged sensors to {out_path}")

    return flagged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", nargs="+", default=ALL_COUNTRIES, choices=ALL_COUNTRIES,
                         help="Countries to audit (default: all 5)")
    parser.add_argument("--specs-dir", default=str(REPO_ROOT / "specs"),
                         help="Directory containing <country>.yaml spec files")
    args = parser.parse_args()

    specs_dir = Path(args.specs_dir)
    for country in args.country:
        audit_country(country, output_base=REPO_ROOT / "output", specs_dir=specs_dir)


if __name__ == "__main__":
    main()
