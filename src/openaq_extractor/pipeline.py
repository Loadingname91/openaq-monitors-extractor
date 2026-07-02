"""Orchestrate the 4-phase OpenAQ extraction pipeline."""

import time
from pathlib import Path
from typing import Literal

import pandas as pd
from openaq import OpenAQ

from .config import Spec, get_effective_api_key
from .logging_config import setup_logging
from .phases.discover import run_discover
from .phases.measurements import run_measurements
from .phases.sensors import run_sensors
from .writer import write_run_summary

Phase = Literal["discover", "sensors", "measurements", "output"]


def run_pipeline(
    spec: Spec,
    *,
    api_key: str | None = None,
    phase: Phase | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    debug: bool = False,
    log_file: Path | None = None,
) -> None:
    """
    Run the extraction pipeline for the given spec.
    If phase is set, run only that phase (and preceding phases if needed for inputs).
    """
    key = get_effective_api_key(spec, api_key)
    output_dir = Path(spec.output.directory).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_file if log_file is not None else output_dir / "openaq-extract.log"
    setup_logging(log_path, debug=debug)

    # OpenAQ 0.7.0 only accepts api_key; auto_wait/rate_limit_override in newer SDK
    client = OpenAQ(api_key=key)

    start_time = time.perf_counter()
    locations_df = pd.DataFrame()
    sensors_df = pd.DataFrame()

    try:
        if phase in (None, "discover"):
            if verbose:
                print("Phase 1: Discover locations")
            locations_df = run_discover(client, spec, output_dir, dry_run=dry_run, verbose=verbose)
            if locations_df.empty and not dry_run and phase == "discover":
                return

        if phase in (None, "sensors"):
            if locations_df.empty:
                loc_path = output_dir / "locations.csv"
                if loc_path.exists():
                    locations_df = pd.read_csv(loc_path)
            if verbose:
                print("Phase 2: List sensors")
            if not locations_df.empty or dry_run:
                sensors_df = run_sensors(
                    client, spec, output_dir, locations_df,
                    dry_run=dry_run, verbose=verbose, debug=debug,
                )

        if phase in (None, "measurements"):
            if sensors_df.empty:
                sens_path = output_dir / "sensors.csv"
                if sens_path.exists():
                    sensors_df = pd.read_csv(sens_path)
            if verbose:
                print("Phase 3: Fetch measurements")
            if not sensors_df.empty or dry_run:
                run_measurements(
                    client, spec, output_dir, sensors_df,
                    dry_run=dry_run, verbose=verbose, debug=debug,
                )

        if phase in (None, "output"):
            if locations_df.empty:
                loc_path = output_dir / "locations.csv"
                if loc_path.exists():
                    locations_df = pd.read_csv(loc_path)
            if sensors_df.empty:
                sens_path = output_dir / "sensors.csv"
                if sens_path.exists():
                    sensors_df = pd.read_csv(sens_path)
            measurements_count = 0
            meas_path = output_dir / "measurements.csv"
            if meas_path.exists():
                measurements_count = len(pd.read_csv(meas_path))
            write_run_summary(
                output_dir,
                spec,
                total_locations=len(locations_df),
                total_sensors=len(sensors_df),
                total_measurements=measurements_count,
                runtime_seconds=time.perf_counter() - start_time,
            )
            if verbose:
                print("Phase 4: Output written")
    finally:
        client.close()

    elapsed = time.perf_counter() - start_time
    if verbose:
        print(f"Completed in {elapsed:.1f}s")
