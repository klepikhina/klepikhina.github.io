#!/usr/bin/env python3
"""Keep the Adventure Atlas data synced with Strava, Garmin, and Mountain Project.

The sync runs locally. Secrets are kept in macOS Keychain (or a gitignored
private/.env fallback); raw GPX/FIT and ticks never leave private/. Each run:

  1. fetches only activities added since the previous sync,
  2. writes new GPX/FIT files plus metadata rows into private/,
  3. refreshes the Mountain Project ticks CSV when possible,
  4. runs build_atlas.py incrementally, and
  5. commits/pushes changed data/*.geojson to dev and opens/updates a PR.

It never merges the PR. Install the launchd agent with ``install-scheduler``
and it repeats every ``interval_minutes`` (15 by default).

One-time setup:

    python3 scripts/sync_sources.py setup-strava
    python3 scripts/sync_sources.py authorize-strava
    python3 scripts/sync_sources.py setup-garmin
    python3 scripts/sync_sources.py setup-github
    python3 scripts/sync_sources.py mountain-login
    python3 scripts/sync_sources.py install-scheduler
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import datetime as dt
import getpass
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.sax.saxutils as saxutils
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
PRIVATE_DIR = REPO_ROOT / "private"
CONFIG_PATH = PRIVATE_DIR / "sync_config.json"
ENV_PATH = PRIVATE_DIR / ".env"
STATE_PATH = PRIVATE_DIR / "sync_state.json"
LOG_PATH = PRIVATE_DIR / "logs" / "sync.log"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.klepikhina.atlas-sync.plist"

STRAVA_API = "https://www.strava.com/api/v3"
STRAVA_AUTH = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN = "https://www.strava.com/oauth/token"
GARMIN_API = "https://personal.connectapi.garmin.com"
GARMIN_TOKEN = f"{GARMIN_API}/personalapitoken-service/personal-api/personal-api-key/token"
GITHUB_REPO = "klepikhina/klepikhina.github.io"

KEYCHAIN_PREFIX = "klepikhina-atlas"
SECRETS = {
    "strava_client_id": ("KLEPIKINA_STRAVA_CLIENT_ID", f"{KEYCHAIN_PREFIX}-strava-client-id"),
    "strava_client_secret": ("KLEPIKINA_STRAVA_CLIENT_SECRET", f"{KEYCHAIN_PREFIX}-strava-client-secret"),
    "strava_refresh_token": ("KLEPIKINA_STRAVA_REFRESH_TOKEN", f"{KEYCHAIN_PREFIX}-strava-refresh-token"),
    "garmin_api_key": ("KLEPIKINA_GARMIN_API_KEY", f"{KEYCHAIN_PREFIX}-garmin-api-key"),
    "github_token": ("KLEPIKINA_GITHUB_TOKEN", f"{KEYCHAIN_PREFIX}-github-token"),
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "strava_dir": "private/strava_export",
    "garmin_dir": "private/garmin_export",
    "ticks_csv": "private/mountain-project-ticks.csv",
    "downloads_dir": "~/Downloads",
    "atlas_state": "private/atlas_state.json",
    "interval_minutes": 15,
    "strava_lookback_days": 7,
    "garmin_lookback_days": 7,
    "max_activities_per_sync": 100,
    "mountain_project_ticks_url": "",
    "refresh_crag_coords": False,
    "auto_publish": True,
    "branch": "dev",
    "base_branch": "master",
}

CSV_FIELDS = [
    "Activity ID",
    "Activity Date",
    "Activity Name",
    "Activity Type",
    "Activity Description",
    "Elapsed Time",
    "Distance",
    "Elevation Gain",
    "Filename",
]

GARMIN_SPORT_NAMES = {
    "running": "Run",
    "trail_running": "Trail Run",
    "cycling": "Ride",
    "road_biking": "Ride",
    "mountain_biking": "Mountain Bike Ride",
    "gravel_cycling": "Gravel Ride",
    "hiking": "Hike",
    "walking": "Walk",
    "trekking": "Hike",
    "skiing": "Alpine Ski",
    "resort_skiing": "Alpine Ski",
    "alpine_skiing": "Alpine Ski",
    "backcountry_skiing": "Backcountry Ski",
    "ski_touring": "Backcountry Ski",
    "nordic_skiing": "Nordic Ski",
    "cross_country_skiing": "Nordic Ski",
    "snowboarding": "Snowboard",
    "kayaking": "Kayaking",
    "canoeing": "Canoeing",
    "paddling": "Stand Up Paddling",
    "stand_up_paddling": "Stand Up Paddling",
    "swimming": "Swimming",
    "open_water_swimming": "Swimming",
    "rowing": "Rowing",
    "sailing": "Sailing",
    "surfing": "Surfing",
    "windsurfing": "Windsurfing",
    "kitesurfing": "Kitesurfing",
    "waterskiing": "Waterskiing",
    "wakeboarding": "Wakeboarding",
    "diving": "Scuba Diving",
    "scuba_diving": "Scuba Diving",
    "snorkeling": "Snorkeling",
    "rafting": "Rafting",
    "climbing": "Rock Climbing",
    "rock_climbing": "Rock Climbing",
    "indoor_climbing": "Rock Climbing",
    "bouldering": "Bouldering",
}


class SyncError(RuntimeError):
    """A recoverable sync failure (missing credential, API error, etc.)."""


def log(message: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def load_config() -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                config.update(stored)
        except (ValueError, OSError) as exc:
            log(f"warning: could not read {CONFIG_PATH} ({exc}); using defaults")
    return config


def save_config(config: Dict[str, Any]) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"updated {CONFIG_PATH.relative_to(REPO_ROOT)}")


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "sources": {}, "runs": []}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        log(f"warning: could not read {STATE_PATH} ({exc}); starting fresh")
        return {"version": 1, "sources": {}, "runs": []}
    state.setdefault("sources", {})
    state.setdefault("runs", [])
    return state


def save_state(state: Dict[str, Any]) -> None:
    atomic_json(STATE_PATH, state)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def read_env_file() -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def keychain_get(service: str) -> str:
    if not shutil.which("security"):
        return ""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return ""
    return result.stdout.strip()


def keychain_set(service: str, value: str) -> None:
    if not shutil.which("security"):
        raise SyncError("macOS Keychain is unavailable on this machine")
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            service,
            "-s",
            service,
            "-w",
            value,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def get_secret(name: str) -> str:
    env_key, service = SECRETS[name]
    value = os.environ.get(env_key) or read_env_file().get(env_key) or keychain_get(service)
    return value.strip()


def put_secret(name: str, value: str) -> None:
    env_key, service = SECRETS[name]
    value = value.strip()
    if not value:
        raise SyncError(f"{name} cannot be empty")
    try:
        keychain_set(service, value)
        log(f"stored {name} in macOS Keychain ({service})")
    except (SyncError, subprocess.CalledProcessError) as exc:
        # Keychain is preferred; private/.env is gitignored and 0600.
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        values = read_env_file()
        values[env_key] = value
        ENV_PATH.write_text(
            "\n".join(f"{key}={val}" for key, val in sorted(values.items())) + "\n",
            encoding="utf-8",
        )
        os.chmod(ENV_PATH, 0o600)
        log(f"stored {name} in {ENV_PATH.relative_to(REPO_ROOT)} ({exc})")


def require_secret(name: str, hint: str) -> str:
    value = get_secret(name)
    if not value:
        raise SyncError(f"missing {name}. {hint}")
    return value


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    accept: str = "application/json",
    retries: int = 4,
    timeout: int = 60,
) -> Tuple[bytes, Dict[str, str]]:
    default_headers = {"Accept": accept, "User-Agent": "klepikhina-atlas-sync/1.0"}
    if headers:
        default_headers.update(headers)
    if data is not None and "Content-Type" not in default_headers:
        default_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, headers=default_headers, method=method)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=ssl_context()
            ) as response:
                return response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {429, 500, 502, 503, 504} and attempt + 1 < retries:
                retry_after = exc.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(min(delay, 60))
                continue
            raise SyncError(f"{method} {url} -> HTTP {exc.code}: {body[:400]}") from exc
        except urllib.error.URLError as exc:
            if attempt + 1 < retries:
                time.sleep(2**attempt)
                continue
            raise SyncError(f"{method} {url} failed: {exc}") from exc
    raise SyncError(f"{method} {url} failed after {retries} attempts")


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    form: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = dict(headers or {})
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    else:
        payload = urllib.parse.urlencode(form).encode("utf-8") if form else None
    body, _ = http_request(url, method=method, headers=headers, data=payload)
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise SyncError(f"{url} returned non-JSON: {body[:200]!r}") from exc


def camel_to_words(value: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or ""))
    text = re.sub(r"([A-Z]+)(?=[A-Z][a-z])", r"\1 ", text)
    text = text.replace("_", " ").strip()
    return re.sub(r"\s+", " ", text)


def strava_activity_type(activity: Dict[str, Any]) -> str:
    value = activity.get("sport_type") or activity.get("type") or ""
    text = camel_to_words(value)
    return text or "Activity"


def garmin_activity_type(activity: Dict[str, Any]) -> str:
    activity_type = activity.get("activityType") or {}
    parent = activity.get("parentActivityType") or {}
    key = str(activity_type.get("typeKey") or parent.get("typeKey") or "").lower()
    return GARMIN_SPORT_NAMES.get(key, camel_to_words(key) or "Activity")


def iso_date(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def parse_timestamp(value: Any) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def append_activity_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(CSV_FIELDS)
    if path.exists() and path.stat().st_size:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            existing = next(csv.reader(handle), [])
            if existing:
                fieldnames = list(existing)
    for field in CSV_FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def known_activity_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            lower = {
                str(key).strip().lower(): (value or "").strip() if isinstance(value, str) else ""
                for key, value in row.items()
                if key
            }
            activity_id = lower.get("activity id") or lower.get("id") or ""
            if activity_id:
                ids.add(activity_id)
    return ids


def write_gpx(path: Path, name: str, points: List[Tuple[List[float], Optional[float], Optional[str]]]) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="klepikhina-atlas-sync" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <trk><name>{saxutils.escape(name)}</name><trkseg>",
    ]
    for latlng, altitude, timestamp in points:
        if not isinstance(latlng, (list, tuple)) or len(latlng) < 2:
            continue
        lat, lon = latlng[0], latlng[1]
        if lat is None or lon is None:
            continue
        lines.append(f'    <trkpt lat="{lat}" lon="{lon}">')
        if altitude is not None:
            lines.append(f"      <ele>{altitude}</ele>")
        if timestamp:
            lines.append(f"      <time>{saxutils.escape(timestamp)}</time>")
        lines.append("    </trkpt>")
    lines.extend(["  </trkseg></trk>", "</gpx>", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def strava_access_token(state: Dict[str, Any]) -> str:
    source = state["sources"].setdefault("strava", {})
    expires = int(source.get("expires_at") or 0)
    token = str(source.get("access_token") or "")
    if token and expires > int(time.time()) + 600:
        return token
    client_id = require_secret(
        "strava_client_id", "Run: python3 scripts/sync_sources.py setup-strava"
    )
    client_secret = require_secret(
        "strava_client_secret", "Run: python3 scripts/sync_sources.py setup-strava"
    )
    refresh = require_secret(
        "strava_refresh_token", "Run: python3 scripts/sync_sources.py authorize-strava"
    )
    result = request_json(
        STRAVA_TOKEN,
        method="POST",
        form={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
    )
    if not isinstance(result, dict) or not result.get("access_token"):
        raise SyncError(f"Strava token refresh failed: {str(result)[:300]}")
    source["access_token"] = result["access_token"]
    source["expires_at"] = int(result.get("expires_at") or time.time() + 6 * 3600)
    save_state(state)
    new_refresh = result.get("refresh_token")
    if new_refresh and new_refresh != refresh:
        put_secret("strava_refresh_token", str(new_refresh))
    return str(result["access_token"])


def sync_strava(config: Dict[str, Any], state: Dict[str, Any]) -> int:
    token = strava_access_token(state)
    headers = {"Authorization": f"Bearer {token}"}
    source = state["sources"].setdefault("strava", {})
    seen = set(source.get("seen_ids", []))
    activity_dir = resolve_path(config["strava_dir"]) / "activities"
    activity_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = resolve_path(config["strava_dir"]) / "activities.csv"
    seen |= known_activity_ids(metadata_path)

    lookback = int(config.get("strava_lookback_days", 7))
    after = int(source.get("after") or time.time() - lookback * 86400)
    max_activities = int(config.get("max_activities_per_sync", 100))
    activities: List[Dict[str, Any]] = []
    page = 1
    while len(activities) < max_activities:
        batch = request_json(
            f"{STRAVA_API}/athlete/activities",
            headers=headers,
            params={"after": after, "per_page": min(100, max_activities), "page": page},
        )
        if not isinstance(batch, list) or not batch:
            break
        activities.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    log(f"Strava: {len(activities)} recent activity summary(ies), {len(seen)} already known")

    rows: List[Dict[str, Any]] = []
    newest = after
    skipped = 0
    failed = 0
    for activity in activities[:max_activities]:
        activity_id = str(activity.get("id") or "")
        start = parse_timestamp(activity.get("start_date")) or dt.datetime.now(dt.timezone.utc)
        start_local = parse_timestamp(activity.get("start_date_local")) or start
        newest = max(newest, int(start.timestamp()))
        if not activity_id or activity_id in seen:
            skipped += 1
            continue
        streams = request_json(
            f"{STRAVA_API}/activities/{activity_id}/streams",
            headers=headers,
            params={"keys": "time,latlng,altitude", "key_by_type": "true"},
        )
        latlng = (streams.get("latlng") or {}).get("data") if isinstance(streams, dict) else None
        if not latlng:
            skipped += 1
            seen.add(activity_id)
            continue
        times = ((streams.get("time") or {}).get("data") or [None] * len(latlng))
        altitudes = ((streams.get("altitude") or {}).get("data") or [None] * len(latlng))
        points: List[Tuple[List[float], Optional[float], Optional[str]]] = []
        for index, position in enumerate(latlng):
            offset = times[index] if index < len(times) else None
            timestamp = ""
            if offset is not None and start:
                timestamp = (start + dt.timedelta(seconds=float(offset))).isoformat().replace("+00:00", "Z")
            points.append((position, altitudes[index] if index < len(altitudes) else None, timestamp))
        filename = f"strava-{activity_id}.gpx"
        try:
            write_gpx(activity_dir / filename, str(activity.get("name") or filename), points)
        except OSError as exc:
            failed += 1
            log(f"Strava {activity_id}: could not write GPX ({exc})")
            continue
        rows.append(
            {
                "Activity ID": activity_id,
                "Activity Date": start_local.date().isoformat(),
                "Activity Name": activity.get("name") or "",
                "Activity Type": strava_activity_type(activity),
                "Activity Description": "",
                "Elapsed Time": activity.get("elapsed_time") or "",
                "Distance": activity.get("distance") or "",
                "Elevation Gain": activity.get("total_elevation_gain") or "",
                "Filename": f"activities/{filename}",
            }
        )
        seen.add(activity_id)

    added = append_activity_csv(metadata_path, rows)
    source["seen_ids"] = sorted(seen)[-10000:]
    # Look back a week so late uploads still get picked up; seen_ids prevent dupes.
    if activities:
        source["after"] = max(after, newest - lookback * 86400)
    source["last_sync"] = dt.datetime.now(dt.timezone.utc).isoformat()
    source["last_added"] = added
    save_state(state)
    log(f"Strava: wrote {added} new activity(ies), {skipped} skipped, {failed} failed")
    return added


def jwt_expiration(token: str) -> int:
    """Read the exp claim from a JWT without verifying its signature."""
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        return int(json.loads(decoded.decode("utf-8"))["exp"])
    except (IndexError, KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise SyncError("Garmin token endpoint did not return a usable JWT") from exc


def garmin_bearer_token(state: Dict[str, Any]) -> str:
    source = state["sources"].setdefault("garmin", {})
    token = str(source.get("access_token") or "")
    expires = int(source.get("expires_at") or 0)
    if token and expires > int(time.time()) + 600:
        return token
    api_key = require_secret(
        "garmin_api_key",
        "Create a Personal API key at https://connect.garmin.com/app/settings/personalApiToken "
        "then run: python3 scripts/sync_sources.py setup-garmin",
    )
    body, _ = http_request(
        GARMIN_TOKEN,
        method="POST",
        headers={"Authorization": f"ApiKey {api_key}"},
        accept="*/*",
    )
    text = body.decode("utf-8", errors="replace").strip()
    expires_at = 0
    if text.startswith("{"):
        try:
            result = json.loads(text)
        except ValueError as exc:
            raise SyncError(f"Garmin token request returned invalid JSON: {text[:200]}") from exc
        token = str(result.get("access_token") or result.get("token") or "") if isinstance(result, dict) else ""
        expires_at = int(result.get("expires_at") or 0) if isinstance(result, dict) else 0
        if not expires_at and isinstance(result, dict) and result.get("expires_in"):
            expires_at = int(time.time()) + int(result["expires_in"])
    else:
        # Garmin's documented Personal API returns the bearer JWT as plain text.
        token = text
    if not token:
        raise SyncError(f"Garmin token request failed: {text[:200]}")
    if not expires_at:
        expires_at = jwt_expiration(token)
    source["access_token"] = token
    source["expires_at"] = expires_at
    save_state(state)
    return token


def garmin_json(state: Dict[str, Any], path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    token = garmin_bearer_token(state)
    return request_json(
        f"{GARMIN_API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )


def extract_track(archive: bytes, destination_dir: Path, activity_id: str) -> Optional[Path]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = [
            name
            for name in bundle.namelist()
            if name.lower().endswith((".fit", ".gpx", ".fit.gz", ".gpx.gz"))
        ]
        if not names:
            return None
        names.sort(key=lambda name: (0 if name.lower().endswith((".fit", ".fit.gz")) else 1, name))
        name = names[0]
        suffix = ".gz" if name.lower().endswith(".gz") else Path(name).suffix
        destination = destination_dir / f"garmin-{activity_id}{suffix}"
        destination.write_bytes(bundle.read(name))
        return destination


def sync_garmin(config: Dict[str, Any], state: Dict[str, Any]) -> int:
    source = state["sources"].setdefault("garmin", {})
    seen = set(source.get("seen_ids", []))
    garmin_dir = resolve_path(config["garmin_dir"])
    activity_dir = garmin_dir / "activities"
    activity_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = garmin_dir / "activities.csv"
    seen |= known_activity_ids(metadata_path)

    lookback = int(config.get("garmin_lookback_days", 7))
    today = dt.date.today()
    start_date = (today - dt.timedelta(days=lookback)).isoformat()
    end_date = (today + dt.timedelta(days=1)).isoformat()
    max_activities = int(config.get("max_activities_per_sync", 100))
    activities: List[Dict[str, Any]] = []
    start = 0
    while len(activities) < max_activities:
        batch = garmin_json(
            state,
            "/activitylist-service/activities/search/activities",
            {"start": start, "limit": min(100, max_activities), "startDate": start_date, "endDate": end_date},
        )
        if not isinstance(batch, list) or not batch:
            break
        activities.extend(batch)
        if len(batch) < 100:
            break
        start += len(batch)
    log(f"Garmin: {len(activities)} recent activity summary(ies), {len(seen)} already known")

    rows: List[Dict[str, Any]] = []
    skipped = 0
    failed = 0
    for activity in activities[:max_activities]:
        activity_id = str(activity.get("activityId") or "")
        if not activity_id or activity_id in seen:
            skipped += 1
            continue
        token = garmin_bearer_token(state)
        try:
            body, _ = http_request(
                f"{GARMIN_API}/download-service/files/activity/{activity_id}",
                headers={"Authorization": f"Bearer {token}"},
                accept="application/zip, application/x-zip-compressed, application/octet-stream",
            )
        except SyncError as exc:
            failed += 1
            log(f"Garmin {activity_id}: download failed ({exc})")
            seen.add(activity_id)
            continue
        track = extract_track(body, activity_dir, activity_id)
        if track is None:
            failed += 1
            log(f"Garmin {activity_id}: ZIP contained no GPX/FIT track")
            seen.add(activity_id)
            continue
        activity_type = activity.get("activityType") or {}
        filename = track.relative_to(garmin_dir).as_posix()
        rows.append(
            {
                "Activity ID": activity_id,
                "Activity Date": iso_date(activity.get("startTimeLocal") or activity.get("startTime")),
                "Activity Name": activity.get("activityName") or "",
                "Activity Type": garmin_activity_type(activity),
                "Activity Description": "",
                "Elapsed Time": activity.get("elapsedDuration") or "",
                "Distance": activity.get("distance") or "",
                "Elevation Gain": activity.get("elevationGain") or "",
                "Filename": filename,
            }
        )
        seen.add(activity_id)
        _ = activity_type

    added = append_activity_csv(metadata_path, rows)
    source["seen_ids"] = sorted(seen)[-10000:]
    source["last_sync"] = dt.datetime.now(dt.timezone.utc).isoformat()
    source["last_added"] = added
    save_state(state)
    log(f"Garmin: wrote {added} new activity(ies), {skipped} skipped, {failed} failed")
    return added


def find_ticks_url_from_downloads(downloads_dir: Path, since: float) -> Optional[Path]:
    candidates = []
    for path in downloads_dir.glob("*.csv"):
        name = path.name.lower()
        if ("tick" in name or "mountainproject" in name) and path.stat().st_mtime >= since:
            candidates.append(path)
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def sync_mountain_project(config: Dict[str, Any], state: Dict[str, Any]) -> int:
    source = state["sources"].setdefault("mountain_project", {})
    destination = resolve_path(config["ticks_csv"])
    before = file_sha256(destination) if destination.exists() else ""
    last_sync = float(source.get("last_ts") or 0)
    downloaded = find_ticks_url_from_downloads(resolve_path(config["downloads_dir"]), last_sync)
    url = str(config.get("mountain_project_ticks_url") or "").strip()

    if url:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            log("Mountain Project: playwright is not installed; using downloaded CSV instead")
        else:
            try:
                with sync_playwright() as playwright:
                    profile = PRIVATE_DIR / "mp_browser"
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile), headless=False
                    )
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                    link = page.get_by_text("Export CSV", exact=False).first
                    with page.expect_download(timeout=120_000) as download_info:
                        link.click()
                    download = download_info.value
                    temporary = destination.with_name(destination.name + ".download")
                    download.save_as(str(temporary))
                    context.close()
                    temporary.replace(destination)
                    log("Mountain Project: refreshed ticks CSV from the website")
                    downloaded = destination
            except Exception as exc:  # noqa: BLE001 - browser automation is best-effort
                log(f"Mountain Project: browser export failed ({exc}); trying Downloads")

    if downloaded and downloaded.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(downloaded, destination)
        log(f"Mountain Project: copied {downloaded.name} into {destination.relative_to(REPO_ROOT)}")

    if not destination.exists():
        log("Mountain Project: no ticks CSV found; skipping (use 'mountain-login' to set it up)")
        return 0
    after = file_sha256(destination)
    changed = int(after != before)
    source["last_sync"] = dt.datetime.now(dt.timezone.utc).isoformat()
    source["last_ts"] = time.time()
    source["sha256"] = after
    save_state(state)
    log(f"Mountain Project: ticks CSV {'changed' if changed else 'unchanged'}")
    return changed


def run_build(config: Dict[str, Any], sources: Sequence[str]) -> None:
    """Rebuild the whole atlas, regardless of which sources were polled.

    Even a Garmin-only poll must include Strava and ticks in the build; otherwise
    the published aggregates would be computed from a partial dataset.
    """
    _ = sources  # kept for call-site compatibility; all available sources are included
    command = [
        sys.executable,
        str(SCRIPT_DIR / "build_atlas.py"),
        "--download-watersheds",
        "--weather",
    ]
    strava_dir = resolve_path(config["strava_dir"])
    garmin_dir = resolve_path(config["garmin_dir"])
    ticks_csv = resolve_path(config["ticks_csv"])
    if strava_dir.exists():
        command.extend(["--strava-dir", str(strava_dir)])
    if garmin_dir.exists():
        command.extend(["--garmin-dir", str(garmin_dir)])
    if ticks_csv.exists():
        command.extend(["--ticks-csv", str(ticks_csv)])
    if config.get("refresh_crag_coords") and ticks_csv.exists():
        crag_command = [
            sys.executable,
            str(SCRIPT_DIR / "fetch_crag_coords.py"),
            "--ticks",
            str(ticks_csv),
        ]
        log("refreshing public crag coordinates")
        subprocess.run(crag_command, check=True, cwd=str(REPO_ROOT))
    log("rebuilding atlas data")
    subprocess.run(command, check=True, cwd=str(REPO_ROOT))


def git_output(*args: str, env: Optional[Dict[str, str]] = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), check=True, capture_output=True, text=True, env=env
    )
    return result.stdout.strip()


def publish_data(config: Dict[str, Any]) -> None:
    if not config.get("auto_publish", True):
        log("auto_publish is disabled; data was rebuilt locally only")
        return
    branch = str(config.get("branch") or "dev")
    current = git_output("rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise SyncError(f"sync publishes from {branch}; current branch is {current}")
    if not git_output("status", "--porcelain", "--", "data"):
        log("no data changes to publish")
        return
    git_output("add", "--", "data")
    git_output("commit", "-m", "Update adventure atlas data from activity sources")
    push_env = os.environ.copy()
    push_env["GIT_ASKPASS"] = str(SCRIPT_DIR / "git_askpass.py")
    push_env["GIT_TERMINAL_PROMPT"] = "0"
    git_output("push", "origin", f"HEAD:refs/heads/{branch}", env=push_env)
    log(f"pushed data changes to {branch}")

    token = get_secret("github_token")
    if not token:
        log("no GitHub token stored; open a PR manually for the pushed data")
        return
    base = str(config.get("base_branch") or "master")
    body = {
        "title": "Update adventure atlas data",
        "head": branch,
        "base": base,
        "body": "Automated data refresh from Strava, Garmin, and Mountain Project. Review only; not merged automatically.",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    list_url = f"https://api.github.com/repos/{GITHUB_REPO}/pulls?state=open&head={GITHUB_REPO.split('/')[0]}:{branch}&base={base}"
    try:
        existing = request_json(list_url, headers=headers)
        if isinstance(existing, list) and existing:
            number = existing[0].get("number")
            request_json(
                f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{number}",
                method="PATCH",
                headers=headers,
                json_body=body,
            )
            log(f"updated PR #{number} for {branch} -> {base}")
            return
        created = request_json(
            f"https://api.github.com/repos/{GITHUB_REPO}/pulls",
            method="POST",
            headers=headers,
            json_body=body,
        )
        log(f"opened PR #{created.get('number')} for {branch} -> {base} (not merged)")
    except SyncError as exc:
        log(f"GitHub PR step failed ({exc}); data was already pushed to {branch}")


def setup_strava() -> None:
    client_id = getpass.getpass("Strava client ID: ").strip()
    client_secret = getpass.getpass("Strava client secret: ").strip()
    if not client_id or not client_secret:
        raise SyncError("client ID and secret are required")
    put_secret("strava_client_id", client_id)
    put_secret("strava_client_secret", client_secret)
    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": "http://localhost/exchange_token",
            "response_type": "code",
            "approval_prompt": "force",
            "scope": "activity:read_all",
        }
    )
    log(f"open this URL, authorize the app, then paste the code from the redirect URL:\n{STRAVA_AUTH}?{params}")
    log("then run: python3 scripts/sync_sources.py authorize-strava")


def authorize_strava() -> None:
    client_id = require_secret("strava_client_id", "Run setup-strava first")
    client_secret = require_secret("strava_client_secret", "Run setup-strava first")
    code = getpass.getpass("Strava authorization code: ").strip()
    result = request_json(
        STRAVA_TOKEN,
        method="POST",
        form={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    refresh = result.get("refresh_token") if isinstance(result, dict) else None
    if not refresh:
        raise SyncError(f"authorization failed: {str(result)[:300]}")
    put_secret("strava_refresh_token", str(refresh))
    state = load_state()
    state["sources"].pop("strava", None)
    save_state(state)
    log("Strava authorized; refresh token stored. Run: python3 scripts/sync_sources.py sync")


def setup_simple(name: str, prompt: str, hint: str) -> None:
    value = getpass.getpass(prompt).strip()
    if not value:
        raise SyncError(f"{name} is required")
    put_secret(name, value)
    log(hint)


def mountain_login(config: Dict[str, Any]) -> None:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        raise SyncError(
            "mountain-login needs playwright: python3 -m pip install playwright && "
            "python3 -m playwright install chromium"
        )
    profile = PRIVATE_DIR / "mp_browser"
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=False
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.mountainproject.com", wait_until="domcontentloaded")
        input("Log in to Mountain Project in the browser, then press Enter here... ")
        page.goto("https://www.mountainproject.com", wait_until="domcontentloaded")
        links = page.locator('a[href*="/ticks"]')
        ticks_url = ""
        for index in range(min(links.count(), 20)):
            href = links.nth(index).get_attribute("href") or ""
            text = links.nth(index).inner_text().strip().lower()
            if "/user/" in href and ("view all" in text or "tick" in text):
                ticks_url = urllib.parse.urljoin("https://www.mountainproject.com", href)
                break
        context.close()
    if ticks_url:
        config["mountain_project_ticks_url"] = ticks_url
        save_config(config)
        log(f"saved ticks URL: {ticks_url}")
    else:
        log("could not detect the ticks URL; set mountain_project_ticks_url in private/sync_config.json")


def install_scheduler(config: Dict[str, Any]) -> None:
    interval = max(5, int(config.get("interval_minutes", 15)))
    python = sys.executable
    script = SCRIPT_DIR / "sync_sources.py"
    programArguments = [python, str(script), "sync", "--scheduled"]
    plist = {
        "Label": "com.klepikhina.atlas-sync",
        "ProgramArguments": programArguments,
        "WorkingDirectory": str(REPO_ROOT),
        "StartInterval": interval * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")},
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    with LAUNCH_AGENT.open("wb") as handle:
        plistlib.dump(plist, handle)
    subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True, text=True)
    result = subprocess.run(
        ["launchctl", "load", "-w", str(LAUNCH_AGENT)], capture_output=True, text=True
    )
    if result.returncode:
        raise SyncError(f"launchctl load failed: {result.stderr.strip()}")
    log(f"installed launchd agent: every {interval} min, log at {LOG_PATH.relative_to(REPO_ROOT)}")


def uninstall_scheduler() -> None:
    if LAUNCH_AGENT.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True, text=True)
        LAUNCH_AGENT.unlink()
        log("removed launchd agent")
    else:
        log("no launchd agent installed")


def status(config: Dict[str, Any], state: Dict[str, Any]) -> None:
    log(f"repo: {REPO_ROOT}")
    log(f"config: {CONFIG_PATH.relative_to(REPO_ROOT)} (exists={CONFIG_PATH.exists()})")
    for name in SECRETS:
        env_key, _ = SECRETS[name]
        log(f"secret {name}: {'set' if get_secret(name) else 'MISSING'} [{env_key}]")
    for source_name, source in sorted(state.get("sources", {}).items()):
        log(
            f"source {source_name}: last_sync={source.get('last_sync', 'never')} "
            f"last_added={source.get('last_added', 0)}"
        )
    agent = "installed" if LAUNCH_AGENT.exists() else "not installed"
    log(f"scheduler: {agent} (interval {config.get('interval_minutes', 15)} min)")
    log(f"publish: {'on' if config.get('auto_publish', True) else 'off'} to {config.get('branch', 'dev')}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="sync all sources, rebuild, and publish")
    sync_parser.add_argument(
        "--sources",
        default="strava,garmin,mountain",
        help="comma-separated subset: strava,garmin,mountain",
    )
    sync_parser.add_argument("--dry-run", action="store_true", help="check credentials/state without network or git")
    sync_parser.add_argument("--no-publish", action="store_true", help="rebuild locally without committing or pushing")
    sync_parser.add_argument("--scheduled", action="store_true", help="used by the launchd agent")

    subparsers.add_parser("setup-strava", help="store Strava client credentials and print the auth URL")
    subparsers.add_parser("authorize-strava", help="exchange a Strava authorization code for a refresh token")
    subparsers.add_parser("setup-garmin", help="store a Garmin Personal API key")
    subparsers.add_parser("setup-github", help="store a GitHub token for PR creation")
    subparsers.add_parser("mountain-login", help="log in once to Mountain Project in a persistent browser")
    subparsers.add_parser("install-scheduler", help="install the launchd polling agent")
    subparsers.add_parser("uninstall-scheduler", help="remove the launchd polling agent")
    subparsers.add_parser("status", help="show sync configuration and credential status")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config()
    state = load_state()

    if args.command == "status":
        status(config, state)
        return 0
    if args.command == "setup-strava":
        setup_strava()
        return 0
    if args.command == "authorize-strava":
        authorize_strava()
        return 0
    if args.command == "setup-garmin":
        setup_simple("garmin_api_key", "Garmin Personal API key: ", "Garmin key stored. Run: python3 scripts/sync_sources.py sync")
        return 0
    if args.command == "setup-github":
        setup_simple(
            "github_token",
            "GitHub personal access token (repo scope): ",
            "GitHub token stored. It is only used to open/update a PR; never to merge.",
        )
        return 0
    if args.command == "mountain-login":
        mountain_login(config)
        return 0
    if args.command == "install-scheduler":
        install_scheduler(config)
        return 0
    if args.command == "uninstall-scheduler":
        uninstall_scheduler()
        return 0

    sources = [part.strip().lower() for part in args.sources.split(",") if part.strip()]
    unknown = set(sources) - {"strava", "garmin", "mountain"}
    if unknown:
        raise SyncError(f"unknown source(s): {', '.join(sorted(unknown))}")
    if args.dry_run:
        status(config, state)
        log(f"dry run: would sync {', '.join(sources)}; no network, build, or git changes made")
        return 0

    added = 0
    if "strava" in sources:
        try:
            added += sync_strava(config, state)
        except SyncError as exc:
            log(f"Strava sync skipped: {exc}")
    if "garmin" in sources:
        try:
            added += sync_garmin(config, state)
        except SyncError as exc:
            log(f"Garmin sync skipped: {exc}")
    if "mountain" in sources:
        try:
            added += sync_mountain_project(config, state)
        except SyncError as exc:
            log(f"Mountain Project sync skipped: {exc}")

    run_build(config, sources)
    if not args.no_publish:
        publish_data(config)
    state["runs"] = (state.get("runs") or [])[-19:]
    state["runs"].append(
        {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sources": sources,
            "new_items": added,
            "scheduled": bool(args.scheduled),
        }
    )
    save_state(state)
    log("sync complete")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"command failed (exit {exc.returncode}): {' '.join(exc.cmd)}", file=sys.stderr)
        sys.exit(3)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)
