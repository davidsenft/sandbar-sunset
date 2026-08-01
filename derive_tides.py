#!/usr/bin/env python3
"""
derive_tides.py — fetch NOAA tide predictions and pack them into tidedata.json.

Tides are astronomy, so NOAA will hand out high/low predictions as far ahead as you
care to ask. This pulls every high and low for station 8441571 (Annisquam, Lobster
Cove — a mile up the river from the Wingaersheek flats) from 2026-07-01 through
2040-12-31 and encodes them into a compact string the page can carry inline.

Encoding
--------
Highs and lows strictly alternate, so the type of each event is implied by its
position and never stored. Each event costs 4 base-36 characters:

    2 chars  minutes since the previous event   (range seen: 288..444)
    2 chars  height in tenths of a foot, +40    (range seen: -23..117 -> 17..157)

20,475 events land in ~82 KB, small enough to inline in the page with no runtime
network calls. Times are NOAA's `lst_ldt`, i.e. local Gloucester wall-clock,
including the DST jumps; the page re-attaches the timezone on decode.

A NOAA quirk worth knowing: requesting a year at a time silently drops one event at
each year boundary, which breaks the high/low alternation the encoding depends on.
This fetches an overlapping window around every New Year and merges, then asserts
alternation before encoding.

Usage
-----
  python3 derive_tides.py                  # writes ./tidedata.json
  python3 derive_tides.py --outdir /tmp/x
"""

import argparse, datetime as dt, json, os, sys, time, urllib.parse, urllib.request

STATION = "8441571"
FIRST_YEAR, LAST_YEAR = 2026, 2040
START = "20260701"                       # data begins here
DATAGETTER = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def fetch(begin, end, tries=4):
    q = urllib.parse.urlencode({
        "product": "predictions", "application": "sandbar-sunset",
        "begin_date": begin, "end_date": end, "datum": "MLLW",
        "station": STATION, "time_zone": "lst_ldt", "units": "english",
        "interval": "hilo", "format": "json"})
    url = f"{DATAGETTER}?{q}"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sandbar-sunset/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = json.load(r)
            if "predictions" not in payload:
                raise ValueError(payload.get("error", payload))
            return payload["predictions"]
        except Exception as e:
            if attempt == tries - 1:
                raise
            print(f"    retry {begin}-{end}: {e}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))


def collect():
    """Whole years, plus an overlapping window across every New Year."""
    seen = {}

    def absorb(preds):
        for p in preds:
            t = dt.datetime.strptime(p["t"], "%Y-%m-%d %H:%M")
            seen[t] = (t, round(float(p["v"]) * 10), p["type"])

    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        begin = START if year == FIRST_YEAR else f"{year}0101"
        print(f"  {year} ...", end="", flush=True)
        preds = fetch(begin, f"{year}1231")
        absorb(preds)
        print(f" {len(preds)} events")

    print("  year-boundary patches ...", end="", flush=True)
    for year in range(FIRST_YEAR + 1, LAST_YEAR + 1):
        absorb(fetch(f"{year - 1}1230", f"{year}0102"))
    print(" done")

    return sorted(seen.values(), key=lambda e: e[0])


def b36(n, width):
    s = ""
    for _ in range(width):
        s = B36[n % 36] + s
        n //= 36
    return s


def encode(events):
    epoch = events[0][0]

    def wall_minutes(t):
        # deliberately wall-clock, not elapsed: DST shifts are carried, not smoothed
        delta = t - epoch
        return delta.days * 1440 + delta.seconds // 60

    parts, prev = [], 0
    for t, tenths, _kind in events:
        m = wall_minutes(t)
        gap = m - prev
        if not 0 <= gap < 1296:
            raise ValueError(f"gap {gap} min at {t} does not fit in 2 base-36 chars")
        h = tenths + 40
        if not 0 <= h < 1296:
            raise ValueError(f"height {tenths/10} ft at {t} does not fit")
        parts.append(b36(gap, 2) + b36(h, 2))
        prev = m
    return {"epoch": epoch.strftime("%Y-%m-%dT%H:%M"),
            "firstType": events[0][2],
            "blob": "".join(parts)}


def decode(data):
    """Mirror of the page's decoder — used to prove the round trip."""
    ed, et = data["epoch"].split("T")
    y, mo, d = (int(x) for x in ed.split("-"))
    hh, mm = (int(x) for x in et.split(":"))
    epoch = dt.datetime(y, mo, d, hh, mm)
    blob, out, cum, kind = data["blob"], [], 0, data["firstType"]
    for i in range(0, len(blob), 4):
        cum += B36.index(blob[i]) * 36 + B36.index(blob[i + 1])
        tenths = B36.index(blob[i + 2]) * 36 + B36.index(blob[i + 3]) - 40
        out.append((epoch + dt.timedelta(minutes=cum), tenths, kind))
        kind = "L" if kind == "H" else "H"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    print(f"Fetching station {STATION}, {FIRST_YEAR}-{LAST_YEAR} ...")
    events = collect()
    print(f"  {len(events)} events, {events[0][0]} .. {events[-1][0]}")

    breaks = [(a[0], b[0]) for a, b in zip(events, events[1:]) if a[2] == b[2]]
    if breaks:
        for a, b in breaks[:5]:
            print(f"  ALTERNATION BREAK: {a} then {b}", file=sys.stderr)
        raise SystemExit(f"{len(breaks)} high/low alternation breaks — the encoding "
                         "assumes strict alternation, so this must be fixed first.")
    print("  high/low alternation OK")

    data = encode(events)
    print(f"  blob {len(data['blob'])} chars, epoch {data['epoch']} ({data['firstType']} first)")

    # round trip: every event, not a sample
    back = decode(data)
    if len(back) != len(events):
        raise SystemExit(f"round trip length mismatch: {len(back)} vs {len(events)}")
    for orig, got in zip(events, back):
        if orig[0] != got[0] or orig[1] != got[1] or orig[2] != got[2]:
            raise SystemExit(f"round trip mismatch at {orig[0]}: {orig} != {got}")
    print(f"  round trip verified across all {len(back)} events")

    dest = os.path.join(args.outdir, "tidedata.json")
    json.dump(data, open(dest, "w"))
    print(f"\nWrote {dest} ({os.path.getsize(dest):,} bytes)")


if __name__ == "__main__":
    main()
