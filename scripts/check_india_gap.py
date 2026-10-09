#!/usr/bin/env python3
"""Check whether India's Nov 2022 - Jan 2025 PM2.5 gap is in OpenAQ or in our extraction.

Our daily extraction has ~319 active India PM2.5 locations in Oct 2022, 3 in Nov 2022 and
~510 again in Feb 2025. Most affected locations carry two PM2.5 sensors in OpenAQ: an old
one whose last reading is 2022-10-31 and a new one (id ~12.23M) starting in 2025. So the
check works per location, across every PM2.5 sensor OpenAQ lists there:

  meta    each sensor's datetime_first / datetime_last as OpenAQ reports them
  daily   each sensor's full daily history (the pipeline's request). OpenAQ's daily
          endpoint ignores datetime_from/datetime_to and returns everything, so days are
          filtered to the gap window here, client side.
  hourly  the pre-gap sensor, 2023-06-01..2023-06-08 (this endpoint does honor dates)
  raw     the pre-gap sensor, 2023-06-01..2023-06-03

Verdict per location:

  EXTRACTION MISSED IT   OpenAQ has daily data in the gap that our CSV lacks -> re-fetch
  NO GAP                 both OpenAQ and our CSV have gap-period data (expected for the control)
  DAILY ROLLUP MISSING   no daily data in the gap, but hourly/raw exist -> fetch hourly
  OPENAQ ARCHIVE GAP     nothing at any resolution -> OpenAQ never received it; use CPCB CCR

One control location that kept reporting through 2023 is added automatically; if it does
not come back NO GAP, don't trust the other verdicts. 2022 day counts are compared too,
since affected locations have at most ~192 days in 2022 in our data.

Needs network access and an OpenAQ API key (same as the extractor: OPENAQ_API_KEY env
var, .env file, or ~/.config/openaq-extractor/config.yaml). About 8-10 API calls per
location, 1-3 minutes for the default 5 + control. Run it on a login node:

    python3 scripts/check_india_gap.py                    # 5 gap locations + 1 control
    python3 scripts/check_india_gap.py --n 10
    python3 scripts/check_india_gap.py --location-ids 7044 6931
    python3 scripts/check_india_gap.py --dry-run          # show what would be checked

Errors on a single request (e.g. OpenAQ's HTTP 408 "smaller time frame" timeout) are
recorded and the run continues. Results go to output/coverage/india_gap_check.csv.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from openaq_extractor.config import resolve_api_key  # noqa: E402
from openaq_extractor.rate_limiter import api_call  # noqa: E402

PIPELINE_FROM, PIPELINE_TO = "2015-01-01", "2025-12-31"
GAP_FROM, GAP_TO = "2022-11-01", "2025-01-31"
PAGE_LIMIT = 1000
MAX_PAGES = 20
HOURLY = dict(data="hours", datetime_from="2023-06-01", datetime_to="2023-06-08")
RAW = dict(data=None, datetime_from="2023-06-01", datetime_to="2023-06-03")


def pick_locations(n: int, location_ids: list[int] | None) -> pd.DataFrame:
    """Gap-affected India PM2.5 monitor locations (data in 2022, none in 2023-24), plus a control.

    Takes up to 2 Delhi DPCC sites, then one site per other state board, for spread.
    """
    locs = pd.read_csv(REPO_ROOT / "output" / "coverage" / "pm25_location_days.csv")
    locs = locs[locs["country"] == "india"]
    cols = ["location_id", "name", "sensor_type", "days_2022", "days_2023", "days_2024"]
    if location_ids:
        picked = locs[locs["location_id"].isin(location_ids)][cols]
        missing = set(location_ids) - set(picked["location_id"])
        if missing:
            print(f"warning: not India PM2.5 locations: {sorted(missing)}", file=sys.stderr)
        return picked.assign(role="requested")
    mon = locs[locs["sensor_type"] == "monitor"][cols].copy()
    gap = mon[(mon["days_2022"] >= 30) & (mon["days_2023"] == 0) & (mon["days_2024"] == 0)].copy()
    gap["is_dpcc"] = gap["name"].fillna("").str.contains("DPCC")
    gap["board"] = gap["name"].fillna("").str.extract(r"- (\S+)$", expand=False).fillna("")
    gap = gap.sort_values("days_2022", ascending=False)
    gap = pd.concat([gap[gap["is_dpcc"]].head(2),
                     gap[~gap["is_dpcc"]].drop_duplicates("board"),
                     gap[~gap["is_dpcc"]]]).drop_duplicates("location_id")
    control = mon[mon["days_2023"] > 0].sort_values("days_2023", ascending=False).head(1)
    return pd.concat([gap.head(n).assign(role="gap"), control.assign(role="control")],
                     ignore_index=True)[cols + ["role"]]


def pm25_sensors_at(location_id: int) -> list[int]:
    sensors = pd.read_csv(REPO_ROOT / "output" / "india" / "sensors.csv")
    s = sensors[(sensors["parameter_name"] == "pm25") & (sensors["location_id"] == location_id)]
    return sorted(int(x) for x in s["sensor_id"])


def safe(label: str, fn, errors: list):
    """Run one API request (with the pipeline's retry/backoff); record any failure and go on."""
    try:
        return api_call(fn, context=label)
    except Exception as e:  # noqa: BLE001 -- e.g. openaq TimeoutError (HTTP 408) isn't retryable
        msg = f"{label}: {type(e).__name__}: {str(e)[:160]}"
        errors.append(msg)
        print(f"        ! {msg}")
        return None


def fetch_all(client, sensor_id: int, errors: list, **kw):
    """All pages of a measurements query. Returns (rows, page_sizes, found) or None on error."""
    rows, pages, found = [], [], None
    for page in range(1, MAX_PAGES + 1):
        resp = safe(f"sensor {sensor_id} {kw.get('data') or 'raw'} page {page}",
                    lambda pg=page: client.measurements.list(sensors_id=sensor_id, page=pg, limit=PAGE_LIMIT, **kw),
                    errors)
        if resp is None:
            return None
        if found is None:
            found = getattr(getattr(resp, "meta", None), "found", None)
        pages.append(len(resp.results))
        rows.extend(resp.results)
        if len(resp.results) < PAGE_LIMIT:
            break
    return rows, pages, found


def utc_date(m) -> str:
    period = getattr(m, "period", None)
    dt = getattr(period, "datetime_from", None) if period else None
    return (getattr(dt, "utc", None) or "")[:10]


def in_range(dates, lo: str, hi: str) -> list[str]:
    return [d for d in dates if lo <= d <= hi]


def check_location(client, loc) -> dict:
    lid = int(loc["location_id"])
    errors: list[str] = []
    print(f"\nlocation {lid}  {loc['name']}  [{loc['role']}]")
    r = {"location_id": lid, "name": loc["name"], "role": loc["role"],
         "ours_days_2022": int(loc["days_2022"]), "ours_days_2023": int(loc["days_2023"]),
         "ours_days_2024": int(loc["days_2024"])}

    sensor_ids = pm25_sensors_at(lid)
    all_dates: set[str] = set()
    spans = []  # (sensor_id, first, last)
    for sid in sensor_ids:
        meta = safe(f"sensors.get {sid}", lambda s=sid: client.sensors.get(s), errors)
        first = last = None
        if meta is not None and meta.results:
            s = meta.results[0]
            first = (getattr(getattr(s, "datetime_first", None), "utc", None) or "")[:10] or None
            last = (getattr(getattr(s, "datetime_last", None), "utc", None) or "")[:10] or None
        spans.append((sid, first, last))

        got = fetch_all(client, sid, errors, data="days", datetime_from=PIPELINE_FROM, datetime_to=PIPELINE_TO)
        if got is None:
            print(f"  sensor {sid}: OpenAQ active {first} -> {last}; daily history failed")
            continue
        rows, pages, found = got
        dates = sorted({utc_date(m) for m in rows if utc_date(m)})
        all_dates.update(dates)
        by_year = Counter(d[:4] for d in dates)
        print(f"  sensor {sid}: OpenAQ active {first} -> {last}")
        print(f"        daily history: {len(rows)} rows (found={found}), pages={pages}, "
              f"by year {dict(sorted(by_year.items()))}")

    gap_days = in_range(sorted(all_dates), GAP_FROM, GAP_TO)
    r["sensors"] = " ".join(f"{sid}[{f or '?'}..{l or '?'}]" for sid, f, l in spans)
    r["api_gap_days"] = len(gap_days)
    r["api_days_2022"] = len(in_range(all_dates, "2022-01-01", "2022-12-31"))
    r["api_last_before_gap"] = max((d for d in all_dates if d < GAP_FROM), default="")
    r["api_first_after_gap"] = min((d for d in all_dates if d > GAP_TO), default="")
    r["meta_spans_gap"] = any(f and l and f <= GAP_TO and l >= GAP_FROM for _, f, l in spans)

    # hourly / raw for the sensor that was reporting before the gap (earliest first reading)
    pre = sorted((s for s in spans if s[1]), key=lambda s: s[1])
    probe = pre[0][0] if pre else (sensor_ids[0] if sensor_ids else None)
    for key, kw in (("hourly_2023", HOURLY), ("raw_2023", RAW)):
        r[key] = None
        if probe is None:
            continue
        got = fetch_all(client, probe, errors, **kw)
        if got is not None:
            dates = [utc_date(m) for m in got[0]]
            r[key] = len(in_range(dates, kw["datetime_from"], kw["datetime_to"]))
            print(f"  sensor {probe}: {key.replace('_', ' ')} {kw['datetime_from']}..{kw['datetime_to']}: "
                  f"{r[key]} rows in window ({len(got[0])} returned)")

    r["errors"] = " | ".join(errors)
    r["verdict"] = verdict(r)
    print(f"  ours: location days 2022={r['ours_days_2022']} 2023={r['ours_days_2023']} 2024={r['ours_days_2024']}"
          f"   OpenAQ: gap-window days={r['api_gap_days']}, 2022 days={r['api_days_2022']}, "
          f"last before gap={r['api_last_before_gap'] or '-'}, first after={r['api_first_after_gap'] or '-'}")
    if r["api_days_2022"] > r["ours_days_2022"] + 30:
        print(f"  note: OpenAQ has {r['api_days_2022']} days in 2022 vs our {r['ours_days_2022']}")
    print(f"  ==> {r['verdict']}")
    return r


def verdict(r: dict) -> str:
    ours_gap = r["ours_days_2023"] + r["ours_days_2024"] > 0
    if r["api_gap_days"] > 0:
        return "NO GAP" if ours_gap else "EXTRACTION MISSED IT (re-fetch India for the gap)"
    if (r.get("hourly_2023") or 0) > 0 or (r.get("raw_2023") or 0) > 0:
        return "DAILY ROLLUP MISSING (hourly/raw exist: fetch hourly and aggregate)"
    incomplete = r["errors"] and (r.get("hourly_2023") is None or r.get("raw_2023") is None)
    why = ("OpenAQ lists no PM2.5 sensor here active during the gap" if not r["meta_spans_gap"]
           else "sensor metadata spans the gap but no data was returned")
    return f"OPENAQ ARCHIVE GAP ({why}){' -- some requests failed, see errors' if incomplete else ''}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=5, help="Number of gap locations to auto-pick (default 5)")
    parser.add_argument("--location-ids", type=int, nargs="+", help="Check these India location ids instead")
    parser.add_argument("--api-key", help="OpenAQ API key (default: env / .env / config file)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be checked, no API calls")
    parser.add_argument("--out", default=str(REPO_ROOT / "output" / "coverage" / "india_gap_check.csv"))
    args = parser.parse_args()

    picked = pick_locations(args.n, args.location_ids)
    if picked.empty:
        raise SystemExit("no locations selected")
    picked["pm25_sensors"] = [" ".join(map(str, pm25_sensors_at(int(l)))) for l in picked["location_id"]]
    print(f"Checking {len(picked)} India location(s) for the {GAP_FROM}..{GAP_TO} PM2.5 gap:")
    print(picked[["role", "location_id", "name", "pm25_sensors", "days_2022", "days_2023", "days_2024"]]
          .to_string(index=False))
    if args.dry_run:
        return

    from openaq import OpenAQ
    client = OpenAQ(api_key=resolve_api_key(cli_api_key=args.api_key))
    results = [check_location(client, loc) for _, loc in picked.iterrows()]

    out = pd.DataFrame(results)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print("\nSummary (gap locations)")
    for v, k in Counter(out.loc[out["role"] != "control", "verdict"]).most_common():
        print(f"  {k} x {v}")
    ctrl = out[out["role"] == "control"]
    if not ctrl.empty:
        ok = (ctrl["verdict"] == "NO GAP").all()
        print("Control location: " + ("OK, came back NO GAP" if ok else
              f"UNEXPECTED ({ctrl['verdict'].iloc[0]}) -- check key/network before trusting the rest"))
    thin = out[out["api_days_2022"] > out["ours_days_2022"] + 30]
    print(f"2022 completeness: {len(thin)} of {len(out)} locations have >30 more 2022 days in OpenAQ than in our CSV")
    print(f"Details: {args.out}")


if __name__ == "__main__":
    main()
