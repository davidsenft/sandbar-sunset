#!/usr/bin/env python3
"""
derive_sandbar.py — rebuild the Wingaersheek sandbar terrain products from source.

This is the method behind the two numbers the whole almanac rests on: the height of
the saddle joining the bar to the beach, and how much bar is bare at a given tide.

Source data
-----------
NOAA/NCEI Continuously Updated Digital Elevation Model (CUDEM), 1/9 arc-second
(~3 m), tile ncei19_n42x75_w070x75_2021v1, served from the NCEI ArcGIS ImageServer.
It is referenced to **NAVD88 in metres**; the almanac works in **feet above MLLW**
(the datum NOAA tide predictions use), so every elevation is converted on load.

The conversion offset comes from VDatum at the beach: 0.0 m NAVD88 = +1.561 m MLLW
(reported uncertainty +/-0.117 m, i.e. ~0.4 ft — the dominant error term in all of
this). `verify_datum()` re-checks it against tide-station 8441571's published datums
and will complain if the two disagree by more than a tenth of a foot.

Outputs
-------
  dem_mllw_ft.npy      full raster, ft above MLLW
  bar_crop.npy         the spit and flats north-west of the beach
  bottleneck.npy       highest "minimum elevation along a path" from permanent land
  bar_dry_table.json   bare area of the bar vs water level      <-- build input
  exposure_curve.json  reachable area + walk-out distance vs water level
  wingaersheek_map.png / bar_zoom.png / bottleneck_map.png   visual checks

Usage
-----
  python3 derive_sandbar.py              # reuse a cached DEM if present
  python3 derive_sandbar.py --refetch    # re-download the DEM
  python3 derive_sandbar.py --outdir /tmp/check   # write somewhere else

Needs numpy and Pillow. The DEM fetch needs network; everything after it is offline.
"""

import argparse, io, json, heapq, math, os, sys, urllib.parse, urllib.request
import numpy as np
from PIL import Image

# ---------------------------------------------------------------- constants

IMAGESERVER = ("https://gis.ngdc.noaa.gov/arcgis/rest/services/"
               "DEM_mosaics/DEM_all/ImageServer")
VDATUM_API = "https://vdatum.noaa.gov/vdatumweb/api/convert"
COOPS_DATUMS = ("https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/"
                "stations/8441571/datums.json")

BEACH_LAT, BEACH_LON = 42.6501, -70.6842      # Wingaersheek Beach
NAVD88_TO_MLLW_M = 1.561                      # VDatum, at the beach
M_TO_FT = 3.28084

# full raster window, and the pixel grid requested from the ImageServer
BBOX = (-70.6960, 42.6425, -70.6700, 42.6660)  # W, S, E, N
GRID_W, GRID_H = 720, 800

# the spit and flats north-west of the beach
CROP = dict(lon0=-70.6935, lon1=-70.6820, lat0=42.6510, lat1=42.6625)

ALWAYS_DRY_FT = 9.2      # ~MHW above MLLW; ground above this is permanent land
BAR_SOUTH_LAT = 42.6548  # everything seaward (north) of the beach berm
BAR_MIN_FT, BAR_MAX_FT = -4.0, 8.0

SQ_M_PER_ACRE = 4046.86


# ---------------------------------------------------------------- datum check

