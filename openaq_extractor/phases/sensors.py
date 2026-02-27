"""Phase 2: List sensors per location, filtered by pollutant names."""

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from openaq import OpenAQ
from tqdm import tqdm

from ..config import Spec
from ..rate_limiter import RETRYABLE_EXCEPTIONS, api_call

logger = logging.getLogger("openaq_extractor.sensors")


def run_sensors(
    client: OpenAQ,
    spec: Spec,
    output_dir: Path,
    locations_df: pd.DataFrame,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    debug: bool = False,
) -> pd.DataFrame:
    """
    For each location, fetch sensors and filter by spec pollutants.
    Saves sensors.csv and returns DataFrame of matching sensors.
    Skips locations that fail after retries.
    """
    pollutant_set = {p.strip().lower() for p in spec.pollutants}
    rows = list(locations_df.to_dict("records"))

    if dry_run:
        if verbose:
            print(f"[DRY RUN] Would fetch sensors for {len(rows)} locations, pollutants={list(pollutant_set)}")
        return pd.DataFrame()

    sensors_rows: list[dict[str, Any]] = []
    api_calls = 0
    skipped_rows: list[dict[str, Any]] = []

    for row in tqdm(rows, desc="Fetching sensors", disable=not verbose):
        loc_id = int(row["location_id"])
        name = str(row.get("name", "") or "")

        try:
            resp = api_call(
                lambda lid=loc_id: client.locations.sensors(lid),
                verbose=verbose,
                context=f"location_id={loc_id}",
            )
        except RETRYABLE_EXCEPTIONS as e:
            err_msg = str(e)
            logger.warning(
                "Skipping location_id=%s name=%r after retries: %s",
                loc_id,
                name,
                e,
                exc_info=debug,
            )
            skipped_rows.append({
                "location_id": loc_id,
                "name": name,
                "locality": row.get("locality", ""),
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
                "is_monitor": row.get("is_monitor"),
                "is_mobile": row.get("is_mobile"),
                "datetime_first": row.get("datetime_first"),
                "datetime_last": row.get("datetime_last"),
                "error": err_msg,
            })
            continue

        api_calls += 1

        for s in resp.results:
            param_name = (s.parameter.name or "").strip().lower()
            if param_name not in pollutant_set:
                continue

            last_val = None
            last_dt = None
            if s.latest:
                last_val = s.latest.value
                last_dt = s.latest.datetime.utc if s.latest.datetime else None

            sensors_rows.append({
                "sensor_id": s.id,
                "location_id": loc_id,
                "parameter_name": param_name,
                "parameter_id": s.parameter.id if s.parameter else None,
                "parameter_units": s.parameter.units if s.parameter else "",
                "last_value": last_val,
                "last_datetime": last_dt,
            })

    df = pd.DataFrame(sensors_rows)

    if not df.empty:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / "sensors.csv"
        df.to_csv(out_path, index=False)
        if verbose:
            print(f"  Saved {len(df)} sensors to {out_path}")

    if verbose:
        print(f"  API calls: {api_calls}")

    if skipped_rows:
        skipped_df = pd.DataFrame(skipped_rows)
        output_dir.mkdir(parents=True, exist_ok=True)
        skipped_path = output_dir / "skipped_locations.csv"
        skipped_df.to_csv(skipped_path, index=False)
        logger.warning("Skipped %d locations, saved to %s", len(skipped_rows), skipped_path)
        if verbose:
            print(f"  Skipped {len(skipped_rows)} locations, saved to {skipped_path}")

    return df
