# Sandbar Sunset

**Live at [sandbar-sunset.davesenft.com](https://sandbar-sunset.davesenft.com)**

At Wingaersheek Beach in Gloucester, Massachusetts, a sandbar runs out from the shore and
bares itself at low tide. Once in a while the tide falls away at the same moment the sun
goes down, and you can walk far out onto it in the last of the light. This works out when
that happens next — and every other time it will happen through December 2040.

Tides are astronomy, so this needs no server and no live data. The whole thing is one
self-contained 124 KB HTML file: no CDN, no fonts, no runtime network calls. It works
offline.

## The idea

Three things have to line up, and the page rates each evening on where the tide curve
sits relative to two crossing depths and the golden hour.

The bar is a spit joined to the beach by a low saddle, and that saddle is the crux.
Mapping NOAA's 1/9 arc-second coastal elevation model puts it at roughly **+1.35 ft
above MLLW**, while the bar's own spine runs a foot or two higher. That gap is what makes
the good evenings good: there is a real window where the bar is already bare sand while
the neck you cross is still shin-deep.

Two levels follow from it. Below about **1.9 ft** the crossing is ankle-deep at most and
you can simply walk it; below about **3.4 ft** it is a wade, up to roughly thigh-deep.
Deeper than that and the bar is effectively an island.

The ratings are pure geometry — nothing weighted or averaged:

| | |
|---|---|
| **Three suns** | golden hour opens while the crossing is in the wadeable band, and the half hour after sunset is walkable throughout |
| **Two suns** | the half hour either side of sunset is walkable throughout |
| **One sun** | that same hour is wadeable throughout |

That works out to roughly 23, 63 and 42 evenings a year respectively, with about ten
three-sun evenings falling in swimmable weather.

## Rebuilding

```bash
python3 derive_tides.py     # NOAA predictions -> tidedata.json        (network)
python3 derive_sandbar.py   # elevation model -> bar_dry_table.json    (network, cached)
python3 build.py            # inline both -> public/index.html
```

`derive_tides.py` pulls every high and low for station 8441571 and packs 20,475 events
into ~82 KB of base-36. `derive_sandbar.py` downloads the elevation raster, converts it
from NAVD88 to feet above MLLW, and runs a max-min path analysis to find the saddle. Both
verify themselves: the tide encoder round-trips every event, and the terrain script
cross-checks its datum conversion against the tide station's published values.

Only `public/` is deployed. The rasters, derived tables and rendered maps stay in the repo
as evidence and are never served.

## Sources and caveats

- **Tides** — NOAA harmonic predictions, station [8441571, Annisquam (Lobster Cove)](https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id=8441571), a mile up the river from the flats.
- **Sunset** — NOAA solar position algorithm computed for the beach itself (42.650° N, 70.684° W), checked against the US Naval Observatory.
- **Terrain** — NOAA/NCEI CUDEM 1/9 arc-second, tile `ncei19_n42x75_w070x75_2021v1`, converted to MLLW using VDatum.

The saddle height carries about **±0.4 ft** of datum uncertainty, the elevation survey
dates from 2021, and sandbars migrate. Treat the depths as a good guide rather than a
promise. The predictions also ignore weather entirely — wind, storm surge and cloud on
the horizon do as they like.

Tide data runs out on **31 December 2040**, at which point `derive_tides.py` needs a
re-run and the page a rebuild.

## Deploying

A Cloudflare Worker serving static assets. `wrangler.toml` points at `public/` and claims
the custom domain; pushing to `main` triggers a build and deploy.