def _get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "sandbar-sunset/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def verify_datum():
    """Cross-check the NAVD88->MLLW offset two independent ways.

    VDatum gives MLLW and MHW relative to NAVD88; the tide station publishes MHW
    relative to MLLW directly. The implied MLLW->MHW separation should agree.
    """
    def vdatum(target):
        q = urllib.parse.urlencode({
            "s_x": BEACH_LON, "s_y": BEACH_LAT, "s_z": 0.0, "region": "contiguous",
            "s_h_frame": "NAD83_2011", "s_coor": "geo", "s_v_frame": "NAVD88",
            "s_v_unit": "m", "t_h_frame": "NAD83_2011", "t_coor": "geo",
            "t_v_frame": target, "t_v_unit": "m"})
        return float(_get_json(f"{VDATUM_API}?{q}")["t_z"])

    mllw, mhw = vdatum("MLLW"), vdatum("MHW")
    vd_range_ft = (mllw + mhw) * M_TO_FT * -1 if mhw > 0 else (mllw - mhw) * M_TO_FT

    d = {x["name"]: float(x["value"]) for x in _get_json(COOPS_DATUMS)["datums"]}
    station_range_ft = d["MHW"] - d["MLLW"]

    print(f"  VDatum:   0 m NAVD88 = {mllw:+.3f} m MLLW, {mhw:+.3f} m MHW")
    print(f"  MLLW->MHW separation: VDatum {vd_range_ft:.2f} ft"
          f"  |  station 8441571 {station_range_ft:.2f} ft")
    if abs(vd_range_ft - station_range_ft) > 0.1:
        print("  WARNING: datum sources disagree by more than 0.1 ft", file=sys.stderr)
    else:
        print("  datum check OK")
    if abs(mllw - NAVD88_TO_MLLW_M) > 0.01:
        print(f"  WARNING: VDatum now reports {mllw:.3f} m, but this script is "
              f"pinned to {NAVD88_TO_MLLW_M} m — update NAVD88_TO_MLLW_M.",
              file=sys.stderr)


# ---------------------------------------------------------------- the raster

