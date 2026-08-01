#!/usr/bin/env python3
"""
build.py — inline the data into the page template and write public/index.html.

The published page is a single self-contained file: no CDN, no fonts, no runtime
network calls. That is achieved here, by substituting two placeholders in
template.html with JSON produced by the derive scripts.

    __TIDEDATA__   tidedata.json       from derive_tides.py
    __DRYTBL__     bar_dry_table.json  from derive_sandbar.py

Usage
-----
  python3 derive_tides.py     # refresh the tide table   (network)
  python3 derive_sandbar.py   # refresh the bar geometry (network, cached)
  python3 build.py            # -> public/index.html

public/ is the Cloudflare Pages output directory, so nothing else in the repo is
ever served.
"""

import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSTITUTIONS = {"__TIDEDATA__": "tidedata.json", "__DRYTBL__": "bar_dry_table.json"}


def main():
    template_path = os.path.join(HERE, "template.html")
    page = open(template_path, encoding="utf-8").read()

    for token, filename in SUBSTITUTIONS.items():
        if token not in page:
            raise SystemExit(f"{token} not found in template.html — nothing to fill")
        path = os.path.join(HERE, filename)
        if not os.path.exists(path):
            raise SystemExit(f"missing {filename} — run the derive scripts first")
        blob = json.dumps(json.load(open(path)), separators=(",", ":"))
        page = page.replace(token, blob)
        print(f"  {token:<14} <- {filename} ({len(blob):,} chars)")

    dest = os.path.join(HERE, "public", "index.html")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "w", encoding="utf-8").write(page)
    print(f"\nWrote {dest} ({len(page.encode('utf-8')):,} bytes)")


if __name__ == "__main__":
    main()
