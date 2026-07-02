"""Phase 3: Fetch measurements per sensor with date range and aggregation."""

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from openaq import OpenAQ
from openaq.shared.types import Data, Rollup
from tqdm import tqdm

from ..config import Spec
from ..rate_limiter import RETRYABLE_EXCEPTIONS, api_call

logger = logging.getLogger("openaq_extractor.measurements")

# Map spec aggregation to OpenAQ measurements (data, rollup)
AGGREGATION_MAP: dict[str, tuple[Data | None, Rollup | None]] = {
    "raw": (None, None),
    "hourly": ("hours", None),
    "daily": ("days", None),
    "monthly": ("days", "monthly"),
    "yearly": ("years", None),
}


def _get_data_rollup(aggregation: str) -> tuple[Data | None, Rollup | None]:
    """Return (data, rollup) for the given aggregation level."""
    return AGGREGATION_MAP.get(aggregation, ("days", None))


def run_measurements(
    client: OpenAQ,
    spec: Spec,
    output_dir: Path,
    sensors_df: pd.DataFrame,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    debug: bool = False,
) -> pd.DataFrame:
    """
    For each sensor, fetch measurements in the date range at the spec's aggregation level.
    Paginates through all results per sensor. Skips sensors that fail after retries.
    """
    date_from = spec.date_range.from_
    date_to = spec.date_range.to
    data, rollup = _get_data_rollup(spec.aggregation)
    limit = 1000

    sensors = sensors_df[["sensor_id", "location_id", "parameter_name", "parameter_units"]].drop_duplicates().to_dict("records")

    if dry_run:
        if verbose:
            print(f"[DRY RUN] Would fetch measurements for {len(sensors)} sensors, {date_from}..{date_to}")
        return pd.DataFrame()

    measurements_rows: list[dict[str, Any]] = []
    api_calls = 0
    skipped_rows: list[dict[str, Any]] = []

    for row in tqdm(sensors, desc="Fetching measurements", disable=not verbose):
        sensor_id = int(row["sensor_id"])
        location_id = int(row["location_id"])
        param_name = row["parameter_name"]
        param_units = row.get("parameter_units") or ""

        page = 1
        sensor_ok = True
        while sensor_ok:
            try:
                resp = api_call(
                    lambda sid=sensor_id, pg=page: client.measurements.list(
                        sensors_id=sid,
                        data=data,
                        rollup=rollup,
                        datetime_from=date_from,
                        datetime_to=date_to,
                        page=pg,
                        limit=limit,
                    ),
                    verbose=verbose,
                    context=f"sensor_id={sensor_id}, location_id={location_id}",
                )
            except RETRYABLE_EXCEPTIONS as e:
                err_msg = str(e)
                logger.warning(
                    "Skipping sensor_id=%s location_id=%s after retries: %s",
                    sensor_id,
                    location_id,
                    e,
                    exc_info=debug,
                )
                skipped_rows.append({
                    "sensor_id": sensor_id,
                    "location_id": location_id,
                    "parameter_name": param_name,
                    "parameter_units": param_units,
                    "error": err_msg,
                })
                sensor_ok = False
                break

            api_calls += 1

            for m in resp.results:
                period = m.period
                coverage = m.coverage
                cov_pct = coverage.percent_complete if coverage else None

                dt_from_utc = period.datetime_from.utc if period and period.datetime_from else None
                dt_to_utc = period.datetime_to.utc if period and period.datetime_to else None
                dt_from_local = period.datetime_from.local if period and period.datetime_from else None
                dt_to_local = period.datetime_to.local if period and period.datetime_to else None

                measurements_rows.append({
                    "sensor_id": sensor_id,
                    "location_id": location_id,
                    "parameter_name": param_name,
                    "value": m.value,
                    "unit": param_units or (m.parameter.units if m.parameter else ""),
                    "datetime_from_utc": dt_from_utc,
                    "datetime_to_utc": dt_to_utc,
                    "datetime_from_local": dt_from_local,
                    "datetime_to_local": dt_to_local,
                    "coverage_percent": cov_pct,
                })

            if len(resp.results) < limit:
                break

            page += 1

    if skipped_rows:
        skipped_df = pd.DataFrame(skipped_rows)
        output_dir.mkdir(parents=True, exist_ok=True)
        skipped_path = output_dir / "skipped_sensors.csv"
        skipped_df.to_csv(skipped_path, index=False)
        logger.warning("Skipped %d sensors, saved to %s", len(skipped_rows), skipped_path)
        if verbose:
            print(f"  Skipped {len(skipped_rows)} sensors, saved to {skipped_path}")

    df = pd.DataFrame(measurements_rows)

    if not df.empty:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / "measurements.csv"
        df.to_csv(out_path, index=False)
        if verbose:
            print(f"  Saved {len(df)} measurements to {out_path}")

    if verbose:
        print(f"  API calls: {api_calls}")

    return df
