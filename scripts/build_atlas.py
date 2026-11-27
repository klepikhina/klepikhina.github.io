#!/usr/bin/env python3
"""Build the Adventure Atlas.

Three layers are produced:

  data/regions.geojson         Aggregate stats per state/province/country.
  data/activity_density.geojson Anonymised coarse-cell activity density.
  data/featured_routes.geojson Curated, blog-linked routes and crag markers.
  data/activities.json         Canonical story records for every Jekyll page.
  data/region_pages.json       Region page/nav definitions generated from the atlas.
  data/insights/*.json         Privacy-safe aggregates for visualisations.
  region/*.html                Region pages, generated from data/region_pages.json.

By default the atlas is global. Natural Earth admin-1 polygons supply US states
and Canadian provinces; admin-0 polygons supply every other country. Every
geocoded activity or blog post is assigned to one of those public regions, so
Colorado, British Columbia, Costa Rica, Iceland, Georgia, and Japan can all
appear without drawing private track geometry.

The script has two data sources:

  * Blog mode (default) - reads the YAML front matter of this site's posts
    (title, location, region, latitude/longitude or nested coordinates, activity,
    date). This produces the committed sample files.

  * Private mode - pass --strava-dir, --garmin-dir, and/or --ticks-csv. Raw
    GPX/FIT tracks are read in memory, spatially joined to admin polygons, and
    reduced to counts and sums per region. Raw tracks are never written to disk
    and never copied into the repo. Only aggregate numbers and public admin
    polygons are emitted. Garmin activities win when the same outing appears in
    both Strava and Garmin exports.

Curated blogged GPX (opt-in, public by definition) can be turned into real
route lines with --blogged. Those tracks are trimmed by --trim-meters at each
end and written without timestamps; nothing is inferred or exported silently.

Usage
-----
Sample data (no private files needed):

    python3 scripts/build_atlas.py --download-watersheds

Private aggregation + curated lines (exports stay in gitignored private/):

    python3 scripts/fetch_crag_coords.py --ticks private/mountain-project-ticks.csv
    python3 scripts/build_atlas.py \
        --strava-dir private/strava_export \
        --garmin-dir private/garmin_export \
        --ticks-csv private/mountain-project-ticks.csv \
        --blogged scripts/blogged_tracks.json \
        --download-watersheds

Parsed summaries are cached in private/atlas_state.json. Subsequent runs only
read new or changed GPX/FIT files and new or changed Mountain Project rows.
Use --no-cache to force a full reparse.

See scripts/README.md for the full workflow.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pandas as pd
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import geopandas as gpd
    from shapely.geometry import LineString, Point, mapping
    from shapely.ops import unary_union
except ImportError as exc:  # pragma: no cover - friendly setup message
    sys.exit(
        "build_atlas.py needs geopandas and shapely.\n"
        "Install them with: python3 -m pip install -r scripts/requirements.txt\n"
        f"({exc})"
    )


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE = Path(
    os.environ.get("KLEPIKINA_ATLAS_CACHE", Path.home() / ".cache" / "klepikhina-atlas")
).expanduser()
EARTH_RADIUS_M = 6_371_008.8
DEFAULT_CRS = "EPSG:4326"
EQUAL_AREA_CRS = "EPSG:6933"
METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.28084
MILES_PER_KILOMETER = 0.621371
# Bump when cached summaries need to be rebuilt with new unit handling.
CACHE_UNITS_VERSION = 2

DIST_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(mi|mile|miles|km|kilometer|kilometre|kms)\b", re.I)
ELEV_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(ft|feet|foot|m|meter|metre|meters|metres)\b", re.I)
# Guardrail: published GPS-derived labels must not reintroduce metric units.
METRIC_LABEL_RE = re.compile(
    r"(?<![\w.])[\d,]+(?:\.\d+)?\s*"
    r"(?:kms|km|kilometers?|kilometres?|meters?|metres?|m)(?![\w])",
    re.I,
)
GRADE_RE = re.compile(r"\b(5\.\d+[abcd]?|WI\s?\d+(?:-\d+)?[+-]?|V\d+(?:-\d+)?)\b", re.I)
PITCH_RE = re.compile(r"(\d+)\s*(?:pitches|pitch)\b", re.I)
DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})-")
SLUG_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})-(.+)$")
TAG_RE = re.compile(r"<[^>]+>")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL_RE = re.compile(r"https?://\S+")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def log(message: str) -> None:
    print(message)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> Optional[int]:
    number = to_float(value)
    if number is None:
        return None
    return int(round(number))


def normalize_date(value: Any) -> str:
    """Return an ISO date from the many formats Strava/MP/Jekyll use."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            pass
    for fmt in (
        "%b %d, %Y, %I:%M:%S %p",
        "%B %d, %Y, %I:%M:%S %p",
        "%b %d, %Y %I:%M:%S %p",
        "%B %d, %Y %I:%M:%S %p",
        "%b %d, %Y",
        "%B %d, %Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d %b %Y",
    ):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def date_from_slug(slug: str) -> str:
    match = SLUG_DATE_RE.match(slug)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups()[:3])
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def season_year(date_text: str) -> Optional[int]:
    if not date_text:
        return None
    try:
        return int(date_text[:4])
    except ValueError:
        return None


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text or "route"


def haversine_m(a: Sequence[float], b: Sequence[float]) -> float:
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def finite_or_none(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(number) else None


def finite_values(records: List[Dict[str, Any]], key: str) -> List[float]:
    """Numeric values only; NaN/Infinity would make the GeoJSON invalid."""
    values: List[float] = []
    for record in records:
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def round_nested(value: Any, ndigits: int = 5) -> Any:
    if isinstance(value, (list, tuple)):
        return [round_nested(item, ndigits) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), ndigits)
    return value


def geometry_dict(geom, ndigits: int = 5) -> Dict[str, Any]:
    data = mapping(geom)
    data["coordinates"] = round_nested(data["coordinates"], ndigits)
    return data


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ---------------------------------------------------------------------------
# Incremental cache (private, gitignored)
# ---------------------------------------------------------------------------

# Bump when parsing or normalisation changes so cached summaries are rebuilt.
STATE_VERSION = 2


def empty_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "strava": {},
        "garmin": {},
        "ticks": {"coords_sig": "", "rows": {}},
    }


def load_state(path: Path, enabled: bool = True) -> Dict[str, Any]:
    """Load the local cache of already-parsed private exports."""
    if not enabled or not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        warn(f"could not read cache {path} ({exc}); reparsing private exports")
        return empty_state()
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        warn("cache schema changed; reparsing private exports")
        return empty_state()
    data.setdefault("strava", {})
    data.setdefault("garmin", {})
    ticks = data.setdefault("ticks", {})
    ticks.setdefault("coords_sig", "")
    ticks.setdefault("rows", {})
    return data


def save_state(path: Path, state: Dict[str, Any], enabled: bool = True, dry_run: bool = False) -> None:
    if not enabled or dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=1)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    log(f"updated private export cache: {path}")


def file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_private_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Strip spatial assignments so a cached activity is not pinned to an old region."""
    result = dict(record)
    result["region_name"] = None
    result["region_id"] = None
    result["configured_region"] = None
    for key in ("distance_mi", "elevation_ft"):
        result[key] = finite_or_none(result.get(key))
    result["units_version"] = CACHE_UNITS_VERSION
    return result


def cached_private_records(cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reuse parsed summaries when raw exports are not available (cloud mode)."""
    records: List[Dict[str, Any]] = []
    stale = 0
    for entry in cache.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("record"), dict):
            continue
        record = entry["record"]
        if record.get("units_version") != CACHE_UNITS_VERSION:
            stale += 1
            continue
        records.append(stable_private_record(record))
    if stale:
        sys.exit(
            f"{stale} cached activity summaries use an old unit schema. "
            "Re-run the build from the source exports (without --use-cache-records) "
            "before publishing; refusing to build stale unit data."
        )
    return records


# ---------------------------------------------------------------------------
# Post front matter
# ---------------------------------------------------------------------------


def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw, body = parts[1], parts[2]
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and current_key:
            value = item.group(1).strip().strip('"').strip("'")
            existing = data.get(current_key)
            if not isinstance(existing, list):
                existing = []
            existing.append(value)
            data[current_key] = existing
            continue
        nested = re.match(r"^\s+([A-Za-z0-9_]+):\s*(.*)$", line)
        if nested and current_key and isinstance(data.get(current_key), list):
            nested_value = nested.group(2).strip().strip('"').strip("'")
            data[current_key].append(f"{nested.group(1)}: {nested_value}")
            continue
        match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        current_key = key
        if value.startswith("[") and value.endswith("]"):
            data[key] = [part.strip().strip('"').strip("'") for part in value[1:-1].split(",") if part.strip()]
        elif value:
            data[key] = value.strip('"').strip("'")
        else:
            data[key] = []
    return data, body


