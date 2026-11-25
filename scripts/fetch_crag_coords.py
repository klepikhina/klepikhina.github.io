#!/usr/bin/env python3
"""Resolve Mountain Project tick locations to coordinates.

Mountain Project CSV exports contain a full location hierarchy (for example
``Colorado > Boulder > Flatirons > North > First Flatiron``) and a route URL,
but no latitude/longitude. This script fetches each unique location's route
page once, reads the coordinates that Mountain Project embeds in the page, and
writes a reusable ``scripts/crag_coords.json`` mapping.

Only public page data is written. The ticks CSV itself is read locally and is
never copied into the output.

Usage
-----
    python3 scripts/fetch_crag_coords.py \
        --ticks private/mountain-project-ticks.csv

Then build the atlas:

    python3 scripts/build_atlas.py \
        --ticks-csv private/mountain-project-ticks.csv
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE = Path.home() / ".cache" / "klepikhina-atlas" / "mp-pages.json"
LAT_RE = re.compile(r'"latitude"\s*:\s*"(-?\d+(?:\.\d+)?)"')
LON_RE = re.compile(r'"longitude"\s*:\s*"(-?\d+(?:\.\d+)?)"')
USER_AGENT = "klepikhina-atlas/1.0 (personal climbing log)"


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def read_ticks(path: Path) -> List[Dict[str, str]]:
    import csv

    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def fetch_coords(url: str, timeout: int) -> Optional[Tuple[float, float]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"warning: could not fetch {url}: {exc}", file=sys.stderr)
        return None
    lat = LAT_RE.search(html)
    lon = LON_RE.search(html)
    if not lat or not lon:
        print(f"warning: no coordinates found on {url}", file=sys.stderr)
        return None
    return round(float(lon.group(1)), 6), round(float(lat.group(1)), 6)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--ticks",
        type=Path,
        default=REPO_ROOT / "private" / "mountain-project-ticks.csv",
        help="Mountain Project ticks CSV (default: private/mountain-project-ticks.csv)",
    )
    parser.add_argument("--out", type=Path, default=SCRIPT_DIR / "crag_coords.json")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--delay", type=float, default=0.6, help="seconds between page requests")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--refresh", action="store_true", help="ignore the page cache")
    args = parser.parse_args(argv)

    if not args.ticks.exists():
        sys.exit(
            f"ticks CSV not found: {args.ticks}\n"
            "Download your ticks from Mountain Project and keep the file outside data/ "
            "(the private/ folder is gitignored)."
        )

    rows = read_ticks(args.ticks)
    by_location: Dict[str, str] = {}
    for row in rows:
        location = (row.get("Location") or "").strip()
        url = (row.get("URL") or "").strip()
        if location and url and location not in by_location:
            by_location[location] = url
    if not by_location:
        sys.exit("no Location/URL pairs found; is this a Mountain Project ticks export?")

    cache: Dict[str, Dict[str, object]] = {}
    if args.cache.exists() and not args.refresh:
        try:
            cache = json.loads(args.cache.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}

    resolved: Dict[str, List[float]] = {}
    missing: List[str] = []
    for index, (location, url) in enumerate(sorted(by_location.items()), start=1):
        record = cache.get(url) if not args.refresh else None
        coords: Optional[Tuple[float, float]] = None
        if isinstance(record, dict) and record.get("lon") is not None:
            coords = (float(record["lon"]), float(record["lat"]))  # type: ignore[arg-type]
        if coords is None:
            coords = fetch_coords(url, args.timeout)
            if coords is not None:
                cache[url] = {"lon": coords[0], "lat": coords[1], "location": location}
            if args.delay:
                time.sleep(args.delay)
        if coords is None:
            missing.append(location)
            continue
        resolved[location] = [coords[0], coords[1]]
        print(f"[{index:3}/{len(by_location)}] {coords[0]:9.5f},{coords[1]:8.5f}  {location}")

    # Also expose the leaf name (e.g. "First Flatiron") for reuse across exports.
    leaves: Dict[str, List[float]] = {}
    conflicts: Dict[str, List[str]] = {}
    for location, coords in resolved.items():
        leaf = location.split(">")[-1].strip()
        if not leaf:
            continue
        if leaf in leaves and leaves[leaf] != coords:
            conflicts.setdefault(leaf, []).append(location)
            continue
        leaves.setdefault(leaf, coords)

    output: Dict[str, object] = {
        "_comment": "Public Mountain Project coordinates. Generated by scripts/fetch_crag_coords.py.",
        "_leaf_conflicts": sorted(conflicts),
    }
    output.update(resolved)
    for leaf, coords in leaves.items():
        output.setdefault(leaf, coords)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"\nwrote {args.out}: {len(resolved)} locations, {len(leaves)} leaf names, {len(missing)} unresolved")
    if missing:
        print("unresolved (add manually if needed):", file=sys.stderr)
        for location in missing:
            print(f"  {location}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
