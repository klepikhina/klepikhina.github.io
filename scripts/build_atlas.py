#!/usr/bin/env python3
"""Build the Adventure Atlas.

Three layers are produced:

  data/regions.geojson         Aggregate stats per state/province/country.
  data/activity_density.geojson Anonymised coarse-cell activity density.
  data/featured_routes.geojson Curated, blog-linked routes and crag markers.

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

DIST_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(mi|mile|miles|km|kilometer|kilometre|kms)\b", re.I)
ELEV_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(ft|feet|foot|m|meter|metre|meters|metres)\b", re.I)
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
    return result


def cached_private_records(cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reuse parsed summaries when raw exports are not available (cloud mode)."""
    records: List[Dict[str, Any]] = []
    for entry in cache.values():
        if isinstance(entry, dict) and isinstance(entry.get("record"), dict):
            records.append(stable_private_record(entry["record"]))
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
            miles = value * 0.621371 if unit.startswith("km") or unit.startswith("kilomet") else value
            metrics["distance_mi"] = round(miles, 2)

    elevation = ELEV_RE.search(title or "")
    if elevation:
        value = to_float(elevation.group(1))
        unit = elevation.group(2).lower()
        if value is not None:
            feet = value * 3.28084 if unit in {"m", "meter", "metre", "meters", "metres"} else value
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
        "distance_mi": distance / 1609.344,
        "gain_m": gain,
        "gain_ft": gain * 3.28084,
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


def read_strava_metadata(strava_dir: Path) -> Dict[str, Dict[str, str]]:
    """Map a file name / activity id to metadata from activities.csv."""
    path = strava_dir / "activities.csv"
    if not path.exists():
        return {}
    metadata: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            lower = {
                str(key).strip().lower(): (value or "").strip() if isinstance(value, str) else ""
                for key, value in row.items()
                if key
            }
            filename = lower.get("filename") or lower.get("file name") or ""
            activity_id = lower.get("activity id") or lower.get("id") or ""
            activity_type = lower.get("activity type") or lower.get("type") or ""
            record = {
                "name": lower.get("activity name") or lower.get("name") or "",
                "date": normalize_date(lower.get("activity date") or lower.get("date") or ""),
                "sport": normalize_sport(activity_type),
                "activity_type": activity_type,
                "ski_style": classify_ski_style(activity_type + " " + (lower.get("activity name") or "")),
            }
            if filename:
                metadata[Path(filename).name] = record
            if activity_id:
                metadata[activity_id] = record
    return metadata


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

    A file is considered unchanged when its size and mtime match the cached
    signature. New, modified, or removed files are the only ones re-parsed.
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
        signature = file_signature(path)
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
        meta = metadata.get(path.name) or metadata.get(path.stem.split(".")[0]) or {}
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
            "distance_mi": round(stats["distance_mi"], 2),
            "elevation_ft": round(stats["gain_ft"]),
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


def read_garmin_metadata(garmin_dir: Path) -> Dict[str, Dict[str, str]]:
    """Read Garmin activities.csv using the same column names as Strava."""
    return read_strava_metadata(garmin_dir)


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
        signature = file_signature(path)
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
        meta = metadata.get(path.name) or metadata.get(path.stem.split(".")[0]) or {}
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
            "distance_mi": round(stats["distance_mi"], 2),
            "elevation_ft": round(stats["gain_ft"]),
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
    save_state(state_path, state, cache_enabled, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