def fetch_dem():
    """Pull the CUDEM window as float32 GeoTIFF and convert to ft above MLLW."""
    q = urllib.parse.urlencode({
        "bbox": ",".join(str(v) for v in BBOX), "bboxSR": 4326, "imageSR": 4326,
        "size": f"{GRID_W},{GRID_H}", "format": "tiff", "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation", "f": "image"})
    req = urllib.request.Request(f"{IMAGESERVER}/exportImage?{q}",
                                 headers={"User-Agent": "sandbar-sunset/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
    arr = np.array(Image.open(io.BytesIO(raw)), dtype="float64")
    arr[np.abs(arr) > 1e10] = np.nan          # NoData sentinel
    return (arr + NAVD88_TO_MLLW_M) * M_TO_FT


def crop_to_bar(mllw):
    h, w = mllw.shape
    W0, S0, E0, N0 = BBOX
    j0 = int((CROP["lon0"] - W0) / (E0 - W0) * w)
    j1 = int((CROP["lon1"] - W0) / (E0 - W0) * w)
    i0 = int((N0 - CROP["lat1"]) / (N0 - S0) * h)
    i1 = int((N0 - CROP["lat0"]) / (N0 - S0) * h)
    return mllw[i0:i1, j0:j1]


def pixel_metres(sub):
    """Pixel size in metres. The grid is geographic, so x and y differ."""
    h, w = sub.shape
    mid = (CROP["lat0"] + CROP["lat1"]) / 2
    dx = (CROP["lon1"] - CROP["lon0"]) * 111320 * math.cos(math.radians(mid)) / w
    dy = (CROP["lat1"] - CROP["lat0"]) * 110950 / h
    return dx, dy


def row_lat(i, h):
    return CROP["lat1"] - (i / h) * (CROP["lat1"] - CROP["lat0"])


# ---------------------------------------------------------------- analysis

def bottleneck_map(elev):
    """For every pixel, the highest achievable *minimum* elevation along a walking
    path from permanent land — i.e. the water level at which it stops being
    reachable on foot. A max-min (widest-path) Dijkstra.

    You can walk to pixel p without swimming iff  water_level < bottleneck[p].
    """
    h, w = elev.shape
    bott = np.full((h, w), -np.inf)
    pq = []
    ys, xs = np.where(elev > ALWAYS_DRY_FT)
    for y, x in zip(ys, xs):
        bott[y, x] = elev[y, x]
        pq.append((-elev[y, x], y, x))
    heapq.heapify(pq)
    nb = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
    while pq:
        negb, y, x = heapq.heappop(pq)
        b = -negb
        if b < bott[y, x] - 1e-12:
            continue
        for dy, dx in nb:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                cand = min(b, elev[ny, nx])
                if cand > bott[ny, nx] + 1e-12:
                    bott[ny, nx] = cand
                    heapq.heappush(pq, (-cand, ny, nx))
    return bott


def saddle_elevation(elev, bott):
    """The controlling height of the neck onto the bar: the median bottleneck over
    the dry crest of the spit. This is the number the almanac calls SADDLE."""
    h, w = elev.shape
    lat = np.array([[row_lat(i, h)] for i in range(h)]) * np.ones((1, w))
    crest = (elev > 1.0) & (elev < BAR_MAX_FT) & (lat > 42.6555)
    return float(np.median(bott[crest])), int(crest.sum())


def dry_area_table(elev, px_area):
    """Bare area of the bar itself vs water level, ignoring how you'd get there."""
    h, w = elev.shape
    lat = np.array([[row_lat(i, h)] for i in range(h)]) * np.ones((1, w))
    region = (elev > BAR_MIN_FT) & (elev < BAR_MAX_FT) & (lat > BAR_SOUTH_LAT)
    out = []
    for tenth in range(-20, 41, 2):
        lv = tenth / 10.0
        acres = (region & (elev > lv)).sum() * px_area / SQ_M_PER_ACRE
        out.append([lv, round(float(acres), 1)])
    return out, int(region.sum())


def exposure_curve(elev, dx, dy, px_area):
    """Walkable area and how far out you can get, as a function of water level.
    Distance is geodesic (walking), not straight-line."""
    h, w = elev.shape
    diag = math.hypot(dx, dy)
    steps = [(-1, 0, dy), (1, 0, dy), (0, -1, dx), (0, 1, dx),
             (-1, -1, diag), (-1, 1, diag), (1, -1, diag), (1, 1, diag)]
    rows = []
    for q in range(-6, 15):
        level = q / 4.0
        dry = elev > level
        dist = np.full((h, w), np.inf)
        pq = []
        ys, xs = np.where((elev > ALWAYS_DRY_FT) & dry)
        for y, x in zip(ys, xs):
            dist[y, x] = 0.0
            pq.append((0.0, y, x))
        heapq.heapify(pq)
        while pq:
            d, y, x = heapq.heappop(pq)
            if d > dist[y, x] + 1e-9:
                continue
            for dyi, dxi, cost in steps:
                ny, nx = y + dyi, x + dxi
                if 0 <= ny < h and 0 <= nx < w and dry[ny, nx] and d + cost < dist[ny, nx] - 1e-9:
                    dist[ny, nx] = d + cost
                    heapq.heappush(pq, (d + cost, ny, nx))
        tidal = np.isfinite(dist) & (elev <= ALWAYS_DRY_FT)
        acres = tidal.sum() * px_area / SQ_M_PER_ACRE
        reach = float(dist[tidal].max()) if tidal.any() else 0.0
        idx = np.where(tidal.any(axis=1))[0]
        north = row_lat(int(idx.min()), h) if idx.size else None
        rows.append([level, round(float(acres), 1), round(reach),
                     round(north, 5) if north is not None else None])
    return rows


# ---------------------------------------------------------------- rendering

def _paint(arr2d, stops):
    h, w = arr2d.shape
    out = np.zeros((h, w, 3), dtype=np.uint8)
    for k, (lo, rgb) in enumerate(stops):
        hi = stops[k + 1][0] if k + 1 < len(stops) else 1e9
        out[(arr2d >= lo) & (arr2d < hi) & (~np.isnan(arr2d))] = rgb
    return out


def render(mllw, sub, bott, outdir):
    wide = [(-999, (8, 24, 58)), (-12, (14, 45, 92)), (-6, (26, 78, 138)),
            (-3, (48, 118, 180)), (-1, (120, 175, 210)), (0, (255, 241, 170)),
            (1, (255, 206, 84)), (2, (247, 160, 52)), (3, (214, 190, 140)),
            (5, (196, 176, 132)), (8, (150, 160, 120)), (12, (96, 120, 86)),
            (30, (70, 95, 70))]
    Image.fromarray(_paint(mllw, wide)).save(os.path.join(outdir, "wingaersheek_map.png"))

    fine = [(-99, (10, 30, 70)), (-6, (22, 70, 130)), (-3, (45, 110, 175)),
            (-1.5, (95, 155, 200)), (-0.5, (180, 215, 230)), (0, (255, 250, 205)),
            (0.5, (255, 235, 150)), (1.0, (255, 215, 110)), (1.5, (255, 190, 80)),
            (2.0, (250, 160, 60)), (2.5, (235, 130, 55)), (3.0, (210, 175, 135)),
            (4.0, (195, 178, 140)), (6.0, (160, 165, 125)), (9.0, (100, 125, 90)),
            (20, (72, 98, 72))]
    h, w = sub.shape
    Image.fromarray(_paint(sub, fine)).resize((w * 2, h * 2), Image.NEAREST) \
        .save(os.path.join(outdir, "bar_zoom.png"))

    gate = [(-9, (12, 32, 72)), (-1.5, (30, 85, 150)), (-0.5, (90, 150, 200)),
            (0.0, (255, 252, 215)), (0.5, (255, 236, 150)), (1.0, (255, 205, 95)),
            (1.25, (250, 165, 60)), (1.45, (226, 96, 45)), (1.6, (150, 120, 180)),
            (2.5, (120, 150, 110)), (6.0, (80, 105, 80))]
    vis = np.where(np.isfinite(bott), bott, -9.0)
    vis[sub > ALWAYS_DRY_FT] = 9.0
    img = _paint(vis, gate)
    img[sub <= -3] = (12, 32, 72)
    Image.fromarray(img).resize((w * 2, h * 2), Image.NEAREST) \
        .save(os.path.join(outdir, "bottleneck_map.png"))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--refetch", action="store_true",
                    help="re-download the DEM even if a cached raster exists")
    ap.add_argument("--skip-datum-check", action="store_true",
                    help="skip the two network datum lookups")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    p = lambda name: os.path.join(args.outdir, name)

    if not args.skip_datum_check:
        print("Checking vertical datums ...")
        try:
            verify_datum()
        except Exception as e:
            print(f"  datum check skipped ({e})", file=sys.stderr)

    cached = p("dem_mllw_ft.npy")
    if os.path.exists(cached) and not args.refetch:
        print(f"Using cached raster {cached}")
        mllw = np.load(cached)
    else:
        print("Fetching CUDEM window from NCEI ...")
        mllw = fetch_dem()
        np.save(cached, mllw)
    valid = mllw[~np.isnan(mllw)]
    print(f"  raster {mllw.shape}  range {valid.min():.1f} .. {valid.max():.1f} ft MLLW")

    sub = crop_to_bar(mllw)
    np.save(p("bar_crop.npy"), sub)
    dx, dy = pixel_metres(sub)
    px_area = dx * dy
    print(f"  crop {sub.shape}  pixel {dx:.2f} x {dy:.2f} m")

    elev = np.where(np.isnan(sub), -999.0, sub)

    print("Bottleneck (max-min path) analysis ...")
    bott = bottleneck_map(elev)
    np.save(p("bottleneck.npy"), bott)
    saddle, n_crest = saddle_elevation(elev, bott)
    print(f"  crest pixels {n_crest}")
    print(f"  SADDLE onto the bar: {saddle:+.2f} ft MLLW"
          f"   (+/-~0.4 ft from datum uncertainty)")

    print("Bare-area table ...")
    table, n_region = dry_area_table(elev, px_area)
    json.dump(table, open(p("bar_dry_table.json"), "w"))
    print(f"  bar region {n_region} px = {n_region * px_area / SQ_M_PER_ACRE:.1f} acres")

    print("Exposure / reach curve ...")
    curve = exposure_curve(elev, dx, dy, px_area)
    json.dump(curve, open(p("exposure_curve.json"), "w"))
    tight = [r for r in curve if 1.0 <= r[0] <= 1.75]
    for lv, ac, reach, _ in tight:
        print(f"   {lv:+.2f} ft -> {ac:6.1f} acres reachable, {reach:4.0f} m out")

    print("Rendering maps ...")
    render(mllw, sub, bott, args.outdir)

    json.dump({"saddle_ft_mllw": round(saddle, 2),
               "navd88_to_mllw_m": NAVD88_TO_MLLW_M,
               "datum_uncertainty_ft": 0.38,
               "source": "NCEI CUDEM 1/9 arc-second, ncei19_n42x75_w070x75_2021v1",
               "survey_year": 2021},
              open(p("terrain.json"), "w"), indent=1)
    print(f"\nDone. Wrote products to {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()
