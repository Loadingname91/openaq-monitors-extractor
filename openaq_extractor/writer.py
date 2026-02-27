"""Phase 4: Write run summary and consolidate outputs."""

import json
from pathlib import Path
from typing import Any

from .config import Spec


def write_run_summary(
    output_dir: Path,
    spec: Spec,
    *,
    total_locations: int = 0,
    total_sensors: int = 0,
    total_measurements: int = 0,
    runtime_seconds: float = 0,
) -> None:
    """Write run_summary.json with metadata about the extraction run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "spec": {
            "name": spec.name,
            "country_iso": spec.country.iso,
            "pollutants": spec.pollutants,
            "aggregation": spec.aggregation,
            "date_range": {"from": spec.date_range.from_, "to": spec.date_range.to},
        },
        "total_locations": total_locations,
        "total_sensors": total_sensors,
        "total_measurements": total_measurements,
        "output_directory": str(output_dir.resolve()),
        "runtime_seconds": round(runtime_seconds, 2),
    }
    out_path = output_dir / "run_summary.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
