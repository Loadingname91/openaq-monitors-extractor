#!/usr/bin/env python3
"""PM2.5 spatial and temporal coverage across all 5 countries.

Builds, from each country's daily measurements.csv:
  - pm25_location_days.csv: one row per location with PM2.5 sensors -- coordinates,
    reference monitor vs low-cost, first/last date, total days with data, and
    days with data per year.
  - pm25_yearly_counts.csv: per country x year x sensor type, active locations
    and sensors (>=1 day and >=30 days) and total location-days.
  - pm25_monthly_active.csv: per country x month x sensor type, locations with
    any PM2.5 data that month (shows network outages month by month).
  - borders.json: Natural Earth country outlines clipped to the map window, for
    the static maps and the HTML report (scripts/build_report.py).
  - figures/: maps of days-with-data, per-year small multiples, locations-per-year
    bars, and a days-per-location histogram.

A "day with data" is counted two ways:
  days_any -- the location reported any PM2.5 row that day
  days_75  -- at least one PM2.5 sensor at the location had coverage_percent >= 75
Days are distinct local dates across all PM2.5 sensors at a location, so a
location with two co-located sensors is not double counted.

Run on a compute node, not a login node -- India's measurements.csv is ~3M rows.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from openaq_extractor.config import load_spec  # noqa: E402

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAVE_CARTOPY = True
except ImportError:
    HAVE_CARTOPY = False

ALL_COUNTRIES = ["bhutan", "bangladesh", "nepal", "pakistan", "india"]
YEARS = list(range(2015, 2026))
VALID_COVERAGE = 75  # coverage_percent threshold for a "complete" day
MIN_DAYS_ACTIVE = 30  # days in a year for a location/sensor to count as meaningfully active
WINDOW_DAYS = (pd.Timestamp(f"{YEARS[-1]}-12-31") - pd.Timestamp(f"{YEARS[0]}-01-01")).days + 1
MAP_EXTENT = [60, 98, 5, 37]  # lon_min, lon_max, lat_min, lat_max
# Natural Earth 1:110m country polygons that ship with CHPC's ncvis install (no network needed)
NE_COUNTRIES_SHP = Path("/uufs/chpc.utah.edu/sys/installdir/r8/ncvis/08292022/resources/"
                        "ne_110m_admin_0_countries.shp")
# approximate country label positions on the static maps
COUNTRY_LABELS = {"PAKISTAN": (66.5, 29.5), "INDIA": (77.5, 21.0), "NEPAL": (81.5, 30.0),
                  "BHUTAN": (90.5, 28.4), "BANGLADESH": (91.5, 23.5)}

# Colors from the dataviz reference palette: categorical slots 1-2, blue sequential ramp
COLOR_MONITOR = "#2a78d6"
COLOR_LOWCOST = "#eb6834"
SEQ_CMAP = matplotlib.colors.LinearSegmentedColormap.from_list(
    "seq_blue", ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"])
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"


def _clip_ring(ring, extent):
    """Sutherland-Hodgman clip of one polygon ring to the [lon0, lon1, lat0, lat1] rectangle."""
    lon0, lon1, lat0, lat1 = extent
    edges = [
        (lambda p: p[0] >= lon0, lambda a, b: (lon0, a[1] + (b[1] - a[1]) * (lon0 - a[0]) / (b[0] - a[0]))),
        (lambda p: p[0] <= lon1, lambda a, b: (lon1, a[1] + (b[1] - a[1]) * (lon1 - a[0]) / (b[0] - a[0]))),
        (lambda p: p[1] >= lat0, lambda a, b: (a[0] + (b[0] - a[0]) * (lat0 - a[1]) / (b[1] - a[1]), lat0)),
        (lambda p: p[1] <= lat1, lambda a, b: (a[0] + (b[0] - a[0]) * (lat1 - a[1]) / (b[1] - a[1]), lat1)),
    ]
    for inside, cross in edges:
        if not ring:
            break
        out = []
        for i, cur in enumerate(ring):
            prev = ring[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        ring = out
    return ring


def load_borders(shp_path: Path = NE_COUNTRIES_SHP, extent=MAP_EXTENT):
    """Read polygon rings from an ESRI shapefile (shapetype 5) with plain struct, clipped to extent.

    pyshp/shapely aren't installed on CHPC's default python, and the format is simple enough
    to read directly. Returns a list of rings, each a list of (lon, lat) tuples.
    """
    if not shp_path.exists():
        return []
    data = shp_path.read_bytes()
    lon0, lon1, lat0, lat1 = extent
    rings = []
    offset = 100  # fixed-size file header
    while offset < len(data):
        _, content_words = struct.unpack(">ii", data[offset:offset + 8])
        rec = data[offset + 8:offset + 8 + content_words * 2]
        offset += 8 + content_words * 2
        shape_type, = struct.unpack("<i", rec[:4])
        if shape_type != 5:
            continue
        xmin, ymin, xmax, ymax = struct.unpack("<4d", rec[4:36])
        if xmax < lon0 or xmin > lon1 or ymax < lat0 or ymin > lat1:
            continue
        n_parts, n_points = struct.unpack("<ii", rec[36:44])
        parts = list(struct.unpack(f"<{n_parts}i", rec[44:44 + 4 * n_parts])) + [n_points]
        pts_off = 44 + 4 * n_parts
        points = struct.unpack(f"<{2 * n_points}d", rec[pts_off:pts_off + 16 * n_points])
        for a, b in zip(parts[:-1], parts[1:]):
            ring = [(points[2 * i], points[2 * i + 1]) for i in range(a, b)]
            clipped = _clip_ring(ring, extent)
            if len(clipped) >= 3:
                rings.append([(round(x, 3), round(y, 3)) for x, y in clipped])
    return rings


BORDERS = load_borders()


def output_dir_for(country: str, specs_dir: Path) -> Path:
    spec = load_spec(specs_dir / f"{country}.yaml")
    out_dir = Path(spec.output.directory)
    if not out_dir.is_absolute():
        out_dir = (specs_dir.parent / out_dir).resolve()
    return out_dir


def load_country(country: str, specs_dir: Path):
    """Return (location metadata, pm25 sensors, pm25 sensor-day rows) for one country."""
    out_dir = output_dir_for(country, specs_dir)
    locations = pd.read_csv(out_dir / "locations.csv")
    sensors = pd.read_csv(out_dir / "sensors.csv")
    sensors = sensors[sensors["parameter_name"] == "pm25"][["sensor_id", "location_id"]]

    chunks = []
    usecols = ["sensor_id", "location_id", "parameter_name", "datetime_from_local", "coverage_percent"]
    for chunk in pd.read_csv(out_dir / "measurements.csv", usecols=usecols, chunksize=500_000):
        chunk = chunk[chunk["parameter_name"] == "pm25"]
        chunks.append(pd.DataFrame({
            "sensor_id": chunk["sensor_id"],
            "location_id": chunk["location_id"],
            "date": pd.to_datetime(chunk["datetime_from_local"].str[:10]),
            "coverage_percent": chunk["coverage_percent"],
        }))
    rows = pd.concat(chunks, ignore_index=True)
    in_window = rows["date"].dt.year.isin(YEARS)
    n_outside = int((~in_window).sum())
    rows = rows[in_window]

    for df in (locations, sensors, rows):
        df["country"] = country
    print(f"[{country}] pm25 sensors={len(sensors)} pm25 sensor-days={len(rows)} "
          f"(dropped {n_outside} outside {YEARS[0]}-{YEARS[-1]})")
    return locations, sensors, rows


def build_tables(locations, sensors, rows):
    # one row per location-day; a day is "complete" if any sensor there hit the threshold
    loc_day = rows.groupby(["country", "location_id", "date"], as_index=False)["coverage_percent"].max()
    loc_day["year"] = loc_day["date"].dt.year
    loc_day["valid75"] = loc_day["coverage_percent"] >= VALID_COVERAGE

    per_loc = loc_day.groupby(["country", "location_id"]).agg(
        first_date=("date", "min"),
        last_date=("date", "max"),
        days_any=("date", "size"),
        days_75=("valid75", "sum"),
    )
    per_year = loc_day.pivot_table(index=["country", "location_id"], columns="year",
                                   values="date", aggfunc="size", fill_value=0)
    per_year = per_year.reindex(columns=YEARS, fill_value=0)
    per_year.columns = [f"days_{y}" for y in YEARS]

    n_sensors = sensors.groupby(["country", "location_id"]).size().rename("n_sensors")
    meta = locations.set_index(["country", "location_id"])[["name", "latitude", "longitude", "is_monitor"]]

    # every location with a PM2.5 sensor, including those with zero rows
    table = (n_sensors.to_frame()
             .join(meta, how="left")
             .join(per_loc, how="left")
             .join(per_year, how="left")
             .reset_index())
    day_cols = ["days_any", "days_75"] + list(per_year.columns)
    table[day_cols] = table[day_cols].fillna(0).astype(int)
    table["sensor_type"] = np.where(table["is_monitor"] == True, "monitor", "low-cost")  # noqa: E712
    table = table[["country", "location_id", "name", "latitude", "longitude", "is_monitor",
                   "sensor_type", "n_sensors", "first_date", "last_date"] + day_cols]

    # per-year counts at location and sensor level
    loc_year = loc_day.groupby(["country", "location_id", "year"]).agg(
        days_any=("date", "size"), days_75=("valid75", "sum")).reset_index()
    loc_year = loc_year.merge(table[["country", "location_id", "sensor_type"]], on=["country", "location_id"])

    sensor_year = rows.assign(year=rows["date"].dt.year).groupby(
        ["country", "location_id", "sensor_id", "year"])["date"].nunique().rename("days").reset_index()
    sensor_year = sensor_year.merge(table[["country", "location_id", "sensor_type"]], on=["country", "location_id"])

    keys = ["country", "year", "sensor_type"]
    yearly = loc_year.groupby(keys).agg(
        n_locations=("location_id", "size"),
        n_locations_30d=("days_any", lambda d: int((d >= MIN_DAYS_ACTIVE).sum())),
        location_days_any=("days_any", "sum"),
        location_days_75=("days_75", "sum"),
    ).join(sensor_year.groupby(keys).agg(
        n_sensors=("sensor_id", "size"),
        n_sensors_30d=("days", lambda d: int((d >= MIN_DAYS_ACTIVE).sum())),
    ))
    full_index = pd.MultiIndex.from_product(
        [ALL_COUNTRIES, YEARS, ["monitor", "low-cost"]], names=keys)
    yearly = yearly.reindex(full_index, fill_value=0).astype(int).reset_index()
    yearly = yearly[yearly["country"].isin(table["country"].unique())]

    loc_month = loc_day.assign(month=loc_day["date"].dt.strftime("%Y-%m")).drop_duplicates(
        ["country", "location_id", "month"])
    loc_month = loc_month.merge(table[["country", "location_id", "sensor_type"]], on=["country", "location_id"])
    monthly = (loc_month.groupby(["country", "month", "sensor_type"]).size().rename("n_locations")
               .reset_index().sort_values(["country", "month", "sensor_type"]))
    return table, yearly, monthly


# --- figures -----------------------------------------------------------------

def map_axes(fig, *subplot_args):
    if HAVE_CARTOPY:
        ax = fig.add_subplot(*subplot_args, projection=ccrs.PlateCarree())
        ax.set_extent(MAP_EXTENT, crs=ccrs.PlateCarree())
        ax.add_feature(cfeature.LAND, facecolor="#f6f6f4")
        ax.add_feature(cfeature.OCEAN, facecolor="#ffffff")
        ax.add_feature(cfeature.BORDERS, linewidth=0.5, edgecolor="#8a8984")
        ax.add_feature(cfeature.COASTLINE, linewidth=0.5, edgecolor="#8a8984")
        return ax, {"transform": ccrs.PlateCarree()}
    ax = fig.add_subplot(*subplot_args)
    ax.set_xlim(MAP_EXTENT[0], MAP_EXTENT[1])
    ax.set_ylim(MAP_EXTENT[2], MAP_EXTENT[3])
    ax.set_aspect("equal")
    ax.grid(color=GRID, linewidth=0.5)
    ax.set_xlabel("Longitude", color=TEXT_SECONDARY)
    ax.set_ylabel("Latitude", color=TEXT_SECONDARY)
    for ring in BORDERS:
        xs, ys = zip(*ring)
        ax.fill(xs, ys, facecolor="#f6f6f4", edgecolor="#a9a8a2", linewidth=0.5, zorder=0)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY)
    for label, (lon, lat) in COUNTRY_LABELS.items():
        ax.text(lon, lat, label, fontsize=7, color="#8a8984", ha="center", va="center", zorder=1)
    return ax, {}


def plot_days_map(table, col, title, path):
    fig = plt.figure(figsize=(9, 8))
    ax, tkw = map_axes(fig, 1, 1, 1)
    norm = LogNorm(vmin=1, vmax=WINDOW_DAYS)
    markers = {"monitor": "o", "low-cost": "^"}
    sc = None
    for stype, marker in markers.items():
        sub = table[table["sensor_type"] == stype]
        empty = sub[sub[col] == 0]
        has = sub[sub[col] > 0].sort_values(col)
        ax.scatter(empty["longitude"], empty["latitude"], marker=marker, s=22, facecolors="none",
                   edgecolors="#8a8984", linewidths=0.7, zorder=2, **tkw)
        sc = ax.scatter(has["longitude"], has["latitude"], c=has[col], cmap=SEQ_CMAP, norm=norm,
                        marker=marker, s=26, edgecolors="white", linewidths=0.4, zorder=3, **tkw)
    cbar = fig.colorbar(sc, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label(f"PM2.5 days with data, 2015–2025 ({col})", color=TEXT_SECONDARY)
    legend = [
        Line2D([], [], marker="o", linestyle="", color="#3987e5", label="Reference monitor"),
        Line2D([], [], marker="^", linestyle="", color="#3987e5", label="Low-cost sensor"),
        Line2D([], [], marker="o", linestyle="", markerfacecolor="none", markeredgecolor="#8a8984",
               label=f"No PM2.5 data {YEARS[0]}–{YEARS[-1]}"),
    ]
    ax.legend(handles=legend, loc="lower left", frameon=True, fontsize=8)
    n_loc = len(table)
    n_data = int((table[col] > 0).sum())
    ax.set_title(f"{title}\n{n_data} of {n_loc} PM2.5 locations have data", fontsize=11, loc="left")
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_map_by_year(table, path):
    fig = plt.figure(figsize=(16, 13))
    for i, year in enumerate(YEARS):
        ax, tkw = map_axes(fig, 3, 4, i + 1)
        col = f"days_{year}"
        counts = []
        for stype, color, marker in (("monitor", COLOR_MONITOR, "o"), ("low-cost", COLOR_LOWCOST, "^")):
            sub = table[(table["sensor_type"] == stype) & (table[col] >= MIN_DAYS_ACTIVE)]
            ax.scatter(sub["longitude"], sub["latitude"], s=7, c=color, marker=marker,
                       edgecolors="white", linewidths=0.2, zorder=3, **tkw)
            counts.append(len(sub))
        ax.set_title(f"{year}  ·  {counts[0]} monitors, {counts[1]} low-cost", fontsize=9, loc="left")
        if not HAVE_CARTOPY:
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.tick_params(labelsize=6)
    legend_ax = fig.add_subplot(3, 4, 12)
    legend_ax.axis("off")
    legend_ax.legend(handles=[
        Line2D([], [], marker="o", linestyle="", color=COLOR_MONITOR, label="Reference monitor"),
        Line2D([], [], marker="^", linestyle="", color=COLOR_LOWCOST, label="Low-cost sensor"),
    ], loc="center", title=f"Locations with ≥{MIN_DAYS_ACTIVE} PM2.5 days in the year", fontsize=10)
    fig.suptitle("PM2.5 locations active per year", fontsize=14, x=0.05, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def plot_locations_per_year(yearly, path):
    countries = [c for c in ALL_COUNTRIES[::-1] if c in set(yearly["country"])]
    fig, axes = plt.subplots(1, len(countries), figsize=(4 * len(countries), 4), squeeze=False)
    for ax, country in zip(axes[0], countries):
        sub = yearly[yearly["country"] == country].pivot(index="year", columns="sensor_type",
                                                         values="n_locations_30d").reindex(YEARS, fill_value=0)
        mon = sub.get("monitor", pd.Series(0, index=YEARS))
        low = sub.get("low-cost", pd.Series(0, index=YEARS))
        ax.bar(YEARS, mon, color=COLOR_MONITOR, width=0.8, edgecolor="white", linewidth=1, label="Reference monitor")
        ax.bar(YEARS, low, bottom=mon, color=COLOR_LOWCOST, width=0.8, edgecolor="white", linewidth=1,
               label="Low-cost sensor")
        total = mon + low
        for x, t in zip(YEARS, total):
            if t > 0:
                ax.text(x, t, str(int(t)), ha="center", va="bottom", fontsize=7, color=TEXT_SECONDARY)
        if total.max() == 0:
            ax.set_ylim(0, 1)
            ax.text(0.5, 0.5, f"No location reached\n{MIN_DAYS_ACTIVE} days in any year", transform=ax.transAxes,
                    ha="center", va="center", fontsize=9, color=TEXT_SECONDARY)
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
        ax.set_title(country.capitalize(), loc="left", fontsize=11)
        ax.set_xticks(YEARS)
        ax.set_xticklabels([str(y)[2:] for y in YEARS], fontsize=8, color=TEXT_SECONDARY)
        ax.tick_params(axis="y", labelsize=8, colors=TEXT_SECONDARY)
        ax.grid(axis="y", color=GRID, linewidth=0.5)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[0][0].set_ylabel(f"Locations with ≥{MIN_DAYS_ACTIVE} PM2.5 days", color=TEXT_SECONDARY)
    axes[0][0].legend(fontsize=8, frameon=False, loc="upper left")
    fig.suptitle("Active PM2.5 locations per year (independent y-axes per country)", fontsize=12, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_days_hist(table, path):
    fig, ax = plt.subplots(figsize=(8, 4))
    bins = np.arange(0, WINDOW_DAYS + 182, 182)  # ~half-year bins over the 11-year window
    for stype, color in (("monitor", COLOR_MONITOR), ("low-cost", COLOR_LOWCOST)):
        sub = table[table["sensor_type"] == stype]["days_any"]
        ax.hist(sub, bins=bins, color=color, alpha=0.75, edgecolor="white", linewidth=1,
                label=f"{'Reference monitor' if stype == 'monitor' else 'Low-cost sensor'} (n={len(sub)})")
    ax.set_xlabel("PM2.5 days with data per location, 2015–2025", color=TEXT_SECONDARY)
    ax.set_ylabel("Locations", color=TEXT_SECONDARY)
    ax.tick_params(colors=TEXT_SECONDARY)
    ax.grid(axis="y", color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False)
    ax.set_title("Record length per location", loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--country", nargs="+", default=ALL_COUNTRIES, choices=ALL_COUNTRIES,
                        help="Countries to include (default: all 5)")
    parser.add_argument("--specs-dir", default=str(REPO_ROOT / "specs"),
                        help="Directory containing <country>.yaml spec files")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "output" / "coverage"),
                        help="Where to write tables and figures")
    args = parser.parse_args()

    specs_dir = Path(args.specs_dir)
    out_dir = Path(args.out_dir)
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    loaded = [load_country(c, specs_dir) for c in args.country]
    locations = pd.concat([x[0] for x in loaded], ignore_index=True)
    sensors = pd.concat([x[1] for x in loaded], ignore_index=True)
    rows = pd.concat([x[2] for x in loaded], ignore_index=True)

    table, yearly, monthly = build_tables(locations, sensors, rows)
    table.to_csv(out_dir / "pm25_location_days.csv", index=False)
    yearly.to_csv(out_dir / "pm25_yearly_counts.csv", index=False)
    monthly.to_csv(out_dir / "pm25_monthly_active.csv", index=False)
    (out_dir / "borders.json").write_text(json.dumps({"extent": MAP_EXTENT, "rings": BORDERS}))
    print(f"wrote {len(table)} locations -> {out_dir / 'pm25_location_days.csv'}")
    print(f"wrote {len(yearly)} rows -> {out_dir / 'pm25_yearly_counts.csv'}")

    if not HAVE_CARTOPY and not BORDERS:
        print(f"no cartopy and no shapefile at {NE_COUNTRIES_SHP}: static maps have no borders", file=sys.stderr)
    plot_days_map(table, "days_any", "PM2.5 days with data per location (any reporting)",
                  fig_dir / "pm25_map_days.png")
    plot_days_map(table, "days_75", f"PM2.5 days with ≥{VALID_COVERAGE}% coverage per location",
                  fig_dir / "pm25_map_days_75.png")
    plot_map_by_year(table, fig_dir / "pm25_map_by_year.png")
    plot_locations_per_year(yearly, fig_dir / "pm25_sensors_per_year.png")
    plot_days_hist(table, fig_dir / "pm25_location_days_hist.png")
    print(f"figures -> {fig_dir}")


if __name__ == "__main__":
    main()
