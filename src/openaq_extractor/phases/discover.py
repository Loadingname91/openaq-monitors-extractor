"""Phase 1: Discover locations by country ISO code."""

from pathlib import Path
from typing import Any

import pandas as pd
from openaq import OpenAQ

from ..config import Spec
from ..rate_limiter import api_call


def run_discover(
    client: OpenAQ,
    spec: Spec,
    output_dir: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    List all locations for the spec's country, with pagination.
    Saves locations.csv and returns a DataFrame of discovered locations.
    """
    iso = spec.country.iso.upper()
    monitor = spec.filters.monitor
    mobile = spec.filters.mobile
    limit = 1000

    if dry_run:
        if verbose:
            print(f"[DRY RUN] Would discover locations for iso={iso}, monitor={monitor}, mobile={mobile}")
        return pd.DataFrame()

    locations_rows: list[dict[str, Any]] = []
    page = 1
    total_found = None
    api_calls = 0

    while True:
        resp = api_call(
            lambda: client.locations.list(
                page=page,
                limit=limit,
                iso=iso,
                monitor=monitor,
                mobile=mobile,
            ),
            verbose=verbose,
        )
        api_calls += 1

        if verbose:
            print(f"  Page {page}: fetched {len(resp.results)} locations")

        for loc in resp.results:
            lat = loc.coordinates.latitude if loc.coordinates else None
            lon = loc.coordinates.longitude if loc.coordinates else None
            dt_from = loc.datetime_first.utc if loc.datetime_first else None
            dt_to = loc.datetime_last.utc if loc.datetime_last else None
            locations_rows.append({
                "location_id": loc.id,
                "name": loc.name or "",
                "locality": loc.locality or "",
                "latitude": lat,
                "longitude": lon,
                "is_monitor": loc.is_monitor,
                "is_mobile": loc.is_mobile,
                "datetime_first": dt_from,
                "datetime_last": dt_to,
            })

        meta = getattr(resp, "meta", None)
        if meta:
            found = getattr(meta, "found", None)
            if found is not None:
                total_found = int(found) if isinstance(found, str) else found

        if len(resp.results) < limit:
            break

        if total_found is not None and len(locations_rows) >= total_found:
            break

        page += 1

    df = pd.DataFrame(locations_rows)

    if not df.empty:
        out_path = output_dir / "locations.csv"
        output_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False)
        if verbose:
            print(f"  Saved {len(df)} locations to {out_path}")

    if verbose:
        print(f"  API calls: {api_calls}")

    return df
