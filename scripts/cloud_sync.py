#!/usr/bin/env python3
"""Weekly cloud sync for the Adventure Atlas.

Runs in GitHub Actions. It pages through the full Garmin and Strava activity
summaries, rebuilds the atlas from those fresh exports, and publishes only
aggregate GeoJSON directly to master. No private state repo, local seeding,
or extra repo token is required.

Raw FIT/GPX files and Mountain Project CSVs exist only in the runner's temporary
directory and are discarded when the job ends. Nothing private is committed to
the public site repo.

Required secrets/env:
  GITHUB_TOKEN                 built-in Actions token, used by git push only
  GARMIN_API_KEY               Garmin Connect Personal API key
  MOUNTAIN_PROJECT_TICKS_B64   base64 of the private ticks CSV (optional)

Optional Strava secrets: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET,
STRAVA_REFRESH_TOKEN.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import gzip
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
ATLAS_STATE_NAME = "atlas_state.json"
SYNC_STATE_NAME = "sync_state.json"

sys.path.insert(0, str(SCRIPT_DIR))
import sync_sources as sync  # noqa: E402


def log(message: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {message}", flush=True)


def write_ticks_secret(temp: Path) -> Optional[Path]:
    encoded = os.environ.get("MOUNTAIN_PROJECT_TICKS_B64", "").strip()
    if not encoded:
        log("Mountain Project: no ticks secret configured; skipping")
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise sync.SyncError(f"MOUNTAIN_PROJECT_TICKS_B64 is not valid base64: {exc}") from exc
    # GitHub Actions secrets have a size limit, so the CSV is usually gzipped
    # before base64 encoding. Accept plain CSV too for older setups.
    if raw.startswith(b"\x1f\x8b"):
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise sync.SyncError(f"MOUNTAIN_PROJECT_TICKS_B64 gzip data is invalid: {exc}") from exc
    path = temp / "mountain-project-ticks.csv"
    path.write_bytes(raw)
    log(f"Mountain Project: staged {len(raw):,} bytes of private ticks data")
    return path


def strava_configured() -> bool:
    return all(
        os.environ.get(name, "").strip()
        for name in (
            "KLEPIKINA_STRAVA_CLIENT_ID",
            "KLEPIKINA_STRAVA_CLIENT_SECRET",
            "KLEPIKINA_STRAVA_REFRESH_TOKEN",
        )
    )


def cloud_config(temp: Path, ticks: Optional[Path]) -> Dict[str, Any]:
    config = dict(sync.DEFAULT_CONFIG)
    config["strava_dir"] = str(temp / "strava_export")
    config["garmin_dir"] = str(temp / "garmin_export")
    config["ticks_csv"] = str(ticks) if ticks else str(temp / "mountain-project-ticks.csv")
    # Rebuild from the full source history each week. Summaries are cheap to
    # page through; raw tracks are never committed and no private cache/token
    # is needed. Strava stream endpoints are deliberately not used.
    config["garmin_lookback_days"] = 7_300
    config["strava_lookback_days"] = 7_300
    config["max_activities_per_sync"] = 10_000
    config["auto_publish"] = False
    return config


def sync_sources(config: Dict[str, Any]) -> None:
    sync_state = sync.load_state()

    if os.environ.get("KLEPIKINA_GARMIN_API_KEY", "").strip():
        sync.sync_garmin(config, sync_state)
    else:
        log("Garmin: no API key configured; skipping")

    if strava_configured():
        sync.sync_strava(config, sync_state)
    else:
        log("Strava: no API credentials configured; skipping")

    sync.save_state(sync_state)


def run_build(temp: Path, ticks: Optional[Path], config: Dict[str, Any]) -> None:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "build_atlas.py"),
        "--strava-dir",
        config["strava_dir"],
        "--garmin-dir",
        config["garmin_dir"],
        "--regions-geojson",
        "data/regions.geojson",
        "--state-path",
        str(temp / ATLAS_STATE_NAME),
        "--download-watersheds",
        "--weather",
    ]
    if ticks and ticks.exists():
        command.extend(["--ticks-csv", str(ticks)])
    log("rebuilding atlas from current source exports")
    subprocess.run(command, check=True, cwd=str(REPO_ROOT))
    log("rebuilding story photo thumbnails")
    subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "build_thumbs.py")],
        check=True,
        cwd=str(REPO_ROOT),
    )


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), check=check, capture_output=True, text=True
    )


def publish_master() -> None:
    git("config", "user.name", "Atlas Sync")
    git("config", "user.email", "atlas-sync@users.noreply.github.com")
    git("add", "--", "data", "region", "images/story-thumbs")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        log("no aggregate data or region page changes to publish")
        return
    git("commit", "-m", "Weekly atlas data update [skip ci]")
    git("push", "origin", "HEAD:refs/heads/master")
    log("published aggregate data to master")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="rebuild locally without publishing")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="atlas-cloud-") as directory:
        temp = Path(directory)
        sync.STATE_PATH = temp / SYNC_STATE_NAME
        ticks = write_ticks_secret(temp)
        config = cloud_config(temp, ticks)
        sync_sources(config)
        run_build(temp, ticks, config)
        if args.dry_run:
            log("dry run: no publish")
            return 0
        publish_master()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except sync.SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"command failed (exit {exc.returncode}): {' '.join(exc.cmd)}", file=sys.stderr)
        sys.exit(3)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)