def front_matter_coordinates(front: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Read flat or nested ``coordinates:`` front matter."""
    lat = to_float(front.get("latitude"))
    lon = to_float(front.get("longitude"))
    if lat is not None and lon is not None:
        return lat, lon
    nested = front.get("coordinates")
    if isinstance(nested, dict):
        nested = [nested]
    if isinstance(nested, list):
        parsed: Dict[str, str] = {}
        for item in nested:
            if isinstance(item, dict):
                parsed.update({str(key): str(value) for key, value in item.items()})
            elif isinstance(item, str) and ":" in item:
                key, value = item.split(":", 1)
                parsed[key.strip()] = value.strip().strip('"').strip("'")
            if parsed.get("latitude") and parsed.get("longitude"):
                return to_float(parsed["latitude"]), to_float(parsed["longitude"])
        if parsed.get("lat") and parsed.get("lon"):
            return to_float(parsed["lat"]), to_float(parsed["lon"])
    return None, None


def parse_title_metrics(title: str, activity: str) -> Dict[str, Any]:
    """Pull distance / gain / grade out of the prose titles."""
    metrics: Dict[str, Any] = {
        "distance_mi": None,
        "elevation_ft": None,
        "grade": "",
        "pitches": None,
    }
    distance = DIST_RE.search(title or "")
    if distance:
        value = to_float(distance.group(1))
        unit = distance.group(2).lower()
        if value is not None:
            miles = value * MILES_PER_KILOMETER if unit.startswith("km") or unit.startswith("kilomet") else value
            metrics["distance_mi"] = round(miles, 2)

    elevation = ELEV_RE.search(title or "")
    if elevation:
        value = to_float(elevation.group(1))
        unit = elevation.group(2).lower()
        if value is not None:
            feet = value * FEET_PER_METER if unit in {"m", "meter", "metre", "meters", "metres"} else value
            metrics["elevation_ft"] = round(feet)

    if activity == "climb":
        grade = GRADE_RE.search(title or "")
        if grade:
            metrics["grade"] = re.sub(r"\s+", "", grade.group(1)).upper().replace("WI", "WI")

    pitches = PITCH_RE.search(title or "")
    if pitches:
        metrics["pitches"] = int(pitches.group(1))
    return metrics


def derive_summary(body: str, max_len: int = 172) -> str:
    text = TAG_RE.sub(" ", body or "")
    text = MD_IMAGE_RE.sub(" ", text)
    text = MD_LINK_RE.sub(r"\1", text)
    text = URL_RE.sub(" ", text)
    text = re.sub(r"[*_`>#]+", "", text)
    for paragraph in re.split(r"\n\s*\n", text):
        clean = " ".join(paragraph.split())
        if len(clean) < 35:
            continue
        if clean.startswith(("{", "|", "!", "[")):
            continue
        if len(clean) <= max_len:
            return clean
        return clean[: max_len - 1].rstrip() + "\u2026"
    return ""


def difficulty_for(activity: str, grade: str) -> str:
    if activity == "climb":
        return grade or "Rock climb"
    return {
        "bike": "Mountain bike",
        "hike": "Alpine hike",
        "run": "Trail run",
        "ski": "Backcountry ski",
        "water": "Canyoneering",
    }.get(activity, activity.title() if activity else "Adventure")


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        sys.exit(f"regions config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def post_region_label(front: Dict[str, Any], location: str, config: Dict[str, Any]) -> str:
    """Choose the atlas region label for a post.

    In global mode the first non-empty front-matter field in ``group_by`` wins
    (usually ``region``, then ``country``, then ``location``). Legacy mode uses
    the explicit location map in regions_config.json.
    """
    if config.get("mode") == "dynamic":
        for key in config.get("group_by", ["region", "country", "location"]):
            value = str(front.get(key) or "").strip()
            if value:
                return value
        return location or "Unassigned"
    return ""


def load_posts(posts_dir: Path, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    location_to_region: Dict[str, Dict[str, str]] = {}
    for region in config.get("regions", []):
        for location in region.get("locations", []):
            location_to_region[location] = {
                "region_name": region["region_name"],
                "region_id": region["region_id"],
            }

    posts: List[Dict[str, Any]] = []
    for path in sorted(posts_dir.glob("*/_posts/*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        front, body = parse_front_matter(text)
        lat, lon = front_matter_coordinates(front)
        if lat is None or lon is None:
            warn(f"no coordinates for {path.name}; skipping")
            continue
        activity = str(front.get("activity") or "").strip().lower()
        if activity not in config["activity_labels"]:
            continue
        slug = path.stem
        slug_match = SLUG_DATE_RE.match(slug)
        slug = slug_match.group(4) if slug_match else slug
        date_text = date_from_slug(path.stem)
        title = str(front.get("title") or "").strip()
        location = str(front.get("location") or "").strip()
        if config.get("mode") == "dynamic":
            label = post_region_label(front, location, config)
            region = {"region_name": label, "region_id": slugify(label)}
        else:
            region = location_to_region.get(location, {})
        metrics = parse_title_metrics(title, activity)
        posts.append(
            {
                "slug": slug,
                "file": path.name,
                "title": title,
                "activity": activity,
                "location": location,
                "country": str(front.get("country") or "").strip(),
                "region_name": region.get("region_name"),
                "region_id": region.get("region_id"),
                "configured_region": region.get("region_name"),
                "lat": lat,
                "lon": lon,
                "date": date_text,
                "season_year": season_year(date_text),
                "summary": derive_summary(body),
                "image": str(front.get("featured_image") or "").strip(),
                "post_url": f"/{slug}/",
                "distance_mi": metrics["distance_mi"],
                "elevation_ft": metrics["elevation_ft"],
                "grade": metrics["grade"],
                "pitches": metrics["pitches"],
                "source": "blog",
            }
        )
    return posts


# ---------------------------------------------------------------------------
# Track readers (private mode)
# ---------------------------------------------------------------------------


def read_gpx(path: Path) -> List[Tuple[float, float, Optional[float], Optional[dt.datetime]]]:
    try:
        import gpxpy  # type: ignore
    except ImportError:
        warn("gpxpy is not installed; cannot read GPX files")
        return []
    try:
        if path.name.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                gpx = gpxpy.parse(handle)
        else:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                gpx = gpxpy.parse(handle)
    except Exception as exc:  # noqa: BLE001 - malformed exports shouldn't abort the run
        warn(f"could not parse {path.name}: {exc}")
        return []
    points: List[Tuple[float, float, Optional[float], Optional[dt.datetime]]] = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points.append((point.longitude, point.latitude, point.elevation, point.time))
    return points


def read_fit(path: Path) -> Tuple[List[Tuple[float, float, Optional[float], Optional[dt.datetime]]], str]:
    try:
        from fitparse import FitFile  # type: ignore
    except ImportError:
        warn("fitparse is not installed; cannot read FIT files")
        return [], ""
    handle = gzip.open(path, "rb") if path.name.endswith(".gz") else path.open("rb")
    points: List[Tuple[float, float, Optional[float], Optional[dt.datetime]]] = []
    sport = ""
    try:
        fit = FitFile(handle)
        for message in fit:
            if message.name == "record":
                values = {field.name: field.value for field in message}
                lat = values.get("position_lat")
                lon = values.get("position_long")
                if lat is None or lon is None:
                    continue
                lat_deg = float(lat) * (180.0 / 2**31)
                lon_deg = float(lon) * (180.0 / 2**31)
                elevation = values.get("enhanced_altitude") or values.get("altitude")
                points.append((lon_deg, lat_deg, elevation, values.get("timestamp")))
            elif message.name == "session" and not sport:
                sport = str(message.get_value("sport") or "")
    except Exception as exc:  # noqa: BLE001
        warn(f"could not parse {path.name}: {exc}")
        return [], ""
    finally:
        handle.close()
    return points, sport


def track_stats(points: Sequence[Tuple[float, float, Optional[float], Optional[dt.datetime]]]) -> Dict[str, float]:
    distance = 0.0
    for first, second in zip(points, points[1:]):
        distance += haversine_m(first, second)
    elevations = [point[2] for point in points if point[2] is not None]
    gain = 0.0
    if len(elevations) >= 2:
        previous = elevations[0]
        for elevation in elevations[1:]:
            delta = elevation - previous
            if delta > 1.0:  # ignore GPS noise
                gain += delta
            previous = elevation
    return {
        "distance_m": distance,
        "distance_mi": distance / METERS_PER_MILE,
        "gain_m": gain,
        "gain_ft": gain * FEET_PER_METER,
    }


def normalize_sport(value: str) -> str:
    """Map Strava activity names to the six atlas filters.

    Water is checked before ski so "Waterskiing" is not mistaken for a snow
    sport. The token list covers Strava's water activity types as well as the
    generic "Water Sport" bucket, including paddling and swimming.
    """
    text = (value or "").lower()
    if any(token in text for token in ("ride", "bike", "cycl", "ebike", "e-bike")):
        return "bike"
    if "climb" in text:
        return "climb"
    if any(token in text for token in ("hike", "walk", "trek")):
        return "hike"
    if "run" in text:
        return "run"
    if any(token in text for token in (
        "water", "kayak", "paddl", "canoe", "swim", "row", "surf",
        "wake", "raft", "div", "snorkel", "sail", "stand up", "sup"
    )):
        return "water"
    if any(token in text for token in ("ski", "snowboard")):
        return "ski"
    return "other"


def classify_ski_style(text: str) -> str:
    """Map Strava's ski activity names to touring / alpine / nordic."""
    value = (text or "").lower()
    if any(token in value for token in ("backcountry", "touring", "tour ", "randonee", "randonnee", "rando", "skimo", "skin")):
        return "touring"
    if any(token in value for token in ("nordic", "cross-country", "cross country", "xc ski", "xc skiing")):
        return "nordic"
    if any(token in value for token in ("alpine", "resort", "downhill", "snowboard")):
        return "alpine"
    return "alpine"


def read_activity_csv_metadata(
    path: Path, default_distance_unit: str = "auto"
) -> Dict[str, Dict[str, Any]]:
    """Read source CSV metrics once, in metres, from Strava/Garmin exports.

    Strava exports can contain two ``Distance`` columns: a display column and a
    canonical metric column. The last one is canonical (metres); Garmin exports
    have a single metric column. Keeping these values in metres at ingest lets
    us convert to miles/feet in exactly one place.
    """

    def indices(headers: Sequence[str], names: set[str]) -> List[int]:
        return [index for index, header in enumerate(headers) if header in names]

    def value(row: Sequence[str], index: Optional[int]) -> str:
        if index is None or index >= len(row):
            return ""
        return (row[index] or "").strip()

    metadata: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = [str(header).strip().lower() for header in next(reader)]
        except StopIteration:
            return {}

        filename_indices = indices(headers, {"filename", "file name"})
        id_indices = indices(headers, {"activity id", "id"})
        date_indices = indices(headers, {"activity date", "date"})
        name_indices = indices(headers, {"activity name", "name"})
        type_indices = indices(headers, {"activity type", "type"})
        distance_indices = indices(headers, {"distance", "distance (m)", "distance_m"})
        elevation_indices = indices(
            headers, {"elevation gain", "elevation gain (m)", "elevation_gain"}
        )

        filename_index = filename_indices[0] if filename_indices else None
        id_index = id_indices[0] if id_indices else None
        date_index = date_indices[0] if date_indices else None
        name_index = name_indices[0] if name_indices else None
        type_index = type_indices[0] if type_indices else None
        distance_index = distance_indices[-1] if distance_indices else None
        elevation_index = elevation_indices[-1] if elevation_indices else None

        for row in reader:
            filename = value(row, filename_index)
            activity_id = value(row, id_index)
            activity_name = value(row, name_index)
            activity_type = value(row, type_index)

            distance_m: Optional[float] = None
            if distance_index is not None:
                distance_m = to_float(value(row, distance_index))
                if distance_m is not None:
                    header = headers[distance_index]
                    if "km" in header or "kilomet" in header:
                        distance_m *= 1000
                    elif "mi" in header:
                        distance_m *= METERS_PER_MILE
                    elif default_distance_unit == "km":
                        distance_m *= 1000
                    elif default_distance_unit == "mi":
                        distance_m *= METERS_PER_MILE
                    elif (
                        default_distance_unit == "auto"
                        and len(distance_indices) == 1
                        and 0 < distance_m < 100
                    ):
                        # A lone Strava display column is usually kilometres.
                        distance_m *= 1000

            elevation_m: Optional[float] = None
            if elevation_index is not None:
                elevation_m = to_float(value(row, elevation_index))
                if elevation_m is not None and "ft" in headers[elevation_index]:
                    elevation_m /= FEET_PER_METER

            record = {
                "name": activity_name,
                "date": normalize_date(value(row, date_index)),
                "sport": normalize_sport(activity_type),
                "activity_type": activity_type,
                "ski_style": classify_ski_style(activity_type + " " + activity_name),
                "distance_m": distance_m,
                "elevation_m": elevation_m,
            }
            if filename:
                metadata[Path(filename).name] = record
            if activity_id:
                metadata[activity_id] = record
    return metadata


def read_strava_metadata(strava_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Map a file name / activity id to metadata from Strava's activities.csv."""
    path = strava_dir / "activities.csv"
    if not path.exists():
        return {}
    return read_activity_csv_metadata(path, default_distance_unit="auto")


def read_garmin_metadata(garmin_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Read Garmin's activities.csv, whose metrics are always metres."""
    path = garmin_dir / "activities.csv"
    if not path.exists():
        return {}
    return read_activity_csv_metadata(path, default_distance_unit="m")


def source_imperial_metrics(meta: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Convert canonical source metrics (metres) to published imperial fields."""
    distance_m = meta.get("distance_m")
    elevation_m = meta.get("elevation_m")
    distance_mi = round(distance_m / METERS_PER_MILE, 2) if isinstance(distance_m, (int, float)) else None
    elevation_ft = round(elevation_m * FEET_PER_METER) if isinstance(elevation_m, (int, float)) else None
    return distance_mi, elevation_ft


def metadata_signature(meta: Dict[str, Any]) -> str:
    """Invalidate cached summaries when the source CSV metrics change."""
    distance_mi, elevation_ft = source_imperial_metrics(meta)
    return f"{distance_mi}:{elevation_ft}"


def discover_track_files(strava_dir: Path) -> List[Path]:
    suffixes = (".gpx", ".gpx.gz", ".fit", ".fit.gz")
    files: List[Path] = []
    skipped = 0
    for path in sorted(strava_dir.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if any(name.endswith(suffix) for suffix in suffixes):
            files.append(path)
        elif name.endswith(".tcx"):
            skipped += 1
    if skipped:
        warn(f"skipping {skipped} TCX file(s); export GPX or FIT instead")
    return files


def load_strava_activities(
    strava_dir: Path,
    cache: Optional[Dict[str, Any]] = None,
    prune_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Read Strava tracks, reusing cached summaries for unchanged files.

    A file is considered unchanged when its size/mtime and source CSV metrics
    match the cached signature. New, modified, or removed files are the only
    ones re-parsed.
    """
    if not strava_dir.exists():
        warn(f"Strava directory not found: {strava_dir}")
        return []
    metadata = read_strava_metadata(strava_dir)
    files = discover_track_files(strava_dir)
    log(f"discovered {len(files):,} Strava track files")
    records: List[Dict[str, Any]] = []
    active_keys = set()
    reused = 0
    parsed = 0

    for path in files:
        key = str(path.resolve())
        active_keys.add(key)
        meta = metadata.get(path.name) or metadata.get(path.stem.split(".")[0]) or {}
        signature = "|".join(
            [file_signature(path), str(CACHE_UNITS_VERSION), metadata_signature(meta)]
        )
        cached = cache.get(key) if cache is not None else None
        if (
            isinstance(cached, dict)
            and cached.get("sig") == signature
            and isinstance(cached.get("record"), dict)
        ):
            records.append(stable_private_record(cached["record"]))
            reused += 1
            continue

        if path.name.lower().endswith((".fit", ".fit.gz")):
            points, fit_sport = read_fit(path)
        else:
            points = read_gpx(path)
            fit_sport = ""
        if len(points) < 2:
            if cache is not None:
                cache.pop(key, None)
            continue
        stats = track_stats(points)
        source_distance_mi, source_elevation_ft = source_imperial_metrics(meta)
        date_text = meta.get("date") or ""
        if not date_text:
            timestamps = [point[3] for point in points if point[3] is not None]
            if timestamps:
                date_text = timestamps[0].date().isoformat()
        sport = meta.get("sport") or normalize_sport(fit_sport) or "other"
        record = {
            "slug": path.stem,
            "title": meta.get("name") or path.stem,
            "activity": sport,
            "ski_style": meta.get("ski_style") or classify_ski_style(meta.get("name") or path.stem),
            "location": "",
            "region_name": None,
            "region_id": None,
            "configured_region": None,
            "lat": points[0][1],
            "lon": points[0][0],
            "date": date_text,
            "season_year": season_year(date_text),
            "summary": "",
            "image": "",
            "post_url": "",
            "distance_mi": source_distance_mi if source_distance_mi is not None else round(stats["distance_mi"], 2),
            "elevation_ft": source_elevation_ft if source_elevation_ft is not None else round(stats["gain_ft"]),
            "grade": "",
            "pitches": None,
            "source": "strava",
        }
        if cache is not None:
            cache[key] = {"sig": signature, "record": stable_private_record(record)}
        records.append(record)
        parsed += 1
        if parsed % 250 == 0:
            log(f"  parsed {parsed:,}/{len(files):,} track files")

    if cache is not None and prune_cache:
        for key in list(cache):
            if key not in active_keys:
                del cache[key]
    log(f"Strava tracks: {reused} cached, {parsed} newly parsed")
    return records


def attach_ski_styles(
    records: List[Dict[str, Any]], strava_dir: Path, source: Optional[str] = None
) -> int:
    """Add ski style from activities.csv without re-parsing cached tracks."""
    metadata = read_strava_metadata(strava_dir)
    if not metadata:
        return 0
    attached = 0
    for record in records:
        if record.get("activity") != "ski" or record.get("ski_style"):
            continue
        if source and record.get("source") != source:
            continue
        slug = str(record.get("slug") or "")
        candidates = [slug, slug.split(".")[0], slug.replace(".fit", "").replace(".gpx", "")]
        for key in candidates:
            meta = metadata.get(key)
            if meta and meta.get("ski_style"):
                record["ski_style"] = meta["ski_style"]
                attached += 1
                break
        if not record.get("ski_style"):
            record["ski_style"] = classify_ski_style(str(record.get("title") or slug))
    return attached


def load_garmin_activities(
    garmin_dir: Path,
    cache: Optional[Dict[str, Any]] = None,
    prune_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Read Garmin FIT/GPX exports, reusing cached summaries for unchanged files.

    The sync layer writes Garmin tracks into ``private/garmin_export`` with an
    ``activities.csv`` that mirrors Strava's column names, so the same parser
    can be reused. The cache is separate from Strava's.
    """
    if not garmin_dir.exists():
        warn(f"Garmin directory not found: {garmin_dir}")
        return []
    metadata = read_garmin_metadata(garmin_dir)
    files = discover_track_files(garmin_dir)
    log(f"discovered {len(files):,} Garmin track files")
    records: List[Dict[str, Any]] = []
    active_keys = set()
    reused = 0
    parsed = 0

    for path in files:
        key = str(path.resolve())
        active_keys.add(key)
        meta = metadata.get(path.name) or metadata.get(path.stem.split(".")[0]) or {}
        signature = "|".join(
            [file_signature(path), str(CACHE_UNITS_VERSION), metadata_signature(meta)]
        )
        cached = cache.get(key) if cache is not None else None
        if (
            isinstance(cached, dict)
            and cached.get("sig") == signature
            and isinstance(cached.get("record"), dict)
        ):
            records.append(stable_private_record(cached["record"]))
            reused += 1
            continue

        if path.name.lower().endswith((".fit", ".fit.gz")):
            points, fit_sport = read_fit(path)
        else:
            points = read_gpx(path)
            fit_sport = ""
        if len(points) < 2:
            if cache is not None:
                cache.pop(key, None)
            continue
        stats = track_stats(points)
        source_distance_mi, source_elevation_ft = source_imperial_metrics(meta)
        date_text = meta.get("date") or ""
        if not date_text:
            timestamps = [point[3] for point in points if point[3] is not None]
            if timestamps:
                date_text = timestamps[0].date().isoformat()
        sport = meta.get("sport") or normalize_sport(fit_sport) or "other"
        record = {
            "slug": path.stem,
            "title": meta.get("name") or path.stem,
            "activity": sport,
            "ski_style": meta.get("ski_style") or classify_ski_style(meta.get("name") or path.stem),
            "location": "",
            "region_name": None,
            "region_id": None,
            "configured_region": None,
            "lat": points[0][1],
            "lon": points[0][0],
            "date": date_text,
            "season_year": season_year(date_text),
            "summary": "",
            "image": "",
            "post_url": "",
            "distance_mi": source_distance_mi if source_distance_mi is not None else round(stats["distance_mi"], 2),
            "elevation_ft": source_elevation_ft if source_elevation_ft is not None else round(stats["gain_ft"]),
            "grade": "",
            "pitches": None,
            "source": "garmin",
        }
        if cache is not None:
            cache[key] = {"sig": signature, "record": stable_private_record(record)}
        records.append(record)
        parsed += 1

    if cache is not None and prune_cache:
        for key in list(cache):
            if key not in active_keys:
                del cache[key]
    log(f"Garmin tracks: {reused} cached, {parsed} newly parsed")
    return records


def dedupe_garmin_primary(
    strava_records: List[Dict[str, Any]], garmin_records: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], int]:
    """Drop Strava duplicates of Garmin activities (Garmin is authoritative).

    A match is the same local date, the same atlas activity filter, and a track
    distance within 12% (or half a mile, whichever is larger). Dates can differ
    by a few seconds at midnight, which this deliberately tolerates.
    """
    if not garmin_records or not strava_records:
        return strava_records, 0
    keep: List[Dict[str, Any]] = []
    removed = 0
    for strava in strava_records:
        duplicate = False
        for garmin in garmin_records:
            if strava.get("date") != garmin.get("date"):
                continue
            if strava.get("activity") != garmin.get("activity"):
                continue
            distance_a = to_float(strava.get("distance_mi"))
            distance_b = to_float(garmin.get("distance_mi"))
            if distance_a is None or distance_b is None:
                continue
            tolerance = max(0.5, 0.12 * max(abs(distance_a), abs(distance_b)))
            if abs(distance_a - distance_b) <= tolerance:
                duplicate = True
                break
        if duplicate:
            removed += 1
        else:
            keep.append(strava)
    if removed:
        log(f"Garmin primary de-duplication: dropped {removed} overlapping Strava activity(ies)")
    return keep, removed


def load_mp_ticks(
    csv_path: Path,
    crag_coords: Dict[str, Tuple[float, float]],
    cache: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Read a Mountain Project ticks export and attach public crag coordinates.

    The export has a full ``Location`` hierarchy but no lat/long, so each row is
    matched against ``scripts/crag_coords.json`` by the longest hierarchy suffix
    (then by leaf crag name). Only counts leave this function; notes, ratings,
    and route URLs stay in the private CSV. Unchanged rows are reused from the
    incremental cache; a change to the coordinate file invalidates the cache.
    """
    if not csv_path.exists():
        warn(f"Mountain Project CSV not found: {csv_path}")
        return []

    coords_sig = text_hash(json.dumps(crag_coords, sort_keys=True, ensure_ascii=False))
    rows_cache: Dict[str, Any] = {}
    if cache is not None:
        if cache.get("coords_sig") != coords_sig:
            cache["coords_sig"] = coords_sig
            cache["rows"] = {}
        rows_cache = cache.setdefault("rows", {})

    normalized: Dict[str, Tuple[float, float]] = {}
    for key, value in crag_coords.items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                normalized[str(key).strip().lower()] = (float(value[0]), float(value[1]))
            except (TypeError, ValueError):
                continue

    def resolve_crag(location: str) -> Optional[Tuple[float, float]]:
        parts = [part.strip() for part in str(location or "").split(">") if part.strip()]
        for start in range(len(parts)):
            key = " > ".join(parts[start:]).lower()
            if key in normalized:
                return normalized[key]
        for part in reversed(parts):
            if part.lower() in normalized:
                return normalized[part.lower()]
        return None

    records: List[Dict[str, Any]] = []
    active_hashes = set()
    reused = 0
    parsed = 0
    with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lower = {
                str(key).strip().lower(): (value or "").strip() if isinstance(value, str) else ""
                for key, value in row.items()
                if key
            }
            # Hash only fields that affect the published aggregates. Notes,
            # personal stars, and URLs can change in a refreshed export without
            # forcing a reparse.
            cache_key_fields = (
                "route", "route name", "climb", "name", "date", "ascent date",
                "tick date", "location", "crag", "area", "latitude", "lat",
                "longitude", "lon", "lng", "rating", "yds", "grade", "length",
                "pitches", "pitch count", "pitch", "route type", "type", "style",
            )
            canonical = json.dumps(
                {name: lower.get(name, "") for name in cache_key_fields},
                sort_keys=True,
                ensure_ascii=False,
            )
            row_hash = text_hash(canonical)
            active_hashes.add(row_hash)
            cached = rows_cache.get(row_hash)
            if isinstance(cached, dict) and cache is not None:
                records.append(stable_private_record(cached))
                reused += 1
                continue

            def field(*names: str) -> str:
                for name in names:
                    if lower.get(name):
                        return lower[name]
                return ""

            name = field("route", "route name", "climb", "name")
            if not name:
                continue
            lat = to_float(field("latitude", "lat"))
            lon = to_float(field("longitude", "lon", "lng"))
            location = field("location", "crag", "area")
            crag_parts = [part.strip() for part in location.split(">") if part.strip()]
            if lat is None or lon is None:
                coords = resolve_crag(location)
                if coords:
                    lon, lat = coords
            if lat is None or lon is None:
                warn(f"no coordinates for tick '{name}' ({location}); add the crag to scripts/crag_coords.json")
                continue
            date_text = normalize_date(field("date", "ascent date", "tick date"))
            grade = field("rating", "yds", "grade")
            length = field("length")
            length_ft = None
            length_match = re.search(r"([\d.]+)", length)
            if length_match:
                length_ft = to_int(length_match.group(1))
            record = {
                "slug": slugify(f"{name}-{date_text or 'tick'}"),
                "title": name,
                "activity": "climb",
                "location": crag_parts[-1] if crag_parts else location,
                "crag": location,
                "region_name": None,
                "region_id": None,
                "configured_region": None,
                "lat": lat,
                "lon": lon,
                "date": date_text,
                "season_year": season_year(date_text),
                "summary": "",
                "image": "",
                "post_url": "",
                "distance_mi": None,
                "elevation_ft": None,
                "grade": grade,
                "pitches": to_int(field("pitches", "pitch count", "pitch")),
                "route_type": field("route type", "type"),
                "style": field("style"),
                "route_length_ft": length_ft,
                "source": "mountain-project",
            }
            if cache is not None:
                rows_cache[row_hash] = stable_private_record(record)
            records.append(record)
            parsed += 1

    if cache is not None:
        for row_hash in list(rows_cache):
            if row_hash not in active_hashes:
                del rows_cache[row_hash]
    log(f"Mountain Project ticks: {reused} cached, {parsed} newly parsed")
    return records


# ---------------------------------------------------------------------------
# Watershed geometry
# ---------------------------------------------------------------------------


def fetch_cached_file(url: str, destination: Path, download: bool) -> Path:
    """Download a public data file once into the local cache."""
    if destination.exists():
        return destination
    if not download:
        sys.exit(
            f"cached data file missing: {destination}\n"
            "Re-run with --download-watersheds, or pass --watersheds /path/to/file.shp"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url} -> {destination}")
    request = urllib.request.Request(url, headers={"User-Agent": "klepikhina-atlas/1.0"})
    with urllib.request.urlopen(request, timeout=300, context=ssl_context()) as response:
        with tempfile.NamedTemporaryFile(delete=False, dir=destination.parent) as tmp:
            shutil.copyfileobj(response, tmp)
            temporary = Path(tmp.name)
    temporary.replace(destination)
    return destination


def ensure_watershed_file(source_url: str, shapefile_name: str, cache_dir: Path, download: bool) -> Path:
    shapefile = cache_dir / shapefile_name
    if shapefile.exists():
        return shapefile
    archive = cache_dir / Path(source_url).name
    fetch_cached_file(source_url, archive, download)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(cache_dir)
    if not shapefile.exists():
        candidates = list(cache_dir.glob("*.shp"))
        if not candidates:
            sys.exit(f"could not find a .shp after extracting {archive}")
        shapefile = candidates[0]
    return shapefile


def ensure_watershed_shapefile(config: Dict[str, Any], cache_dir: Path, download: bool) -> Path:
    watershed = config["watershed"]
    return ensure_watershed_file(
        watershed["source_url"], watershed["shapefile"], cache_dir, download
    )


def ensure_global_watersheds(
    config: Dict[str, Any], cache_dir: Path, download: bool
) -> List[Path]:
    """Return one level-8 shapefile per configured continent."""
    watershed = config["watershed"]
    template = watershed.get("source_url_template")
    if not template:
        return [ensure_watershed_shapefile(config, cache_dir, download)]
    names = watershed.get("shapefile_template") or Path(template).name.replace(
        "{code}", "na"
    ).replace(".zip", ".shp")
    paths: List[Path] = []
    for code in watershed.get("continents", []):
        url = template.format(code=code, level=watershed.get("level", 8))
        name = names.format(code=code, level=watershed.get("level", 8))
        paths.append(ensure_watershed_file(url, name, cache_dir, download))
    return paths


def ensure_country_polygons(config: Dict[str, Any], cache_dir: Path, download: bool) -> Path:
    """Natural Earth admin-0 countries, used to name regions outside the blog."""
    name = config["watershed"].get("countries_geojson", "ne_110m_admin_0_countries.geojson")
    destination = cache_dir / name
    if destination.exists():
        return destination
    url = (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
        "ne_110m_admin_0_countries.geojson"
    )
    return fetch_cached_file(url, destination, download)


def load_global_basins(paths: List[Path]) -> "gpd.GeoDataFrame":
    frames = []
    for path in paths:
        log(f"reading watersheds: {path.name}")
        frame = gpd.read_file(path, columns=["HYBAS_ID", "geometry"])
        if frame.crs is None:
            frame = frame.set_crs(DEFAULT_CRS)
        elif str(frame.crs).upper() != DEFAULT_CRS:
            frame = frame.to_crs(DEFAULT_CRS)
        frames.append(frame)
    basins = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs=DEFAULT_CRS
    )
    basins["HYBAS_ID"] = basins["HYBAS_ID"].astype("int64")
    log(f"global watersheds: {len(basins):,} level-8 basins")
    return basins


def ensure_admin1_polygons(config: Dict[str, Any], cache_dir: Path, download: bool) -> Path:
    """Natural Earth states/provinces, used to expand blog regions globally."""
    name = config["watershed"].get(
        "admin1_geojson", "ne_50m_admin_1_states_provinces.geojson"
    )
    destination = cache_dir / name
    if destination.exists():
        return destination
    url = (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
        "ne_50m_admin_1_states_provinces.geojson"
    )
    return fetch_cached_file(url, destination, download)


def load_country_polygons(path: Path) -> "gpd.GeoDataFrame":
    countries = gpd.read_file(path)
    if countries.crs is None:
        countries = countries.set_crs(DEFAULT_CRS)
    elif str(countries.crs).upper() != DEFAULT_CRS:
        countries = countries.to_crs(DEFAULT_CRS)
    return countries[["NAME", "geometry"]].copy()


def load_admin1_polygons(path: Path) -> "gpd.GeoDataFrame":
    admin1 = gpd.read_file(path)
    if admin1.crs is None:
        admin1 = admin1.set_crs(DEFAULT_CRS)
    elif str(admin1.crs).upper() != DEFAULT_CRS:
        admin1 = admin1.to_crs(DEFAULT_CRS)
    return admin1[["name", "admin", "geometry"]].copy()


def build_admin_polygons(
    admin1: "gpd.GeoDataFrame", countries: "gpd.GeoDataFrame"
) -> "gpd.GeoDataFrame":
    """Display regions: US/Canada states and provinces, countries elsewhere.

    A state whose name is also a country (Georgia) gets a short suffix so the
    two never share a region id. Only public Natural Earth geometry is used.
    """
    country_names_set = {str(name) for name in countries["NAME"].dropna()}
    rows: List[Dict[str, Any]] = []
    for _, row in admin1.iterrows():
        country = str(row.get("admin") or "")
        if country not in {"United States of America", "Canada"}:
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        if name in country_names_set:
            suffix = "US" if country == "United States of America" else "CA"
            name = f"{name} ({suffix})"
        rows.append({
            "region_id": slugify(name),
            "region_name": name,
            "region_kind": "state" if country == "United States of America" else "province",
            "country": country,
            "geometry": row.geometry,
        })
    for _, row in countries.iterrows():
        name = str(row.get("NAME") or "").strip()
        if not name or name in {"United States of America", "Canada"}:
            continue
        rows.append({
            "region_id": slugify(name),
            "region_name": name,
            "region_kind": "country",
            "country": name,
            "geometry": row.geometry,
        })
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=DEFAULT_CRS)
    return frame.drop_duplicates(subset="region_id", keep="first").reset_index(drop=True)


def assign_admin_regions(records: List[Dict[str, Any]], polygons: "gpd.GeoDataFrame") -> int:
    """Attach a Natural Earth state/province/country to every located record."""
    located = [
        record for record in records
        if record.get("lon") is not None and record.get("lat") is not None
    ]
    if not located:
        return 0
    points = gpd.GeoDataFrame(
        {"position": range(len(located))},
        geometry=[Point(record["lon"], record["lat"]) for record in located],
        crs=DEFAULT_CRS,
    )
    columns = ["region_id", "region_name", "region_kind", "geometry"]
    joined = gpd.sjoin(points, polygons[columns], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]

    assigned = 0
    for position, row in joined.iterrows():
        if isinstance(row.get("region_id"), str) and row["region_id"]:
            located[int(position)]["region_id"] = row["region_id"]
            located[int(position)]["region_name"] = row["region_name"]
            assigned += 1

    # A few points sit exactly on a coast or border; retry with intersection.
    missing = joined[joined["region_id"].isna()]
    if len(missing):
        retry = gpd.sjoin(
            missing[["position", "geometry"]], polygons[columns], how="left", predicate="intersects"
        )
        retry = retry[~retry.index.duplicated(keep="first")]
        for position, row in retry.iterrows():
            if isinstance(row.get("region_id"), str) and row["region_id"]:
                located[int(position)]["region_id"] = row["region_id"]
                located[int(position)]["region_name"] = row["region_name"]
                assigned += 1
    log(f"admin regions: {assigned:,} of {len(located):,} located activities assigned")
    return assigned


def country_names(value: str) -> List[str]:
    """Split multi-country front matter like 'France/Italy/Switzerland'."""
    parts = re.split(r"[,/]| and ", str(value or ""), flags=re.IGNORECASE)
    names = []
    for part in parts:
        name = part.strip()
        if not name:
            continue
        if name.upper() in {"USA", "US", "U.S.", "UNITED STATES"}:
            name = "United States of America"
        names.append(name)
    return names


def blog_region_boundaries(
    posts: List[Dict[str, Any]],
    admin1: "gpd.GeoDataFrame",
    countries: "gpd.GeoDataFrame",
) -> "gpd.GeoDataFrame":
    """One coarse boundary per blog label: state/province or country."""
    rows: List[Dict[str, Any]] = []
    labels: Dict[str, List[str]] = {}
    for post in posts:
        label = post.get("configured_region")
        if label:
            labels.setdefault(label, []).append(str(post.get("country") or ""))

    for label, country_values in labels.items():
        parts: List[Any] = []
        for value in country_values:
            for country in country_names(value):
                if country in {"United States of America", "Canada"}:
                    matches = admin1[(admin1["name"] == label) & (admin1["admin"] == country)]
                    if matches.empty:
                        matches = countries[countries["NAME"] == country]
                else:
                    matches = countries[countries["NAME"] == country]
                if not matches.empty:
                    parts.extend(list(matches.geometry))
        if not parts:
            continue
        geometry = unary_union(parts)
        if geometry.is_empty:
            continue
        rows.append({"region_name": label, "geometry": geometry})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=DEFAULT_CRS)


def country_for_records(
    records: List[Dict[str, Any]],
    countries: "gpd.GeoDataFrame",
    label_key: str = "configured_region",
) -> int:
    """Attach a Natural Earth country name to records with coordinates."""
    located = [
        record for record in records
        if not record.get(label_key)
        and record.get("lon") is not None
        and record.get("lat") is not None
    ]
    if not located:
        return 0
    points = gpd.GeoDataFrame(
        {"position": range(len(located))},
        geometry=[Point(record["lon"], record["lat"]) for record in located],
        crs=DEFAULT_CRS,
    )
    joined = gpd.sjoin(points, countries, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    assigned = 0
    for position, row in joined.iterrows():
        name = row.get("NAME")
        if isinstance(name, str) and name:
            located[int(position)][label_key] = name
            assigned += 1
    return assigned


def regions_from_labeled_points(
    records: List[Dict[str, Any]],
    basins: "gpd.GeoDataFrame",
    simplify_deg: float,
    label_key: str = "configured_region",
) -> Tuple["gpd.GeoDataFrame", List[int]]:
    """Union the basins containing each label's points into one region polygon."""
    located = [
        (position, record)
        for position, record in enumerate(records)
        if record.get(label_key)
        and record.get("lon") is not None
        and record.get("lat") is not None
    ]
    if not located:
        return gpd.GeoDataFrame(columns=["region_id", "region_name", "basin_count", "geometry"], crs=DEFAULT_CRS), []
    positions = [position for position, _ in located]
    points = gpd.GeoDataFrame(
        {"position": positions, "label": [record[label_key] for _, record in located]},
        geometry=[Point(record["lon"], record["lat"]) for _, record in located],
        crs=DEFAULT_CRS,
    )
    joined = gpd.sjoin(points, basins[["HYBAS_ID", "geometry"]], how="left", predicate="within")
    groups: Dict[str, set] = {}
    matched_positions = set()
    for _, row in joined.iterrows():
        hybas_id = row.get("HYBAS_ID")
        if pd.isna(hybas_id):
            continue
        groups.setdefault(str(row["label"]), set()).add(int(hybas_id))
        matched_positions.add(int(row["position"]))

    rows = []
    for label, ids in groups.items():
        subset = basins[basins["HYBAS_ID"].isin(ids)]
        geometry = unary_union(list(subset.geometry))
        if geometry.is_empty:
            continue
        if simplify_deg > 0:
            geometry = geometry.simplify(simplify_deg, preserve_topology=True)
        rows.append(
            {
                "region_id": slugify(label),
                "region_name": label,
                "basin_count": len(ids),
                "geometry": geometry,
            }
        )
    if not rows:
        return gpd.GeoDataFrame(columns=["region_id", "region_name", "basin_count", "geometry"], crs=DEFAULT_CRS), [
            position for position in positions if position not in matched_positions
        ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=DEFAULT_CRS), [
        position for position in positions if position not in matched_positions
    ]


def build_global_regions(
    posts: List[Dict[str, Any]],
    private_records: List[Dict[str, Any]],
    basins: "gpd.GeoDataFrame",
    countries: "gpd.GeoDataFrame",
    admin1: "gpd.GeoDataFrame",
    simplify_deg: float,
) -> "gpd.GeoDataFrame":
    """Derive regions from blog labels, then country regions for the rest.

    Blog labels such as California or British Columbia are expanded with the
    Natural Earth state/province boundary, so every private activity in that
    state contributes basins to the same watershed region. This is what makes
    the choropleth cover the whole region instead of just the blog locations.
    """
    boundaries = blog_region_boundaries(posts, admin1, countries)
    labeled: List[Dict[str, Any]] = list(posts)
    if not boundaries.empty and private_records:
        points = gpd.GeoDataFrame(
            {"position": range(len(private_records))},
            geometry=[Point(record["lon"], record["lat"]) for record in private_records],
            crs=DEFAULT_CRS,
        )
        joined = gpd.sjoin(
            points,
            boundaries[["region_name", "geometry"]],
            how="left",
            predicate="within",
        )
        joined = joined[~joined.index.duplicated(keep="first")]
        matched = 0
        for position, row in joined.iterrows():
            label = row.get("region_name")
            if isinstance(label, str) and label:
                private_records[int(position)]["configured_region"] = label
                matched += 1
        log(f"state/province boundaries: {matched:,} private activities joined to blog regions")
        labeled.extend(
            [record for record in private_records if record.get("configured_region")]
        )

    post_regions, _ = regions_from_labeled_points(labeled, basins, simplify_deg)
    log(f"blog regions: {len(post_regions)} from post labels and their activities")

    # A label with no basin match at all falls back to its country.
    built_labels = set(post_regions["region_name"])
    fallback_posts = [
        post for post in posts if post.get("configured_region") not in built_labels
    ]
    for post in fallback_posts:
        post["configured_region"] = None
        country_for_records([post], countries)

    outside: List[Dict[str, Any]] = []
    if private_records:
        assign_regions(private_records, post_regions, max_snap_deg=0)
        outside = [record for record in private_records if not record.get("region_id")]
        country_for_records(outside, countries)
    outside_records = outside + [post for post in fallback_posts if post.get("configured_region")]
    country_regions, _ = regions_from_labeled_points(
        outside_records, basins, simplify_deg
    )
    log(f"country regions for remaining activity: {len(country_regions)}")

    if country_regions.empty:
        return post_regions

    # Country basins can overlap the expanded blog regions. Remove that overlap
    # so a watershed is never shaded twice with two different counts.
    covered = unary_union(list(post_regions.geometry))
    kept = []
    for _, row in country_regions.iterrows():
        geometry = row.geometry.difference(covered)
        if geometry.is_empty or geometry.area <= 0:
            continue
        kept.append(
            {
                "region_id": row["region_id"],
                "region_name": row["region_name"],
                "basin_count": row["basin_count"],
                "geometry": geometry,
            }
        )
    if not kept:
        return post_regions
    country_regions = gpd.GeoDataFrame(kept, geometry="geometry", crs=DEFAULT_CRS)

    regions = gpd.GeoDataFrame(
        pd.concat([post_regions, country_regions], ignore_index=True),
        geometry="geometry",
        crs=DEFAULT_CRS,
    )
    return regions.drop_duplicates(subset="region_name", keep="first").reset_index(drop=True)


def dissolve_regions(
    shapefile: Path,
    config: Dict[str, Any],
    simplify_deg: float,
) -> "gpd.GeoDataFrame":
    log(f"reading watersheds: {shapefile}")
    watersheds = gpd.read_file(shapefile)
    if watersheds.crs is None:
        watersheds = watersheds.set_crs(DEFAULT_CRS)
    elif str(watersheds.crs).upper() != DEFAULT_CRS:
        watersheds = watersheds.to_crs(DEFAULT_CRS)
    if "HYBAS_ID" not in watersheds.columns:
        sys.exit("watershed file has no HYBAS_ID column; pass a HydroBASINS file")

    rows = []
    for region in config["regions"]:
        wanted = {int(value) for value in region["basin_ids"]}
        subset = watersheds[watersheds["HYBAS_ID"].astype("int64").isin(wanted)]
        if subset.empty:
            warn(f"no basins matched for {region['region_name']}")
            continue
        geometry = unary_union(list(subset.geometry))
        if geometry.is_empty:
            warn(f"empty geometry for {region['region_name']}")
            continue
        if simplify_deg > 0:
            geometry = geometry.simplify(simplify_deg, preserve_topology=True)
        rows.append(
            {
                "region_id": region["region_id"],
                "region_name": region["region_name"],
                "basin_count": len(subset),
                "geometry": geometry,
            }
        )
    if not rows:
        sys.exit("no regions could be built from the watershed file")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=DEFAULT_CRS)


def load_regions_geometry(path: Path) -> "gpd.GeoDataFrame":
    data = gpd.read_file(path)
    if data.crs is None:
        data = data.set_crs(DEFAULT_CRS)
    elif str(data.crs).upper() != DEFAULT_CRS:
        data = data.to_crs(DEFAULT_CRS)
    keep = [column for column in ("region_id", "region_name", "basin_count") if column in data.columns]
    return data[keep + ["geometry"]].copy()


# ---------------------------------------------------------------------------
# Spatial join + aggregation
# ---------------------------------------------------------------------------


def assign_regions(
    records: List[Dict[str, Any]],
    regions: "gpd.GeoDataFrame",
    max_snap_deg: float = 0.08,
) -> List[Dict[str, Any]]:
    """Point-in-polygon join, with a small edge snap and a curated fallback.

    Records outside the configured watersheds are dropped rather than counted
    against a nearby region: this atlas covers four watersheds, not the globe.
    """
    region_ids = {row["region_name"]: row["region_id"] for _, row in regions.iterrows()}
    located = [record for record in records if record.get("lon") is not None and record.get("lat") is not None]
    if not located:
        return records

    points = gpd.GeoDataFrame(
        {"position": range(len(located))},
        geometry=[Point(record["lon"], record["lat"]) for record in located],
        crs=DEFAULT_CRS,
    )
    joined = gpd.sjoin(points, regions[["region_id", "region_name", "geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]

    assigned = 0
    for position, row in joined.iterrows():
        region_id = row.get("region_id")
        if isinstance(region_id, str) and region_id:
            located[int(position)]["region_id"] = region_id
            located[int(position)]["region_name"] = row.get("region_name")
            assigned += 1

    missing = joined[joined["region_id"].isna()]
    if len(missing):
        buffered = regions.to_crs("EPSG:3857")
        buffered["geometry"] = buffered.geometry.buffer(max_snap_deg * 111_320)
        buffered = buffered.to_crs(DEFAULT_CRS)
        snapped = gpd.sjoin(
            missing[["position", "geometry"]],
            buffered[["region_id", "region_name", "geometry"]],
            how="left",
            predicate="within",
        )
        snapped = snapped[~snapped.index.duplicated(keep="first")]
        for position, row in snapped.iterrows():
            region_id = row.get("region_id")
            if isinstance(region_id, str) and region_id:
                located[int(position)]["region_id"] = region_id
                located[int(position)]["region_name"] = row.get("region_name")
                assigned += 1

    # A curated post location is authoritative for the sample atlas.
    for record in records:
        configured = record.get("configured_region")
        if not record.get("region_id") and configured in region_ids:
            record["region_name"] = configured
            record["region_id"] = region_ids[configured]

    scoped = [record for record in records if record.get("region_id")]
    log(f"spatial join: {assigned} of {len(located)} located activities matched a region; {len(scoped)} in scope")
    return scoped


def merge_posts_with_private(
    posts: List[Dict[str, Any]],
    private_records: List[Dict[str, Any]],
    include_all_posts: bool = False,
) -> List[Dict[str, Any]]:
    """Combine public posts with private records without obvious double counts.

    Public posts are the baseline sample atlas. Once private exports exist they
    are authoritative for the same day and activity, so a blog post covering an
    outing already present in Strava or Mountain Project is dropped. Posts for
    other days or sports remain. Pass ``include_all_posts`` to keep every post.
    """
    if not private_records:
        return posts
    if include_all_posts:
        return private_records + posts

    occupied = {
        (record.get("date"), record.get("activity"))
        for record in private_records
        if record.get("date")
    }
    kept = [
        post
        for post in posts
        if (post.get("date"), post.get("activity")) not in occupied
    ]
    dropped = len(posts) - len(kept)
    if dropped:
        log(f"de-duplication: {dropped} blog post(s) covered by private exports")
    return private_records + kept


def aggregate_regions(
    regions: "gpd.GeoDataFrame",
    records: List[Dict[str, Any]],
    featured: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> "gpd.GeoDataFrame":
    sports_order = config.get("sports_order", [])
    area_frame = regions.to_crs(EQUAL_AREA_CRS)
    area_by_index = area_frame.geometry.area
    rows = []
    for index, region in regions.iterrows():
        region_id = region["region_id"]
        members = [record for record in records if record.get("region_id") == region_id]
        stories = [feature for feature in featured if feature["properties"].get("region_id") == region_id]
        elevations = finite_values(members, "elevation_ft")
        distances = finite_values(members, "distance_mi")
        departments = Counter(record.get("activity") or "other" for record in members)
        sports = [sport for sport in sports_order if departments.get(sport)]
        unknown = sorted(set(departments) - set(sports_order))
        sports.extend(unknown)

        by_sport: Dict[str, Any] = {}
        for sport in sports:
            group = [record for record in members if (record.get("activity") or "other") == sport]
            group_elevations = finite_values(group, "elevation_ft")
            group_distances = finite_values(group, "distance_mi")
            summary = {
                "trips": len(group),
                "vert_ft": int(round(sum(group_elevations))) if group_elevations else 0,
                "distance_mi": round(sum(group_distances), 1) if group_distances else 0,
                "vert_coverage": round(len(group_elevations) / len(group), 2) if group else 0,
                "distance_coverage": round(len(group_distances) / len(group), 2) if group else 0,
            }
            if sport == "ski":
                styles = Counter(record.get("ski_style") or "alpine" for record in group)
                style_counts = {style: styles.get(style, 0) for style in config.get("ski_styles", ["touring", "alpine", "nordic"])}
                total_styles = sum(style_counts.values()) or 1
                summary["styles"] = style_counts
                summary["style_pct"] = {
                    style: round(100.0 * count / total_styles) for style, count in style_counts.items()
                }
            by_sport[sport] = summary

        centroid = region.geometry.representative_point()
        area_km2 = float(area_by_index.loc[index]) / 1_000_000
        row = {
            "region_id": region_id,
            "region_name": region["region_name"],
            "region_kind": str(region.get("region_kind") or "region"),
            "activity_count": len(members),
            "total_vert_ft": int(round(sum(elevations))) if elevations else 0,
            "total_distance_mi": round(sum(distances), 1) if distances else 0,
            "sports": sports,
            "featured_story_count": len(stories),
            "vert_coverage": round(len(elevations) / len(members), 2) if members else 0,
            "distance_coverage": round(len(distances) / len(members), 2) if members else 0,
            "departments": {sport: departments.get(sport, 0) for sport in sports},
            "stats": by_sport,
            "top_stories": [
                {
                    "title": story["properties"]["title"],
                    "post_url": story["properties"]["post_url"],
                    "activity": story["properties"]["activity"],
                }
                for story in stories[:3]
            ],
            "area_km2": round(area_km2),
            "centroid": [round(centroid.x, 4), round(centroid.y, 4)],
            "geometry": region.geometry,
        }
        # Flattened keys are what MapLibre expressions read for filter-aware
        # shading; the nested `stats` object stays human-readable.
        # Emit every sport's keys (zero when absent) so pandas does not insert
        # NaN columns for regions that lack a sport.
        all_sports = list(sports_order) + sorted(set(departments) - set(sports_order))
        for sport in all_sports:
            prefix = sport + "_"
            summary = by_sport.get(sport)
            row[prefix + "trips"] = summary["trips"] if summary else 0
            row[prefix + "vert_ft"] = summary["vert_ft"] if summary else 0
            row[prefix + "distance_mi"] = summary["distance_mi"] if summary else 0
            if sport == "ski":
                for style in config.get("ski_styles", ["touring", "alpine", "nordic"]):
                    row["ski_" + style + "_pct"] = (
                        summary["style_pct"].get(style, 0) if summary else 0
                    )
                    row["ski_" + style + "_count"] = (
                        summary["styles"].get(style, 0) if summary else 0
                    )
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=DEFAULT_CRS)


def build_density_grid(
    records: List[Dict[str, Any]], config: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Coarsen activity locations into an anonymised density grid.

    Only cell totals are emitted: no individual coordinates, timestamps, file
    names, or route geometry. Sparse private cells are suppressed; public blog
    locations are always kept so their stories remain findable.
    """
    watershed = config["watershed"]
    cell = float(watershed.get("density_cell_deg", 0.5))
    min_count = int(watershed.get("density_min_count", 2))
    sports = list(config.get("sports_order", []))
    styles = list(config.get("ski_styles", ["touring", "alpine", "nordic"]))

    buckets: Dict[Tuple[int, int], Counter] = {}
    public_cells: set = set()
    for record in records:
        lon, lat = record.get("lon"), record.get("lat")
        if lon is None or lat is None:
            continue
        key = (math.floor(float(lon) / cell), math.floor(float(lat) / cell))
        bucket = buckets.setdefault(key, Counter())
        bucket["count"] += 1
        sport = record.get("activity") or "other"
        bucket[sport + "_count"] += 1
        for total_key, source_key in (("vert_ft", "elevation_ft"), ("distance_mi", "distance_mi")):
            value = record.get(source_key)
            if value is None or isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                bucket[sport + "_" + total_key] += number
                bucket[total_key] += number
        if sport == "ski":
            bucket["ski_" + str(record.get("ski_style") or "alpine") + "_count"] += 1
        if record.get("source") == "blog":
            public_cells.add(key)

    features = []
    for key, bucket in buckets.items():
        total = bucket["count"]
        if total < min_count and key not in public_cells:
            continue
        i, j = key
        properties: Dict[str, Any] = {
            "cell_id": f"{i}:{j}",
            "count": total,
            "cell_deg": cell,
        }
        for total_key in ("vert_ft", "distance_mi"):
            properties[total_key] = round(bucket.get(total_key, 0), 1)
        for sport in sports + ["other"]:
            properties[sport + "_count"] = bucket.get(sport + "_count", 0)
            for total_key in ("vert_ft", "distance_mi"):
                properties[sport + "_" + total_key] = round(bucket.get(sport + "_" + total_key, 0), 1)
        for style in styles:
            properties["ski_" + style + "_count"] = bucket.get("ski_" + style + "_count", 0)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round((i + 0.5) * cell, 4), round((j + 0.5) * cell, 4)],
                },
                "properties": properties,
            }
        )

    grid = {
        "type": "FeatureCollection",
        "features": features,
    }
    return grid, {
        "cell_deg": cell,
        "min_count": min_count,
        "cells": len(features),
        "activities": sum(bucket["count"] for bucket in buckets.values()),
    }


# ---------------------------------------------------------------------------
# Featured routes
# ---------------------------------------------------------------------------


def trim_polyline(coords: List[Tuple[float, float]], trim_m: float) -> List[Tuple[float, float]]:
    if len(coords) < 3 or trim_m <= 0:
        return coords
    distances = [0.0]
    total = 0.0
    for first, second in zip(coords, coords[1:]):
        total += haversine_m(first, second)
        distances.append(total)
    if total <= 2 * trim_m:
        return coords
    start, end = trim_m, total - trim_m
    trimmed = [coord for coord, distance in zip(coords, distances) if start <= distance <= end]
    return trimmed if len(trimmed) >= 2 else coords


def build_featured_routes(
    posts: List[Dict[str, Any]],
    curated: Dict[str, Any],
    regions: "gpd.GeoDataFrame",
    blogged_tracks: Dict[str, List[Tuple[float, float]]],
    args: argparse.Namespace,
) -> List[Dict[str, Any]]:
    by_slug = {post["slug"]: post for post in posts}
    region_by_name = {row["region_name"]: row["region_id"] for _, row in regions.iterrows()}
    features: List[Dict[str, Any]] = []
    missing: List[str] = []

    for entry in curated.get("routes", []):
        slug = entry["slug"]
        post = by_slug.get(slug)
        if not post:
            missing.append(slug)
            continue
        region_name = entry.get("region_name") or post.get("region_name")
        region_id = region_by_name.get(region_name) or post.get("region_id")
        track = blogged_tracks.get(slug)

        distance = post.get("distance_mi")
        elevation = post.get("elevation_ft")
        geometry: Any
        source = "blog"
        if track:
            coords = trim_polyline(track, args.trim_meters)
            line = LineString(coords)
            if args.feature_simplify_deg > 0:
                line = line.simplify(args.feature_simplify_deg, preserve_topology=False)
            geometry = line
            source = "blog+gpx"
            if distance is None or elevation is None:
                stats = track_stats([(lon, lat, None, None) for lon, lat in coords])
                distance = distance if distance is not None else round(stats["distance_mi"], 2)
                elevation = elevation if elevation is not None else round(stats["gain_ft"])
        else:
            geometry = Point(post["lon"], post["lat"])

        grade = entry.get("grade") or post.get("grade") or ""
        features.append(
            {
                "type": "Feature",
                "geometry": geometry_dict(geometry, 5),
                "properties": {
                    "id": slugify(slug),
                    "slug": slug,
                    "title": entry.get("title") or post["title"],
                    "region_name": region_name,
                    "region_id": region_id,
                    "category": args.activity_labels.get(post["activity"], post["activity"].title()),
                    "activity": post["activity"],
                    "grade_or_difficulty": entry.get("difficulty") or difficulty_for(post["activity"], grade),
                    "grade": grade,
                    "pitches": post.get("pitches"),
                    "distance_mi": distance,
                    "elevation_ft": elevation,
                    "season_year": post.get("season_year"),
                    "date": post.get("date"),
                    "summary": entry.get("summary") or post.get("summary") or "",
                    "post_url": entry.get("post_url") or post.get("post_url") or "",
                    "image": post.get("image") or "",
                    "location": post.get("location") or "",
                    "source": source,
                },
            }
        )

    if missing:
        warn(f"curated slugs not found among posts: {', '.join(missing)}")
    return features


def load_blogged_tracks(manifest_path: Optional[Path], args: argparse.Namespace) -> Dict[str, List[Tuple[float, float]]]:
    if not manifest_path:
        return {}
    if not manifest_path.exists():
        warn(f"blogged manifest not found: {manifest_path}")
        return {}
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "routes" in data:
        entries = data["routes"]
    elif isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = [{"slug": slug, "gpx": gpx} for slug, gpx in data.items()]
    else:
        entries = []
    tracks: Dict[str, List[Tuple[float, float]]] = {}
    for entry in entries:
        slug = entry.get("slug") or Path(entry.get("gpx", "")).stem
        gpx_path = Path(entry.get("gpx", "")).expanduser()
        if not gpx_path.is_absolute():
            gpx_path = manifest_path.parent / gpx_path
        points = read_gpx(gpx_path)
        if len(points) < 3:
            warn(f"could not read blogged GPX for {slug}: {gpx_path}")
            continue
        tracks[slug] = [(point[0], point[1]) for point in points]
    return tracks


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def json_safe(value: Any) -> Any:
    """Replace non-finite floats so the committed GeoJSON is always valid."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def write_collection(
    path: Path,
    features: Iterable[Dict[str, Any]],
    metadata: Dict[str, Any],
    dry_run: bool,
) -> None:
    collection = json_safe({
        "type": "FeatureCollection",
        "metadata": metadata,
        "features": list(features),
    })
    # Keep generated_at stable when the published content is unchanged. Without
    # this, every scheduled sync would create an empty data commit and PR.
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            old_meta = dict(existing.get("metadata") or {})
            old_stamp = old_meta.pop("generated_at", None)
            new_meta = dict(collection["metadata"])
            new_stamp = new_meta.pop("generated_at", None)
            old_features = existing.get("features")
            new_features = collection["features"]
            features_equal = old_features == new_features
            if not features_equal:
                # Density cells are keyed by cell_id; their input order can vary
                # when a new source is added. Preserve the published order when
                # the keyed contents are identical.
                def by_cell_id(features: Any) -> Optional[Dict[str, Any]]:
                    if not isinstance(features, list):
                        return None
                    mapped: Dict[str, Any] = {}
                    for feature in features:
                        props = feature.get("properties") if isinstance(feature, dict) else None
                        cell_id = props.get("cell_id") if isinstance(props, dict) else None
                        if not isinstance(cell_id, str):
                            return None
                        mapped[cell_id] = feature
                    return mapped

                old_map = by_cell_id(old_features)
                new_map = by_cell_id(new_features)
                if old_map is not None and new_map is not None and old_map == new_map:
                    collection["features"] = old_features
                    features_equal = True
            if (
                existing.get("type") == collection["type"]
                and features_equal
                and old_meta == new_meta
            ):
                collection["metadata"]["generated_at"] = old_stamp or new_stamp
                if dry_run:
                    log(f"dry run: no data change for {path}")
                else:
                    log(f"unchanged {path}")
                return
    payload = json.dumps(collection, ensure_ascii=False, indent=1, allow_nan=False)
    if dry_run:
        log(f"dry run: would write {path} ({len(payload):,} bytes)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    log(f"wrote {path} ({len(payload):,} bytes)")


def write_json(
    path: Path, document: Dict[str, Any], dry_run: bool, compact: bool = False
) -> None:
    """Write a JSON document, preserving generated_at when content is unchanged."""
    payload_document = json_safe(document)
    if isinstance(payload_document.get("metadata"), dict) and path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            old_meta = dict(existing.get("metadata") or {})
            new_meta = dict(payload_document.get("metadata") or {})
            old_stamp = old_meta.pop("generated_at", None)
            new_stamp = new_meta.pop("generated_at", None)
            old_body = {key: value for key, value in existing.items() if key != "metadata"}
            new_body = {key: value for key, value in payload_document.items() if key != "metadata"}
            if old_body == new_body and old_meta == new_meta:
                payload_document["metadata"]["generated_at"] = old_stamp or new_stamp
                payload = json.dumps(
                    payload_document,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":") if compact else None,
                    indent=None if compact else 1,
                )
                if dry_run:
                    log(f"dry run: no data change for {path}")
                else:
                    log(f"unchanged {path}")
                return

    payload = json.dumps(
        payload_document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":") if compact else None,
        indent=None if compact else 1,
    )
    if dry_run:
        log(f"dry run: would write {path} ({len(payload):,} bytes)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    log(f"wrote {path} ({len(payload):,} bytes)")


def write_text_if_changed(path: Path, text: str, dry_run: bool) -> None:
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                if dry_run:
                    log(f"dry run: no page change for {path}")
                else:
                    log(f"unchanged {path}")
                return
        except OSError:
            pass
    if dry_run:
        log(f"dry run: would write {path} ({len(text):,} bytes)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log(f"wrote {path} ({len(text):,} bytes)")


def representative_coordinates(feature: Dict[str, Any]) -> List[float]:
    """One public point for a curated route, used only as a card-level hint."""
    points: List[Tuple[float, float]] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            points.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk((feature.get("geometry") or {}).get("coordinates"))
    if not points:
        return []
    return [
        round(sum(point[0] for point in points) / len(points), 5),
        round(sum(point[1] for point in points) / len(points), 5),
    ]


def build_site_activities(featured: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    activities: List[Dict[str, Any]] = []
    for feature in featured:
        props = feature["properties"]
        activity = str(props.get("activity") or "")
        activities.append({
            "slug": props.get("slug"),
            "id": props.get("id"),
            "title": props.get("title"),
            "date": props.get("date"),
            "activity": activity,
            "category": props.get("category") or activity.title(),
            "filter": activity + "_log" if activity else "",
            "region_id": props.get("region_id"),
            "region_name": props.get("region_name"),
            "location": props.get("location"),
            "image": props.get("image"),
            "excerpt": props.get("summary") or "",
            "summary": props.get("summary") or "",
            "post_url": props.get("post_url"),
            "coordinates": representative_coordinates(feature),
            "distance_mi": props.get("distance_mi"),
            "elevation_ft": props.get("elevation_ft"),
            "grade_or_difficulty": props.get("grade_or_difficulty"),
            "season_year": props.get("season_year"),
        })
    activities.sort(
        key=lambda item: (str(item.get("date") or ""), str(item.get("title") or "")),
        reverse=True,
    )
    return activities


def group_matches_region(region: Dict[str, Any], group: Dict[str, Any]) -> bool:
    group_ids = group.get("region_ids")
    if group_ids and region["id"] not in group_ids:
        return False
    kinds = group.get("region_kinds")
    if kinds and region["kind"] not in kinds:
        return False
    if region["id"] in group.get("exclude_region_ids", []):
        return False
    return True


def build_region_pages(
    region_features: List[Dict[str, Any]],
    config: Dict[str, Any],
    generated_at: str,
) -> Dict[str, Any]:
    individual: List[Dict[str, Any]] = []
    for feature in region_features:
        props = feature["properties"]
        region_id = str(props["region_id"])
        individual.append({
            "id": region_id,
            "name": str(props["region_name"]),
            "kind": str(props["region_kind"]),
            "description": f"Adventures in {props['region_name']}",
            "url": f"/region/{region_id}/",
            "nav_group": "International",
            "region_ids": [region_id],
            "primary_region_id": region_id,
            "activity_count": int(props.get("activity_count") or 0),
            "featured_story_count": int(props.get("featured_story_count") or 0),
            "is_aggregate": False,
        })

    config_groups = config.get("region_groups", [])
    nav_groups: List[Dict[str, Any]] = []
    aggregates: List[Dict[str, Any]] = []
    for group in config_groups:
        members = [region for region in individual if group_matches_region(region, group)]
        story_members = [region for region in members if region["featured_story_count"] > 0]
        if not story_members:
            continue
        group_id = str(group["id"])
        group_name = str(group["name"])
        for region in members:
            region["nav_group"] = group_name
        region_ids = [region["id"] for region in members]
        nav_groups.append({
            "id": group_id,
            "name": group_name,
            "nav_name": str(group.get("nav_name") or group_name),
            "url": f"/region/{group_id}/",
            "regions": story_members,
        })
        aggregates.append({
            "id": group_id,
            "name": group_name,
            "kind": "aggregate",
            "description": str(group.get("description") or f"{group_name} adventures"),
            "url": f"/region/{group_id}/",
            "nav_group": group_name,
            "region_ids": region_ids,
            "primary_region_id": region_ids[0],
            "activity_count": sum(region["activity_count"] for region in members),
            "featured_story_count": sum(region["featured_story_count"] for region in members),
            "is_aggregate": True,
        })

    # Only publish region pages for places with a real blog/story. Aggregate
    # pages survive when at least one member has a story.
    story_regions = [region for region in individual if region["featured_story_count"] > 0]
    pages = aggregates + story_regions
    return {
        "metadata": {
            "generated_by": "scripts/build_atlas.py",
            "generated_at": generated_at,
            "source": "data/regions.geojson and data/featured_routes.geojson",
        },
        "regions": pages,
        "nav_groups": nav_groups,
    }


def yaml_string(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def region_page_html(page: Dict[str, Any]) -> str:
    lines = [
        "---",
        "layout: region",
        f"title: {yaml_string(page['name'])}",
        f"description: {yaml_string(page['description'])}",
        f"region_id: {yaml_string(page['primary_region_id'])}",
        "region_ids:",
    ]
    lines.extend(f"  - {yaml_string(region_id)}" for region_id in page["region_ids"])
    lines.extend([
        f"activity_count: {int(page['activity_count'])}",
        f"featured_story_count: {int(page['featured_story_count'])}",
        f"permalink: {yaml_string(page['url'])}",
        "---",
        "",
    ])
    return "\n".join(lines)


def region_index_html(region_data: Dict[str, Any]) -> str:
    lines = [
        "---",
        "layout: default",
        "title: All Regions",
        "description: Browse every place in the adventure atlas",
        "permalink: /region/",
        "---",
        "",
        '<div class="page-header">',
        "  <h1>All Regions</h1>",
        '  <p class="page-description">Every region with a featured story, generated from the atlas.</p>',
        "</div>",
        "",
    ]
    for group in region_data["nav_groups"]:
        lines.append('<div class="section-header">')
        lines.append(f"  <h2><a href=\"{{{{ site.baseurl }}}}{group['url']}\">{group.get('nav_name') or group['name']}</a></h2>")
        lines.append("</div>")
        lines.append('<div class="region-link-grid">')
        for region in group["regions"]:
            lines.append(
                f'  <a href="{{{{ site.baseurl }}}}{region["url"]}">'
                f"{region['name']} <span>({region['featured_story_count']} stories)</span></a>"
            )
        lines.append("</div>")
        lines.append("")
    return "\n".join(lines)


def write_site_pages(region_data: Dict[str, Any], out_dir: Path, dry_run: bool) -> None:
    region_dir = REPO_ROOT / "region"
    expected = {f"{page['id']}.html" for page in region_data["regions"]} | {"index.html"}

    # Regions that lose their last story must not leave a stale empty page.
    for path in sorted(region_dir.glob("*.html")):
        if path.name in expected or not path.read_text().startswith("---\nlayout: region"):
            continue
        if dry_run:
            log(f"would remove stale region page: {path.relative_to(REPO_ROOT)}")
        else:
            path.unlink()

    for page in region_data["regions"]:
        write_text_if_changed(region_dir / f"{page['id']}.html", region_page_html(page), dry_run)
    write_text_if_changed(region_dir / "index.html", region_index_html(region_data), dry_run)


def validate(
    regions: "gpd.GeoDataFrame",
    featured: List[Dict[str, Any]],
    curated: Dict[str, Any],
) -> None:
    problems: List[str] = []
    for _, row in regions.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            problems.append(f"{row['region_name']}: empty geometry")
        if row["activity_count"] == 0:
            problems.append(f"{row['region_name']}: zero activities")
    if len(featured) != len(curated.get("routes", [])):
        problems.append(f"featured routes: built {len(featured)} of {len(curated.get('routes', []))}")
    if problems:
        for problem in problems:
            warn(problem)
    else:
        log(f"validation: {len(regions)} regions, {len(featured)} featured routes, all checks passed")


def validate_imperial_labels(
    posts: List[Dict[str, Any]],
    featured: List[Dict[str, Any]],
    region_features: List[Dict[str, Any]],
) -> None:
    """Fail the build if a published GPS-derived label reverts to metric units."""
    problems: List[str] = []

    def check(label: str, text: Any) -> None:
        match = METRIC_LABEL_RE.search(str(text or ""))
        if match:
            problems.append(f"{label}: {text!r} contains metric unit {match.group(0)!r}")

    for post in posts:
        slug = post.get("slug")
        check(f"post {slug} title", post.get("title"))
        check(f"post {slug} summary", post.get("summary"))
        check(f"post {slug} excerpt", post.get("excerpt"))
    for feature in featured:
        props = feature["properties"]
        title = str(props.get("title") or "")
        main, separator, detail = title.partition(" | ")
        check(f"featured {props.get('slug')} title", main)
        if separator:
            check(f"featured {props.get('slug')} detail", detail)
        check(f"featured {props.get('slug')} summary", props.get("summary"))
    for feature in region_features:
        for story in feature["properties"].get("top_stories", []):
            check(f"region {feature['properties'].get('region_id')} story", story.get("title"))

    if problems:
        sys.exit(
            "published GPS-derived labels must use imperial units:\n  "
            + "\n  ".join(problems[:20])
            + (f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else "")
        )
    log(f"validation: {len(posts)} post titles and {len(featured)} route labels are imperial")


def print_report(regions: "gpd.GeoDataFrame", featured: List[Dict[str, Any]]) -> None:
    log("")
    log(f"{'region':40} {'acts':>5} {'vert_ft':>9} {'stories':>8} {'sports'}")
    for _, row in regions.iterrows():
        log(
            f"{row['region_name'][:40]:40} {row['activity_count']:>5} "
            f"{row['total_vert_ft']:>9,} {row['featured_story_count']:>8} "
            f"{','.join(row['sports'])}"
        )
    log("")
    log(f"featured routes: {len(featured)}")
    for feature in featured:
        props = feature["properties"]
        log(
            f"  {props['activity']:5} {props['category']:6} {props['title'][:48]:48} "
            f"{props['grade_or_difficulty']}"
        )


# ---------------------------------------------------------------------------
# Privacy-safe insight aggregates
# ---------------------------------------------------------------------------


def cell_key_from_point(lon: Any, lat: Any, cell_deg: float) -> str:
    """Return the same coarse cell id used by activity_density.geojson."""
    if lon is None or lat is None:
        return ""
    try:
        ix = math.floor(float(lon) / cell_deg)
        iy = math.floor(float(lat) / cell_deg)
    except (TypeError, ValueError, OverflowError):
        return ""
    return f"{ix}_{iy}"


def cell_center(cell: str, cell_deg: float) -> Optional[Tuple[float, float]]:
    """Coarse cell centre (lon, lat) for optional weather lookups."""
    try:
        ix, iy = (int(part) for part in cell.split("_"))
    except (TypeError, ValueError):
        return None
    return ((ix + 0.5) * cell_deg, (iy + 0.5) * cell_deg)


def build_daily_insights(
    records: List[Dict[str, Any]], config: Dict[str, Any], generated_at: str
) -> Dict[str, Any]:
    """Collapse private activities into public per-day, per-sport totals.

    Only dates, coarse region ids, coarse density cell ids, and numeric totals
    are emitted. No coordinates, file names, timestamps, or tracks are kept.
    """
    cell_deg = float(config["watershed"].get("density_cell_deg", 0.5))
    by_date: Dict[str, Dict[str, Any]] = {}

    for record in records:
        date = record.get("date") or ""
        if not date:
            continue
        try:
            day_of_year = dt.date.fromisoformat(date).timetuple().tm_yday
        except ValueError:
            continue

        sport = record.get("activity") or "other"
        bucket = by_date.setdefault(
            date,
            {
                "date": date,
                "year": season_year(date),
                "day_of_year": day_of_year,
                "activities": 0,
                "distance_mi": 0.0,
                "elevation_ft": 0.0,
                "sports": {},
                "regions": Counter(),
                "cells": Counter(),
            },
        )
        sport_bucket = bucket["sports"].setdefault(
            sport, {"count": 0, "distance_mi": 0.0, "elevation_ft": 0.0}
        )
        distance = float(finite_or_none(record.get("distance_mi")) or 0)
        elevation = float(finite_or_none(record.get("elevation_ft")) or 0)
        sport_bucket["count"] += 1
        sport_bucket["distance_mi"] += distance
        sport_bucket["elevation_ft"] += elevation
        bucket["activities"] += 1
        bucket["distance_mi"] += distance
        bucket["elevation_ft"] += elevation

        region_id = record.get("region_id")
        if region_id:
            bucket["regions"][str(region_id)] += 1
        cell = cell_key_from_point(record.get("lon"), record.get("lat"), cell_deg)
        if cell:
            bucket["cells"][cell] += 1

    days: List[Dict[str, Any]] = []
    for date in sorted(by_date):
        bucket = by_date[date]
        sports = {}
        for sport, values in sorted(bucket["sports"].items()):
            sports[sport] = {
                "count": values["count"],
                "distance_mi": round(values["distance_mi"], 2),
                "elevation_ft": int(round(values["elevation_ft"])),
            }
        region = bucket["regions"].most_common(1)
        cell = bucket["cells"].most_common(1)
        days.append(
            {
                "date": date,
                "year": bucket["year"],
                "day_of_year": bucket["day_of_year"],
                "activities": bucket["activities"],
                "distance_mi": round(bucket["distance_mi"], 2),
                "elevation_ft": int(round(bucket["elevation_ft"])),
                "region_id": region[0][0] if region else "",
                "cell": cell[0][0] if cell else "",
                "sports": sports,
            }
        )

    metadata = {
        "generated_by": "scripts/build_atlas.py",
        "generated_at": generated_at,
        "days": len(days),
        "activities": sum(day["activities"] for day in days),
        "years": len({day["year"] for day in days if day["year"] is not None}),
        "total_distance_mi": round(sum(day["distance_mi"] for day in days), 1),
        "total_elevation_ft": sum(day["elevation_ft"] for day in days),
        "privacy": (
            "Per-day, per-sport aggregates only. Region and cell ids are removed before "
            "publication, so a date cannot be tied to a location. No raw tracks, "
            "timestamps, activity files, or precise coordinates are published."
        ),
    }
    return {"metadata": metadata, "days": days}


def grade_band(grade: Any) -> str:
    """Reduce a route grade to a countable band without revealing route names."""
    text = str(grade or "").strip()
    if not text:
        return "ungraded"
    for pattern in (r"5\.\d+[abcd]?", r"V\d+", r"WI\s?\d+"):
        match = re.match(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", "", match.group(0)).upper().replace("WI", "WI ")
    return text[:12]


def build_climbing_insights(records: List[Dict[str, Any]], generated_at: str) -> Dict[str, Any]:
    """Aggregate Mountain Project ticks by year, grade, style, and region."""
    ticks = [
        record
        for record in records
        if record.get("source") == "mountain-project" and record.get("date")
    ]
    years: Dict[int, Dict[str, Any]] = {}
    grades: Counter = Counter()
    styles: Counter = Counter()
    regions: Counter = Counter()

    for tick in ticks:
        year = season_year(tick["date"])
        if year is None:
            continue
        band = grade_band(tick.get("grade"))
        style = (tick.get("style") or "Unknown").strip() or "Unknown"
        region = str(tick.get("region_id") or "unknown")

        grades[band] += 1
        styles[style] += 1
        regions[region] += 1

        row = years.setdefault(
            year, {"ticks": 0, "grades": Counter(), "styles": Counter(), "regions": Counter()}
        )
        row["ticks"] += 1
        row["grades"][band] += 1
        row["styles"][style] += 1
        row["regions"][region] += 1

    year_rows = []
    for year in sorted(years):
        row = years[year]
        year_rows.append(
            {
                "year": year,
                "ticks": row["ticks"],
                "grades": dict(sorted(row["grades"].items())),
                "styles": dict(sorted(row["styles"].items())),
                "regions": dict(sorted(row["regions"].items())),
            }
        )

    return {
        "metadata": {
            "generated_by": "scripts/build_atlas.py",
            "generated_at": generated_at,
            "total_ticks": len(ticks),
            "privacy": (
                "Counts by grade/style/region only. Route names, notes, star ratings, "
                "and coordinates are never published from the private ticks export."
            ),
        },
        "years": year_rows,
        "grades": dict(sorted(grades.items())),
        "styles": dict(sorted(styles.items())),
        "regions": dict(sorted(regions.items())),
    }


def build_books_insights(books_path: Path, generated_at: str) -> Dict[str, Any]:
    """Summarise the already-public bookshelf by year and format."""
    if not books_path.exists():
        warn(f"books data not found at {books_path}; skipping book aggregates")
        return {}
    try:
        document = json.loads(books_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warn(f"could not read {books_path} ({exc}); skipping book aggregates")
        return {}

    years = []
    formats: Counter = Counter()
    total = 0
    for year_node in document.get("children", []):
        year = str(year_node.get("name") or "")
        year_formats: Counter = Counter()
        count = 0
        for format_node in year_node.get("children", []):
            fmt = str(format_node.get("name") or "other").strip().lower() or "other"
            books = format_node.get("children") or []
            year_formats[fmt] += len(books)
            formats[fmt] += len(books)
            count += len(books)
        if not count:
            continue
        total += count
        years.append(
            {"year": year, "books": count, "formats": dict(sorted(year_formats.items()))}
        )

    return {
        "metadata": {
            "generated_by": "scripts/build_atlas.py",
            "generated_at": generated_at,
            "source": "js/books.json (already published bookshelf data)",
            "total_books": total,
        },
        "years": years,
        "formats": dict(sorted(formats.items())),
    }


# Broad, readable lanes for the public bookshelf. Open Library subjects are
# messy, so every book gets one primary lane based on the first keyword hit.
BOOK_LANES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Horror", ("horror", "vampire", "zombie", "ghost story")),
    ("Romance", ("romance", "love story", "courtship")),
    ("Science fiction", ("science fiction", "sci-fi", "dystop", "spaceship", "alien", "robot", "future")),
    ("Fantasy", ("fantasy", "magic", "magician", "supernatural", "dragon", "fairy", "myth", "folklore", "legend")),
    ("Mystery & thriller", ("mystery", "thriller", "suspense", "detective", "crime", "murder", "espionage", "spy", "cozy")),
    ("Memoir & biography", ("biography", "memoir", "autobiography", "life story", "personal narrative")),
    ("History", ("history", "historical", "world war", "ancient", "medieval", "century")),
    ("Young reader", ("juvenile", "young adult", "children", "teen", "picture books")),
    ("Poetry", ("poetry", "poems", "poet")),
    ("Nature & travel", ("nature", "travel", "outdoor", "wilderness", "forest", "mountain", "environment", "wildfire", "plants", "indigenous", "geography")),
    ("Humor", ("humor", "comedy", "satire")),
    ("Nonfiction", ("nonfiction", "psychology", "politics", "law", "sociology", "education", "science", "business", "self-help", "race", "courage", "medicine", "medical", "ethics", "church", "hymns", "women", "sexual", "health", "essays")),
    ("Literary fiction", ("fiction", "novel", "short stories", "contemporary", "classic", "literary", "american literature", "english literature", "booksellers")),
]
NOISE_SUBJECTS = {
    "fiction", "novels", "novel", "adult", "english", "literature", "books",
    "audiobooks", "book club", "kindle", "e-books", "general", "united states",
}


def normalize_book_key(title: str, author: str, goodreads_id: str) -> str:
    if goodreads_id:
        return f"gr-{goodreads_id}"
    base = re.sub(r"[^a-z0-9]+", "-", f"{title} {author}".lower()).strip("-")
    return base or "unknown"


def book_lane(subjects: Iterable[str]) -> str:
    text = " | ".join(str(subject).lower() for subject in subjects)
    for lane, keywords in BOOK_LANES:
        if any(keyword in text for keyword in keywords):
            return lane
    return "Other"


def fetch_openlibrary_book(title: str, author: str) -> Optional[Dict[str, Any]]:
    """Look up public metadata for one already-published book."""
    fields = (
        "key,title,author_name,subject,number_of_pages_median,"
        "first_publish_year"
    )
    for query in (f"{title} {author}".strip(), title):
        if not query:
            continue
        params = urllib.parse.urlencode(
            {"q": query, "fields": fields, "limit": 3}
        )
        request = urllib.request.Request(
            f"https://openlibrary.org/search.json?{params}",
            headers={"User-Agent": "klepikhina-atlas/1.0 (+https://klepikhina.github.io)"},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
                    document = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:  # network failures must not break the atlas
                if attempt == 2:
                    warn(f"book lookup failed for {title!r}: {exc}")
                    document = None
                else:
                    time.sleep(1 + attempt)
        if not document:
            continue
        candidates = document.get("docs") or []
        if not candidates:
            continue
        wanted_tokens = set(re.findall(r"[a-z0-9]+", title.lower()))
        author_tokens = set(re.findall(r"[a-z0-9]+", author.lower()))
        chosen = candidates[0]
        best_score = -1.0
        for candidate in candidates:
            title_tokens = set(re.findall(r"[a-z0-9]+", str(candidate.get("title") or "").lower()))
            candidate_authors = candidate.get("author_name") or []
            author_match = max(
                [len(author_tokens & set(re.findall(r"[a-z0-9]+", str(name).lower()))) for name in candidate_authors]
                or [0]
            )
            title_score = len(wanted_tokens & title_tokens) / max(1, len(wanted_tokens))
            score = title_score + 0.25 * (1 if author_match >= 2 else 0)
            if score > best_score:
                best_score = score
                chosen = candidate
        return chosen
    return None


def clean_book_text(value: str) -> str:
    """Undo hand-entered escapes and trailing reread notes from the bookshelf."""
    text = str(value or "").replace("\\'", "'").replace('\\"', '"')
    return text.split("|", 1)[0].strip().strip(' "')


def fetch_goodreads_book(goodreads_id: str) -> Optional[Dict[str, Any]]:
    """Fetch public page count, publication year, and top genres from Goodreads."""
    if not goodreads_id:
        return None
    request = urllib.request.Request(
        f"https://www.goodreads.com/book/show/{goodreads_id}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    html = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
                html = response.read().decode("utf-8", errors="replace")
            break
        except Exception as exc:  # network failures must not break the atlas
            if attempt == 2:
                warn(f"Goodreads lookup failed for {goodreads_id}: {exc}")
                return None
            time.sleep(2 + attempt)

    result: Dict[str, Any] = {}
    structured = re.search(r'<script type="application/ld\+json">(.*?)</script>', html or "", re.S)
    if structured:
        try:
            payload = json.loads(structured.group(1))
            pages = payload.get("numberOfPages")
            if pages:
                result["pages"] = int(pages)
        except (ValueError, TypeError):
            pass
    if not result.get("pages"):
        pages_match = re.search(r'data-testid="pagesFormat"[^>]*>(\d+)\s+pages', html or "", re.I)
        if pages_match:
            result["pages"] = int(pages_match.group(1))
    publication = re.search(r'data-testid="publicationInfo"[^>]*>([^<]+)', html or "", re.I)
    if publication:
        year_match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", publication.group(1))
        if year_match:
            result["published"] = int(year_match.group(1))
    genre_block = re.search(r'data-testid="genresList".*?</ul>', html or "", re.S)
    if genre_block:
        genres = [
            genre.strip()
            for genre in re.findall(r'<span class="Button__labelItem">([^<]+)</span>', genre_block.group(0))
            if genre.strip().lower() not in NOISE_SUBJECTS
        ][:8]
        if genres:
            result["subjects"] = genres
            result["lane"] = book_lane(genres)
    return result or None


def build_book_meta_insights(
    books_path: Path,
    previous: Optional[Dict[str, Any]],
    generated_at: str,
    fetch: bool = True,
) -> Dict[str, Any]:
    """Cache public book metadata (pages, publication year, subjects) by Goodreads id."""
    if not books_path.exists():
        return {}
    try:
        document = json.loads(books_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warn(f"could not read {books_path} ({exc}); skipping book metadata")
        return {}

    cached: Dict[str, Dict[str, Any]] = {}
    for row in (previous or {}).get("books", []):
        key = row.get("key")
        if key:
            cached[str(key)] = row

    shelves: List[Dict[str, Any]] = []
    for year_node in document.get("children", []):
        year = str(year_node.get("name") or "")
        for format_node in year_node.get("children", []):
            fmt = str(format_node.get("name") or "other").strip().lower() or "other"
            for book in format_node.get("children") or []:
                name = str(book.get("name") or "").strip().strip('"')
                if '",' in name:
                    title, author = name.split('",', 1)
                    author = author.strip(' "')
                else:
                    title, author = name, ""
                title = clean_book_text(title)
                author = clean_book_text(author)
                goodreads_id = str(book.get("value") or "").strip()
                key = normalize_book_key(title, author, goodreads_id)
                entry = dict(cached.get(key) or {})
                needs_lookup = not entry or not entry.get("pages") or not entry.get("subjects")
                if fetch and needs_lookup:
                    found = fetch_openlibrary_book(title, author)
                    if found:
                        pages = found.get("number_of_pages_median")
                        if not entry.get("pages") and pages:
                            entry["pages"] = int(pages)
                        if not entry.get("published") and found.get("first_publish_year"):
                            entry["published"] = found.get("first_publish_year")
                        if not entry.get("subjects"):
                            subjects = [
                                str(subject)
                                for subject in (found.get("subject") or [])
                                if str(subject).strip().lower() not in NOISE_SUBJECTS
                            ][:8]
                            if subjects:
                                entry["subjects"] = subjects
                    if goodreads_id and (not entry.get("pages") or not entry.get("subjects")):
                        goodreads = fetch_goodreads_book(goodreads_id)
                        if goodreads:
                            if not entry.get("pages") and goodreads.get("pages"):
                                entry["pages"] = goodreads["pages"]
                            if not entry.get("published") and goodreads.get("published"):
                                entry["published"] = goodreads["published"]
                            if not entry.get("subjects") and goodreads.get("subjects"):
                                entry["subjects"] = goodreads["subjects"]
                    if entry:
                        entry["key"] = key
                        entry["fetched_at"] = generated_at
                        cached[key] = entry
                        time.sleep(0.9)
                shelves.append(
                    {
                        "key": key,
                        "title": title,
                        "author": author,
                        "year": year,
                        "format": fmt,
                        "goodreads_id": goodreads_id,
                    }
                )

    if not shelves:
        return {}

    enriched = []
    lanes: Counter = Counter()
    by_year: Dict[str, Dict[str, Any]] = {}
    for row in shelves:
        entry = cached.get(row["key"]) or {}
        subjects = entry.get("subjects") or []
        if not entry:
            lane = "Unindexed"
        elif subjects:
            lane = book_lane(subjects)
        else:
            lane = entry.get("lane") or "Other"
        enriched.append({
            **row,
            "pages": entry.get("pages"),
            "published": entry.get("published"),
            "subjects": subjects,
            "lane": lane,
        })
        lanes[lane] += 1
        year_row = by_year.setdefault(row["year"], {"year": row["year"], "books": 0, "pages": 0, "lanes": Counter()})
        year_row["books"] += 1
        year_row["pages"] += int(entry.get("pages") or 0)
        year_row["lanes"][lane] += 1

    return {
        "metadata": {
            "generated_by": "scripts/build_atlas.py",
            "generated_at": generated_at,
            "source": "Open Library and Goodreads public metadata, cached by Goodreads id",
            "total_books": len(enriched),
            "books_with_pages": sum(1 for row in enriched if row["pages"]),
            "books_with_subjects": sum(1 for row in enriched if row["subjects"]),
            "privacy": (
                "All titles and Goodreads ids are already public on the "
                "bookshelf page. No private reading notes or local files are used."
            ),
        },
        "books": enriched,
        "lanes": dict(sorted(lanes.items(), key=lambda pair: (-pair[1], pair[0]))),
        "years": [
            {**by_year[year], "lanes": dict(sorted(by_year[year]["lanes"].items()))}
            for year in sorted(by_year)
        ],
    }


def build_region_timeline(records: List[Dict[str, Any]], generated_at: str) -> Dict[str, Any]:
    """First public region visited, useful for 'exploration frontier' graphics."""
    regions: Dict[str, Dict[str, Any]] = {}
    for record in records:
        region_id = record.get("region_id")
        date = record.get("date")
        if not region_id or not date:
            continue
        entry = regions.setdefault(
            str(region_id),
            {
                "region_id": str(region_id),
                "region_name": record.get("region_name")
                or str(region_id).replace("-", " ").title(),
                "first_date": date,
                "activity_count": 0,
            },
        )
        if date < entry["first_date"]:
            entry["first_date"] = date
        entry["activity_count"] += 1

    timeline = sorted(
        regions.values(), key=lambda row: (row["first_date"], row["region_id"])
    )
    return {
        "metadata": {
            "generated_by": "scripts/build_atlas.py",
            "generated_at": generated_at,
            "regions": len(timeline),
            "privacy": "Coarse public regions and first-visit dates only.",
        },
        "regions": timeline,
    }


def fetch_weather_range(
    center: Tuple[float, float], start: str, end: str
) -> Optional[List[Dict[str, Any]]]:
    """Fetch one day-range of historical weather at a coarse cell centre."""
    lon, lat = center
    query = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.3f}",
            "longitude": f"{lon:.3f}",
            "start_date": start,
            "end_date": end,
            "daily": "temperature_2m_mean,precipitation_sum",
            "timezone": "UTC",
        }
    )
    request = urllib.request.Request(
        f"https://archive-api.open-meteo.com/v1/archive?{query}",
        headers={"User-Agent": "klepikhina-atlas/1.0 (+https://klepikhina.github.io)"},
    )
    with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
        document = json.loads(response.read().decode("utf-8"))
    daily = document.get("daily") or {}
    times = daily.get("time") or []
    temperatures = daily.get("temperature_2m_mean") or []
    precipitation = daily.get("precipitation_sum") or []
    rows = []
    for index, date in enumerate(times):
        temperature = temperatures[index] if index < len(temperatures) else None
        rain = precipitation[index] if index < len(precipitation) else None
        if temperature is None and rain is None:
            continue
        rows.append(
            {
                "date": date,
                "temperature_c": round(float(temperature), 1) if temperature is not None else None,
                "precipitation_mm": round(float(rain), 1) if rain is not None else None,
            }
        )
    return rows


def build_weather_insights(
    days: List[Dict[str, Any]],
    previous: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    generated_at: str,
    fetch: bool = True,
) -> Dict[str, Any]:
    """Incrementally cache monthly weather at coarse cells.

    Dates are collapsed to year+month before publication so a specific activity
    day can never be joined back to a cell. Only cell centres from the already
    public density grid are sent to the weather API.
    """
    cell_deg = float(config["watershed"].get("density_cell_deg", 0.5))
    cached: Dict[str, Dict[str, Any]] = {}
    for row in (previous or {}).get("weather", []):
        cell, year, month = row.get("cell"), row.get("year"), row.get("month")
        if cell and year and month:
            cached[f"{cell}|{int(year):04d}-{int(month):02d}"] = row

    needed_months: set = set()
    missing: Dict[str, set] = {}
    for day in days:
        cell, date = day.get("cell"), day.get("date")
        if not cell or not date or len(date) < 7:
            continue
        month = date[:7]
        key = f"{cell}|{month}"
        needed_months.add(key)
        if key not in cached:
            missing.setdefault(str(cell), set()).add(month)

    if fetch:
        for cell, months in sorted(missing.items()):
            center = cell_center(cell, cell_deg)
            if center is None:
                continue
            first_month = min(months)
            last_year, last_month = (int(part) for part in max(months).split("-"))
            next_month = dt.date(last_year + (last_month == 12), last_month % 12 + 1, 1)
            end = (next_month - dt.timedelta(days=1)).isoformat()
            try:
                rows = fetch_weather_range(center, f"{first_month}-01", end)
            except Exception as exc:  # network failures must not break the atlas
                warn(f"weather lookup failed for cell {cell}: {exc}")
                continue
            if not rows:
                continue

            buckets: Dict[str, Dict[str, List[float]]] = {}
            for row in rows:
                month = str(row.get("date") or "")[:7]
                key = f"{cell}|{month}"
                if key not in needed_months:
                    continue
                bucket = buckets.setdefault(key, {"temperature": [], "precipitation": []})
                if row.get("temperature_c") is not None:
                    bucket["temperature"].append(float(row["temperature_c"]))
                if row.get("precipitation_mm") is not None:
                    bucket["precipitation"].append(float(row["precipitation_mm"]))

            for key, bucket in buckets.items():
                year, month = (int(part) for part in key.split("|")[1].split("-"))
                temperatures = bucket["temperature"]
                precipitation = bucket["precipitation"]
                cached[key] = {
                    "cell": cell,
                    "year": year,
                    "month": month,
                    "days": len(temperatures) or len(precipitation),
                    "temperature_c": (
                        round(sum(temperatures) / len(temperatures), 1)
                        if temperatures else None
                    ),
                    "precipitation_mm": (
                        round(sum(precipitation) / len(precipitation), 1)
                        if precipitation else None
                    ),
                }
            time.sleep(0.15)

    weather = sorted(
        cached.values(), key=lambda row: (row["year"], row["month"], row["cell"])
    )
    return {
        "metadata": {
            "generated_by": "scripts/build_atlas.py",
            "generated_at": generated_at,
            "source": "Open-Meteo archive API, one query per coarse cell",
            "months_with_weather": len(weather),
            "privacy": (
                "Weather is aggregated by year and month at the centre of the published "
                "0.5-degree density cell. Activity dates are never published next to a "
                "cell, so a specific day cannot be tied to a location. No home "
                "coordinates or precise activity locations are sent."
            ),
        },
        "weather": weather,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the adventure atlas (admin regions + heatmap + curated routes).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--posts-dir", type=Path, default=REPO_ROOT, help="Jekyll site root (default: repo root)")
    parser.add_argument("--strava-dir", type=Path, help="folder of raw Strava GPX/FIT exports (private mode)")
    parser.add_argument("--garmin-dir", type=Path, help="folder of raw Garmin FIT/GPX exports (private mode)")
    parser.add_argument("--ticks-csv", type=Path, help="private Mountain Project ticks CSV (keep outside data/)")
    parser.add_argument(
        "--crag-coords",
        type=Path,
        default=SCRIPT_DIR / "crag_coords.json",
        help="JSON map of crag/location -> [lon, lat] (default: scripts/crag_coords.json)",
    )
    parser.add_argument(
        "--watersheds",
        type=Path,
        nargs="+",
        help="one or more HydroBASINS shapefiles (default: all configured continents)",
    )
    parser.add_argument("--download-watersheds", action="store_true", help="download HydroBASINS NA L8 if missing")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE, help=f"watershed cache (default: {DEFAULT_CACHE})")
    parser.add_argument("--regions-geojson", type=Path, help="reuse region polygons from an existing regions.geojson")
    parser.add_argument("--regions-config", type=Path, default=SCRIPT_DIR / "regions_config.json")
    parser.add_argument("--curated", type=Path, default=SCRIPT_DIR / "curated_routes.json")
    parser.add_argument("--blogged", type=Path, help="JSON manifest of curated blogged GPX tracks")
    parser.add_argument(
        "--books-json",
        type=Path,
        default=REPO_ROOT / "js" / "books.json",
        help="already-published bookshelf JSON (default: js/books.json)",
    )
    parser.add_argument(
        "--weather",
        action="store_true",
        help="incrementally fetch historical weather at coarse cells (Open-Meteo)",
    )
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--simplify-deg", type=float, default=None, help="watershed simplification in degrees")
    parser.add_argument("--feature-simplify-deg", type=float, default=0.0004, help="route line simplification")
    parser.add_argument("--trim-meters", type=float, default=500.0, help="trim each end of blogged GPX (default 500 m)")
    parser.add_argument(
        "--include-posts",
        action="store_true",
        help="keep every blog post even when a private export covers the same day/sport",
    )
    parser.add_argument("--no-featured", action="store_true", help="skip featured_routes.geojson")
    parser.add_argument(
        "--state-path",
        type=Path,
        default=REPO_ROOT / "private" / "atlas_state.json",
        help="incremental cache for parsed private exports (default: private/atlas_state.json)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="ignore and do not update the private export cache (reparse everything)",
    )
    parser.add_argument(
        "--use-cache-records",
        action="store_true",
        help="build from cached parsed summaries without raw exports (cloud mode)",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and report without writing files or cache")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.regions_config)
    args.activity_labels = config.get("activity_labels", {})
    simplify_deg = args.simplify_deg if args.simplify_deg is not None else config["watershed"].get("simplify_deg", 0.008)

    state_path = args.state_path.expanduser()
    cache_enabled = not args.no_cache
    if args.use_cache_records and not cache_enabled:
        sys.exit("--use-cache-records cannot be combined with --no-cache")
    state = load_state(state_path, cache_enabled)

    posts = load_posts(args.posts_dir, config)
    log(f"posts with coordinates: {len(posts)}")

    private_records: List[Dict[str, Any]] = []
    strava_records: List[Dict[str, Any]] = []
    garmin_records: List[Dict[str, Any]] = []
    if args.use_cache_records:
        strava_records = cached_private_records(state["strava"])
        garmin_records = cached_private_records(state["garmin"])
        log(
            f"cached activities read: {len(strava_records)} Strava, "
            f"{len(garmin_records)} Garmin (no raw exports)"
        )
    else:
        if args.strava_dir:
            strava_cache = state["strava"] if cache_enabled else None
            strava_records = load_strava_activities(args.strava_dir.expanduser(), strava_cache)
            log(f"Strava activities read: {len(strava_records)}")
        if args.garmin_dir:
            garmin_cache = state["garmin"] if cache_enabled else None
            garmin_records = load_garmin_activities(args.garmin_dir.expanduser(), garmin_cache)
            log(f"Garmin activities read: {len(garmin_records)}")
    if garmin_records:
        strava_records, _ = dedupe_garmin_primary(strava_records, garmin_records)
    private_records.extend(garmin_records)
    private_records.extend(strava_records)
    if args.ticks_csv:
        crag_coords: Dict[str, Tuple[float, float]] = {}
        coords_path = args.crag_coords
        if coords_path and coords_path.exists():
            raw = json.loads(coords_path.read_text(encoding="utf-8"))
            for name, coords in raw.items():
                if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                    try:
                        crag_coords[name] = (float(coords[0]), float(coords[1]))
                    except (TypeError, ValueError):
                        continue
        else:
            warn(
                f"crag coordinate file not found: {coords_path}. "
                "Run scripts/fetch_crag_coords.py to generate it."
            )
        ticks_cache = state["ticks"] if cache_enabled else None
        ticks = load_mp_ticks(args.ticks_csv.expanduser(), crag_coords, ticks_cache)
        log(f"Mountain Project ticks read: {len(ticks)}")
        private_records.extend(ticks)

    if args.strava_dir:
        attached = attach_ski_styles(private_records, args.strava_dir.expanduser(), source="strava")
        if attached:
            log(f"ski styles from Strava metadata: {attached} activity(ies)")
    if args.garmin_dir:
        attached = attach_ski_styles(private_records, args.garmin_dir.expanduser(), source="garmin")
        if attached:
            log(f"ski styles from Garmin metadata: {attached} activity(ies)")

    dynamic = config.get("mode") == "dynamic"
    snap_deg = 0 if dynamic else 0.08

    if dynamic and config.get("region_basis", "admin") == "admin":
        countries_path = ensure_country_polygons(
            config, args.cache_dir.expanduser(), args.download_watersheds
        )
        countries = load_country_polygons(countries_path)
        admin1_path = ensure_admin1_polygons(
            config, args.cache_dir.expanduser(), args.download_watersheds
        )
        admin1 = load_admin1_polygons(admin1_path)
        polygons = build_admin_polygons(admin1, countries)
        assign_admin_regions(posts, polygons)
        assign_admin_regions(private_records, polygons)
        used_ids = {
            record["region_id"]
            for record in posts + private_records
            if record.get("region_id")
        }
        regions = polygons[polygons["region_id"].isin(used_ids)].copy().reset_index(drop=True)
        log(
            f"admin regions: {len(regions)} states/provinces/countries "
            "with an activity or blog post"
        )
    elif dynamic:
        if args.watersheds:
            paths = [path.expanduser() for path in args.watersheds]
        else:
            paths = ensure_global_watersheds(
                config, args.cache_dir.expanduser(), args.download_watersheds
            )
        basins = load_global_basins(paths)
        countries_path = ensure_country_polygons(
            config, args.cache_dir.expanduser(), args.download_watersheds
        )
        countries = load_country_polygons(countries_path)
        admin1_path = ensure_admin1_polygons(
            config, args.cache_dir.expanduser(), args.download_watersheds
        )
        admin1 = load_admin1_polygons(admin1_path)
        regions = build_global_regions(
            posts, private_records, basins, countries, admin1, simplify_deg
        )
    else:
        if args.watersheds:
            regions = dissolve_regions(args.watersheds[0].expanduser(), config, simplify_deg)
        else:
            existing = args.regions_geojson or (args.out_dir / "regions.geojson")
            if existing.exists():
                log(f"reusing region geometry from {existing}")
                regions = load_regions_geometry(existing)
            else:
                shapefile = ensure_watershed_shapefile(
                    config, args.cache_dir.expanduser(), args.download_watersheds
                )
                regions = dissolve_regions(shapefile, config, simplify_deg)

    if not dynamic:
        target_names = {region["region_name"] for region in config["regions"]}
        posts = [post for post in posts if post.get("configured_region") in target_names]

    # Scope private records to watershed regions before de-duplicating, so a
    # tick in Georgia never hides a blog post in Colorado on the same day.
    if private_records:
        private_records = assign_regions(private_records, regions, max_snap_deg=snap_deg)

    records = merge_posts_with_private(posts, private_records, args.include_posts)
    records = assign_regions(records, regions, max_snap_deg=snap_deg)
    unmatched = [record for record in records if not record.get("region_id")]
    if unmatched:
        warn(f"{len(unmatched)} activity record(s) could not be assigned to a watershed region")

    curated = json.loads(args.curated.read_text(encoding="utf-8")) if args.curated.exists() else {"routes": []}
    blogged_tracks = load_blogged_tracks(args.blogged, args)
    if blogged_tracks:
        log(f"curated blogged GPX tracks: {len(blogged_tracks)} (trimmed {args.trim_meters:g} m each end, timestamps stripped)")

    featured: List[Dict[str, Any]] = []
    if not args.no_featured:
        featured = build_featured_routes(posts, curated, regions, blogged_tracks, args)

    regions = aggregate_regions(regions, records, featured, config)
    density, density_info = build_density_grid(records, config)
    log(
        f"density grid: {density_info['cells']} cells at {density_info['cell_deg']:g}\u00b0 "
        f"(private cells with fewer than {density_info['min_count']} activities suppressed)"
    )
    validate(regions, featured, curated)
    print_report(regions, featured)

    region_features = []
    for _, row in regions.iterrows():
        region_features.append(
            {
                "type": "Feature",
                "geometry": geometry_dict(row.geometry),
                "properties": {
                    "region_id": row["region_id"],
                    "region_name": row["region_name"],
                    "region_kind": row["region_kind"],
                    "activity_count": int(row["activity_count"]),
                    "total_vert_ft": int(row["total_vert_ft"]),
                    "total_distance_mi": float(row["total_distance_mi"]),
                    "sports": list(row["sports"]),
                    "featured_story_count": int(row["featured_story_count"]),
                    "vert_coverage": float(row["vert_coverage"]),
                    "distance_coverage": float(row["distance_coverage"]),
                    "departments": dict(row["departments"]),
                    "stats": dict(row["stats"]),
                    "top_stories": list(row["top_stories"]),
                    "area_km2": int(row["area_km2"]),
                    "centroid": list(row["centroid"]),
                    **{
                        key: value
                        for key, value in row.items()
                        if key.endswith(("_trips", "_vert_ft", "_distance_mi", "_pct", "_count"))
                    },
                },
            }
        )

    validate_imperial_labels(posts, featured, region_features)
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    region_metadata = {
        "generated_by": "scripts/build_atlas.py",
        "generated_at": generated_at,
        "watershed_source": config["watershed"]["dataset"],
        "region_basis": (
            "Natural Earth admin-1 states/provinces (US/Canada) and admin-0 countries"
            if config.get("region_basis", "admin") == "admin"
            else config["watershed"]["dataset"]
        ),
        "crs": "EPSG:4326",
        "privacy": (
            "Aggregate statistics only. No raw GPS tracks, timestamps, or activity files are "
            "stored here. total_vert_ft and total_distance_mi sum only activities where that value "
            "was known; see vert_coverage and distance_coverage."
        ),
    }
    route_metadata = {
        "generated_by": "scripts/build_atlas.py",
        "generated_at": generated_at,
        "curation": (
            "Only explicitly curated, blog-linked routes from scripts/curated_routes.json. "
            "When GPX is supplied, lines are trimmed and timestamps are stripped."
        ),
        "crs": "EPSG:4326",
    }

    density_metadata = {
        "generated_by": "scripts/build_atlas.py",
        "generated_at": generated_at,
        "cell_deg": density_info["cell_deg"],
        "privacy": (
            "Anonymised density only. Activity locations are coarsened into "
            f"{density_info['cell_deg']:g}-degree grid cells and reduced to counts; "
            "no individual coordinates, timestamps, or tracks are stored. Cells with "
            f"fewer than {density_info['min_count']} private activities are suppressed."
        ),
    }
    write_collection(args.out_dir / "regions.geojson", region_features, region_metadata, args.dry_run)
    write_collection(args.out_dir / "activity_density.geojson", density["features"], density_metadata, args.dry_run)
    if not args.no_featured:
        write_collection(args.out_dir / "featured_routes.geojson", featured, route_metadata, args.dry_run)

    site_activities = build_site_activities(featured)
    write_json(
        args.out_dir / "activities.json",
        {
            "metadata": {
                "generated_by": "scripts/build_atlas.py",
                "generated_at": generated_at,
                "source": "scripts/curated_routes.json + Jekyll post front matter",
                "activity_count": len(site_activities),
            },
            "activities": site_activities,
        },
        args.dry_run,
    )
    region_data = build_region_pages(region_features, config, generated_at)
    write_json(args.out_dir / "region_pages.json", region_data, args.dry_run)
    write_site_pages(region_data, args.out_dir, args.dry_run)

    insights_dir = args.out_dir / "insights"
    daily_insights = build_daily_insights(records, config, generated_at)
    public_daily = {
        "metadata": daily_insights["metadata"],
        "days": [
            {key: value for key, value in day.items() if key not in ("region_id", "cell")}
            for day in daily_insights["days"]
        ],
    }
    write_json(insights_dir / "daily.json", public_daily, args.dry_run, compact=True)
    write_json(
        insights_dir / "climbing.json",
        build_climbing_insights(records, generated_at),
        args.dry_run,
        compact=True,
    )
    write_json(
        insights_dir / "region_timeline.json",
        build_region_timeline(records, generated_at),
        args.dry_run,
        compact=True,
    )
    books_insights = build_books_insights(args.books_json, generated_at)
    if books_insights:
        write_json(insights_dir / "books.json", books_insights, args.dry_run, compact=True)
    book_meta_path = insights_dir / "book_meta.json"
    previous_book_meta = None
    if book_meta_path.exists():
        try:
            previous_book_meta = json.loads(book_meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warn(f"could not read book metadata cache {book_meta_path} ({exc}); refetching")
    book_meta_insights = build_book_meta_insights(
        args.books_json,
        previous_book_meta,
        generated_at,
        fetch=not args.dry_run,
    )
    if book_meta_insights:
        write_json(book_meta_path, book_meta_insights, args.dry_run, compact=True)
    if args.weather:
        weather_path = insights_dir / "weather.json"
        previous_weather = None
        if weather_path.exists():
            try:
                previous_weather = json.loads(weather_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                warn(f"could not read weather cache {weather_path} ({exc}); refetching")
        weather_insights = build_weather_insights(
            daily_insights["days"],
            previous_weather,
            config,
            generated_at,
            fetch=not args.dry_run,
        )
        write_json(weather_path, weather_insights, args.dry_run, compact=True)

    save_state(state_path, state, cache_enabled, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
