#!/usr/bin/env python3
"""Build the single-page PM2.5 coverage report from pm25_coverage.py's outputs.

Reads output/coverage/{pm25_location_days,pm25_yearly_counts,pm25_monthly_active}.csv
and borders.json, packs them into one JSON object, and injects it into
scripts/report_template.html at the /*__REPORT_DATA__*/ placeholder. The result,
output/coverage/report/pm25_coverage_report.html, is one self-contained page (it
loads only D3 and Google Fonts from their CDNs).

Cheap to run (small CSVs only) -- fine on a login node.
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "report_template.html"
PLACEHOLDER = "/*__REPORT_DATA__*/null"
COUNTRY_ORDER = ["india", "pakistan", "nepal", "bangladesh", "bhutan"]


def build_data(cov_dir: Path) -> dict:
    locs = pd.read_csv(cov_dir / "pm25_location_days.csv")
    yearly = pd.read_csv(cov_dir / "pm25_yearly_counts.csv")
    monthly = pd.read_csv(cov_dir / "pm25_monthly_active.csv")
    borders = json.loads((cov_dir / "borders.json").read_text())

    # per-year columns are days_YYYY; days_75 (the coverage-threshold count) must not match
    year_cols = sorted(c for c in locs.columns if re.fullmatch(r"days_\d{4}", c))
    years = [int(c[5:]) for c in year_cols]
    countries = [c for c in COUNTRY_ORDER if c in set(locs["country"])]

    # compact row arrays keep the embedded JSON small; field order is documented in `fields`
    loc_fields = ["c", "name", "lat", "lon", "monitor", "sensors", "days", "days75", "first", "last", "byYear"]
    loc_rows = [
        [
            countries.index(r.country),
            "" if pd.isna(r.name) else str(r.name).strip(),
            round(float(r.latitude), 4),
            round(float(r.longitude), 4),
            1 if r.sensor_type == "monitor" else 0,
            int(r.n_sensors),
            int(r.days_any),
            int(r.days_75),
            "" if pd.isna(r.first_date) else str(r.first_date)[:10],
            "" if pd.isna(r.last_date) else str(r.last_date)[:10],
            [int(getattr(r, c)) for c in year_cols],
        ]
        for r in locs.itertuples(index=False)
    ]

    yearly_rows = [
        [countries.index(r.country), int(r.year), 1 if r.sensor_type == "monitor" else 0,
         int(r.n_locations), int(r.n_locations_30d), int(r.n_sensors), int(r.n_sensors_30d),
         int(r.location_days_any), int(r.location_days_75)]
        for r in yearly.itertuples(index=False) if r.country in countries
    ]

    monthly_rows = [
        [countries.index(r.country), r.month, 1 if r.sensor_type == "monitor" else 0, int(r.n_locations)]
        for r in monthly.itertuples(index=False) if r.country in countries
    ]

    return {
        "generated": date.today().isoformat(),
        "countries": countries,
        "years": years,
        "extent": borders["extent"],
        "borders": borders["rings"],
        "locFields": loc_fields,
        "locs": loc_rows,
        "yearlyFields": ["c", "year", "monitor", "locations", "locations30", "sensors", "sensors30",
                         "locDays", "locDays75"],
        "yearly": yearly_rows,
        "monthlyFields": ["c", "month", "monitor", "locations"],
        "monthly": monthly_rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--coverage-dir", default=str(REPO_ROOT / "output" / "coverage"),
                        help="Directory with pm25_coverage.py outputs")
    parser.add_argument("--out", default=str(REPO_ROOT / "output" / "coverage" / "report" / "pm25_coverage_report.html"))
    args = parser.parse_args()

    data = build_data(Path(args.coverage_dir))
    # "</" inside a <script> block would end it early; JSON allows the escaped form
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")

    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise SystemExit(f"{TEMPLATE} must contain {PLACEHOLDER} exactly once")
    html = template.replace(PLACEHOLDER, payload)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html) / 1024:.0f} KB, {len(data['locs'])} locations)")


if __name__ == "__main__":
    main()
