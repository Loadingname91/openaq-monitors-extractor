#!/usr/bin/env python3
"""Per-country, per-sensor coverage for every extracted pollutant -> one JSON per country.

Feeds scripts/build_country_reports.py, which turns each JSON into a country report page.
For each country, from its daily measurements.csv:
  - per sensor and year: days recorded, the sensor's active span in that year (first to last
    report, clipped to the year), and the longest run of missing days inside the span.
    Computed twice: any data that day, and >=75% hourly coverage that day.
  - every gap of >= MIN_GAP missing days inside a sensor's span (for the outage line chart,
    which picks its threshold in the browser).
  - per pollutant and day, the number of sensors reporting.
  - per location, pollutant and year, distinct days with data across co-located sensors (map color).
  - Natural Earth borders around the country, with the country's own rings flagged.

Window: YEARS (2015-2026). 2026 is outside the project's 2015-2025 scope and partial; the report
labels it that way. Run on a compute node (scripts/country_coverage.slurm).
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pm25_coverage import ALL_COUNTRIES, VALID_COVERAGE, load_borders, output_dir_for  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
YEARS = list(range(2015, 2027))
MIN_GAP = 3  # smallest outage threshold offered in the report
PARAM_ORDER = ["pm25", "pm10", "no2", "so2", "co", "o3", "pm1", "um003"]
PARAM_LABELS = {"pm25": "PM2.5", "pm10": "PM10", "no2": "NO₂", "so2": "SO₂", "co": "CO", "o3": "O₃",
                "pm1": "PM1", "um003": "UM003 (particle count)"}
BORDER_EXTENT = [55, 106, 0, 42]  # wide enough for every country's neighbours
# a point inside each country's 1:110m outline; rings containing it are drawn as the focus country
INSIDE_POINT = {"india": (78.5, 22.0), "pakistan": (69.0, 29.5), "nepal": (84.0, 28.2),
                "bangladesh": (90.3, 23.8), "bhutan": (90.4, 27.4)}


def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def gaps_in_span(present, first, last):
    """Runs of absent days inside [first, last], as (start, length) arrays. `present` is sorted unique."""
    edges = np.concatenate(([first - 1], present, [last + 1]))
    lens = np.diff(edges) - 1
    keep = lens > 0
    return edges[:-1][keep] + 1, lens[keep]


def year_stats(present, gstart, glen, first, last, year_bounds):
    """Per year: [recorded, span days, longest gap inside the year] for years the span touches."""
    out = {}
    for yi, (y0, y1) in enumerate(year_bounds):
        s0, s1 = max(first, y0), min(last, y1)
        if s0 > s1:
            continue
        rec = int(np.count_nonzero((present >= y0) & (present <= y1)))
        g0, g1 = np.maximum(gstart, y0), np.minimum(gstart + glen - 1, y1)
        ov = g1 - g0 + 1
        out[yi] = [rec, int(s1 - s0 + 1), int(ov.max()) if (ov > 0).any() else 0]
    return out


def load_rows(out_dir: Path, day0: pd.Timestamp, day_end: pd.Timestamp):
    usecols = ["sensor_id", "location_id", "parameter_name", "datetime_from_local", "coverage_percent"]
    chunks = []
    for chunk in pd.read_csv(out_dir / "measurements.csv", usecols=usecols, chunksize=500_000):
        chunk = chunk[chunk["parameter_name"].isin(PARAM_ORDER)]
        d = pd.to_datetime(chunk["datetime_from_local"].str[:10])
        chunks.append(pd.DataFrame({
            "sensor_id": chunk["sensor_id"].to_numpy(),
            "location_id": chunk["location_id"].to_numpy(),
            "param": chunk["parameter_name"].to_numpy(),
            "day": ((d - day0).dt.days).to_numpy(),
            "cov": chunk["coverage_percent"].fillna(0).to_numpy(),
        }))
    rows = pd.concat(chunks, ignore_index=True)
    n_end = (day_end - day0).days
    rows = rows[(rows["day"] >= 0) & (rows["day"] <= n_end)]
    # one row per sensor-day; a day is complete if its best row reached the threshold
    return rows.groupby(["sensor_id", "location_id", "param", "day"], as_index=False)["cov"].max()


def build_country(country: str, specs_dir: Path, day0, day_end, rings):
    out_dir = output_dir_for(country, specs_dir)
    locations = pd.read_csv(out_dir / "locations.csv")
    sensors = pd.read_csv(out_dir / "sensors.csv")
    sensors = sensors[sensors["parameter_name"].isin(PARAM_ORDER)]
    rows = load_rows(out_dir, day0, day_end)
    n_days = (day_end - day0).days + 1

    params = [p for p in PARAM_ORDER if p in set(sensors["parameter_name"])]
    pidx = {p: i for i, p in enumerate(params)}
    first_day = int(rows["day"].min()) if len(rows) else 0
    first_year = (day0 + pd.Timedelta(days=first_day)).year
    data_years = [y for y in YEARS if y >= first_year]
    year_bounds = [((pd.Timestamp(f"{y}-01-01") - day0).days,
                    min((pd.Timestamp(f"{y}-12-31") - day0).days, n_days - 1)) for y in data_years]

    loc_ids = sorted(set(sensors["location_id"]))
    lidx = {l: i for i, l in enumerate(loc_ids)}
    meta = locations.set_index("location_id")
    loc_rows = []
    for l in loc_ids:
        m = meta.loc[l] if l in meta.index else None
        loc_rows.append([
            int(l),
            "" if m is None or pd.isna(m["name"]) else str(m["name"]).strip(),
            None if m is None else round(float(m["latitude"]), 4),
            None if m is None else round(float(m["longitude"]), 4),
            1 if m is not None and m["is_monitor"] == True else 0,  # noqa: E712
        ])

    # daily reporting counts per pollutant: [any, >=75]
    daily = {}
    for p in params:
        sub = rows[rows["param"] == p]
        daily[p] = [np.bincount(sub["day"], minlength=n_days).tolist(),
                    np.bincount(sub.loc[sub["cov"] >= VALID_COVERAGE, "day"], minlength=n_days).tolist()]

    # per location x pollutant x year, distinct days across co-located sensors
    ld = rows.groupby(["location_id", "param", "day"])["cov"].max().reset_index()
    ld["yi"] = np.searchsorted([b[0] for b in year_bounds], ld["day"], side="right") - 1
    ld["c75"] = ld["cov"] >= VALID_COVERAGE
    loc_year, loc_span = {}, {}  # station totals, same layout as a sensor's byYear

    # outages per location x pollutant: OpenAQ often retires a sensor id and starts a new one at the
    # same station after a break (India's CPCB network, 2022-2025), which a per-sensor gap can't see
    loc_daily, loc_gaps = {}, [[], []]
    for p in params:
        sub = ld[ld["param"] == p]
        loc_daily[p] = [np.bincount(sub["day"], minlength=n_days).tolist(),
                        np.bincount(sub.loc[sub["c75"], "day"], minlength=n_days).tolist()]
    for (l, p), g in ld.groupby(["location_id", "param"]):
        any_days = np.sort(g["day"].to_numpy())
        first, last = int(any_days[0]), int(any_days[-1])
        key = f"{lidx[l]}:{pidx[p]}"
        loc_span[key] = [first, last]
        by_year = loc_year.setdefault(key, {})
        for m, present in enumerate((any_days, np.sort(g.loc[g["c75"], "day"].to_numpy()))):
            gs, gl = gaps_in_span(present, first, last)
            for yi, v in year_stats(present, gs, gl, first, last, year_bounds).items():
                row = by_year.setdefault(yi, [0] * 5)
                row[m], row[3 + m] = v[0], v[2]
                row[2] = v[1]
            big = gl >= MIN_GAP
            loc_gaps[m].extend([lidx[l], pidx[p], int(a), int(b)] for a, b in zip(gs[big], gl[big]))

    # per sensor
    grouped = {k: g for k, g in rows.groupby("sensor_id")}
    sensor_rows, gaps = [], [[], []]
    for s in sensors.sort_values(["location_id", "parameter_name", "sensor_id"]).itertuples(index=False):
        g = grouped.get(s.sensor_id)
        base = [int(s.sensor_id), lidx[s.location_id], pidx[s.parameter_name]]
        if g is None or g.empty:
            sensor_rows.append(base + [None, None, {}])
            continue
        any_days = np.sort(g["day"].to_numpy())
        d75 = np.sort(g.loc[g["cov"] >= VALID_COVERAGE, "day"].to_numpy())
        first, last = int(any_days[0]), int(any_days[-1])
        by_year = {}
        si = len(sensor_rows)
        for m, present in enumerate((any_days, d75)):
            gs, gl = gaps_in_span(present, first, last)
            for yi, v in year_stats(present, gs, gl, first, last, year_bounds).items():
                by_year.setdefault(yi, [0] * 5)
                if m == 0:
                    by_year[yi][0], by_year[yi][2], by_year[yi][3] = v[0], v[1], v[2]
                else:
                    by_year[yi][1], by_year[yi][4] = v[0], v[2]
            big = gl >= MIN_GAP
            gaps[m].extend([si, int(a), int(b)] for a, b in zip(gs[big], gl[big]))
        sensor_rows.append(base + [first, last, {str(k): v for k, v in by_year.items()}])

    pt = INSIDE_POINT[country]
    focus = [i for i, r in enumerate(rings) if point_in_ring(pt, r)]
    # box around the country outline plus every location (some sit just outside the coarse outline)
    fx = [p[0] for i in focus for p in rings[i]] + [r[3] for r in loc_rows if r[3] is not None]
    fy = [p[1] for i in focus for p in rings[i]] + [r[2] for r in loc_rows if r[2] is not None]

    print(f"[{country}] params={params} locations={len(loc_rows)} sensors={len(sensor_rows)} "
          f"sensor-days={len(rows)} gaps(any)={len(gaps[0])} gaps(75)={len(gaps[1])}")
    return {
        "country": country,
        "generated": date.today().isoformat(),
        "day0": day0.strftime("%Y-%m-%d"),
        "nDays": n_days,
        "dataEnd": day_end.strftime("%Y-%m-%d"),
        "years": data_years,
        "scopeEndYear": 2025,
        "yearStart": [b[0] for b in year_bounds],
        "minGap": MIN_GAP,
        "validCoverage": VALID_COVERAGE,
        "params": params,
        "paramLabels": [PARAM_LABELS[p] for p in params],
        "locFields": ["id", "name", "lat", "lon", "monitor"],
        "locs": loc_rows,
        "sensorFields": ["id", "loc", "param", "first", "last",
                         "byYear{yearIdx: [daysAny, days75, spanDays, longestGapAny, longestGap75]}"],
        "sensors": sensor_rows,
        "gapFields": ["sensorIdx", "startDay", "length"],
        "gaps": {"any": [x for g in gaps[0] for x in g], "v75": [x for g in gaps[1] for x in g]},
        "daily": daily,
        "locYearFields": "{'loc:param': {yearIdx: [daysAny, days75, spanDays, longestGapAny, longestGap75]}}",
        "locYear": loc_year,
        "locSpan": loc_span,
        "locGapFields": ["locIdx", "paramIdx", "startDay", "length"],
        "locGaps": {"any": [x for g in loc_gaps[0] for x in g], "v75": [x for g in loc_gaps[1] for x in g]},
        "locDaily": loc_daily,
        "borders": rings,
        "focus": focus,
        "focusBox": [min(fx), max(fx), min(fy), max(fy)] if fx else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--country", nargs="+", default=ALL_COUNTRIES, choices=ALL_COUNTRIES)
    parser.add_argument("--specs-dir", default=str(REPO_ROOT / "specs"))
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "output" / "coverage" / "country"))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day0 = pd.Timestamp(f"{YEARS[0]}-01-01")
    rings = [[list(p) for p in r] for r in load_borders(extent=BORDER_EXTENT)]

    # one shared end date (the latest day any country reports) so partial-2026 is cut the same everywhere
    ends = []
    for c in args.country:
        tail = pd.read_csv(output_dir_for(c, Path(args.specs_dir)) / "measurements.csv",
                           usecols=["datetime_from_local"])["datetime_from_local"].str[:10].max()
        ends.append(pd.Timestamp(tail))
    day_end = min(max(ends), pd.Timestamp(f"{YEARS[-1]}-12-31"))
    print(f"window {day0.date()} .. {day_end.date()}")

    for c in args.country:
        data = build_country(c, Path(args.specs_dir), day0, day_end, rings)
        path = out_dir / f"{c}.json"
        path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
