#!/usr/bin/env python3
"""Weekly cloud sync for the Adventure Atlas.

Runs in GitHub Actions. It keeps a compact parsed-state cache in a private GitHub
repo, polls Garmin (and Strava when configured), rebuilds the atlas from cached
summaries, and publishes only aggregate GeoJSON directly to master.

Raw FIT/GPX files and Mountain Project CSVs exist only in the runner's temporary
directory and are discarded when the job ends. Nothing private is committed to
the public site repo.

Required secrets:
  GITHUB_TOKEN                 built-in Actions token (contents: write)
  ATLAS_PRIVATE_REPO_TOKEN     token with write access to ATLAS_PRIVATE_REPO
  GARMIN_API_KEY               Garmin Connect Personal API key
  MOUNTAIN_PROJECT_TICKS_B64   base64 of the private ticks CSV (optional)

Optional Strava secrets: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET,
STRAVA_REFRESH_TOKEN.

One-time local seeding:
  ATLAS_PRIVATE_REPO_TOKEN=... python3 scripts/cloud_sync.py seed-private
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
PRIVATE_REPO = os.environ.get("ATLAS_PRIVATE_REPO", "klepikhina/atlas-private")
PRIVATE_BRANCH = os.environ.get("ATLAS_PRIVATE_BRANCH", "main")
ATLAS_STATE_NAME = "atlas_state.json"
SYNC_STATE_NAME = "sync_state.json"

sys.path.insert(0, str(SCRIPT_DIR))
import build_atlas as atlas  # noqa: E402
import sync_sources as sync  # noqa: E402


def log(message: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {message}", flush=True)


def require_env(name: str, hint: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise sync.SyncError(f"missing {name}. {hint}".strip())
    return value


def github_token(local_seed: bool = False) -> str:
    name = "ATLAS_PRIVATE_REPO_TOKEN" if local_seed else "GITHUB_TOKEN"
    return require_env(name, "store it as a GitHub Actions secret")


def github_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def contents_url(path: str) -> str:
    encoded = urllib.parse.quote(path, safe="/")
    return f"https://api.github.com/repos/{PRIVATE_REPO}/contents/{encoded}"


def github_get_file(path: str, token: str) -> Optional[bytes]:
    try:
        result = sync.request_json(
            contents_url(path),
            headers=github_headers(token),
            params={"ref": PRIVATE_BRANCH},
        )
    except sync.SyncError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    if not isinstance(result, dict) or result.get("encoding") != "base64":
        return None
    return base64.b64decode(result.get("content") or "")


def github_put_file(path: str, content: bytes, token: str, message: str) -> None:
    payload: Dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": PRIVATE_BRANCH,
    }
    existing = None
    try:
        existing = sync.request_json(
            contents_url(path), headers=github_headers(token), params={"ref": PRIVATE_BRANCH}
        )
    except sync.SyncError as exc:
        if "HTTP 404" not in str(exc):
            raise
    if isinstance(existing, dict) and existing.get("sha"):
        payload["sha"] = existing["sha"]
    sync.request_json(
        contents_url(path), method="PUT", headers=github_headers(token), json_body=payload
    )
    log(f"updated private state file {path}")


def normalize_cache_keys(cache: Dict[str, Any], source: str) -> None:
    """Use stable cache keys instead of runner-local file paths."""
    replacements: Dict[str, str] = {}
    for key, entry in cache.items():
        if str(key).startswith("cloud://"):
            continue
        record = entry.get("record") if isinstance(entry, dict) else None
        slug = record.get("slug") if isinstance(record, dict) else Path(str(key)).stem
        replacements[str(key)] = f"cloud://{source}/{slug}"
    for old_key, new_key in replacements.items():
        cache[new_key] = cache.pop(old_key, cache.get(new_key))


def write_atlas_state(atlas_state: Dict[str, Any], temp: Path) -> Path:
    """Materialise the in-memory cache so the rebuild sees newly parsed tracks."""
    path = temp / ATLAS_STATE_NAME
    path.write_text(
        json.dumps(atlas_state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def sync_state_for_storage() -> bytes:
    """Do not persist short-lived bearer/access tokens in the private repo."""
    state = sync.load_state()
    for source in state.get("sources", {}).values():
        if isinstance(source, dict):
            source.pop("access_token", None)
    return (json.dumps(state, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def load_private_state(temp: Path, token: str) -> Dict[str, Any]:
    atlas_path = temp / ATLAS_STATE_NAME
    raw = github_get_file(ATLAS_STATE_NAME, token)
    if raw:
        atlas_path.write_bytes(raw)
        state = json.loads(raw.decode("utf-8"))
    else:
        state = atlas.empty_state()
        log(f"no {ATLAS_STATE_NAME} in private repo; starting with an empty cache")
    if not isinstance(state, dict):
        state = atlas.empty_state()
    state.setdefault("strava", {})
    state.setdefault("garmin", {})

    sync.STATE_PATH = temp / SYNC_STATE_NAME
    raw_sync = github_get_file(SYNC_STATE_NAME, token)
    if raw_sync:
        sync.STATE_PATH.write_bytes(raw_sync)
    else:
        sync.save_state({"version": 1, "sources": {}, "runs": []})
    return state


def save_private_state(atlas_state: Dict[str, Any], temp: Path, token: str) -> None:
    normalize_cache_keys(atlas_state["strava"], "strava")
    normalize_cache_keys(atlas_state["garmin"], "garmin")
    atlas_path = write_atlas_state(atlas_state, temp)
    message = f"Update atlas sync state {dt.datetime.now(dt.timezone.utc).date().isoformat()}"
    github_put_file(ATLAS_STATE_NAME, atlas_path.read_bytes(), token, message)
    github_put_file(SYNC_STATE_NAME, sync_state_for_storage(), token, message)


def write_ticks_secret(temp: Path) -> Optional[Path]:
    encoded = os.environ.get("MOUNTAIN_PROJECT_TICKS_B64", "").strip()
    if not encoded:
        log("Mountain Project: no ticks secret configured; skipping")
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise sync.SyncError(f"MOUNTAIN_PROJECT_TICKS_B64 is not valid base64: {exc}") from exc
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
    config["garmin_lookback_days"] = 14
    config["strava_lookback_days"] = 14
    config["auto_publish"] = False
    return config


def sync_sources(atlas_state: Dict[str, Any], config: Dict[str, Any], temp: Path) -> None:
    sync_state = sync.load_state()

    if os.environ.get("KLEPIKINA_GARMIN_API_KEY", "").strip():
        sync.sync_garmin(config, sync_state)
        garmin_dir = Path(config["garmin_dir"])
        if garmin_dir.exists():
            atlas.load_garmin_activities(garmin_dir, atlas_state["garmin"], prune_cache=False)
            normalize_cache_keys(atlas_state["garmin"], "garmin")
    else:
        log("Garmin: no API key configured; skipping")

    if strava_configured():
        sync.sync_strava(config, sync_state)
        strava_dir = Path(config["strava_dir"])
        if strava_dir.exists():
            atlas.load_strava_activities(strava_dir, atlas_state["strava"], prune_cache=False)
            normalize_cache_keys(atlas_state["strava"], "strava")
    else:
        log("Strava: no API credentials configured; skipping")

    sync.save_state(sync_state)


def run_build(temp: Path, ticks: Optional[Path]) -> None:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "build_atlas.py"),
        "--state-path",
        str(temp / ATLAS_STATE_NAME),
        "--use-cache-records",
        "--download-watersheds",
    ]
    if ticks and ticks.exists():
        command.extend(["--ticks-csv", str(ticks)])
    log("rebuilding atlas from cached summaries")
    subprocess.run(command, check=True, cwd=str(REPO_ROOT))


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), check=check, capture_output=True, text=True
    )


def publish_master() -> None:
    git("config", "user.name", "Atlas Sync")
    git("config", "user.email", "atlas-sync@users.noreply.github.com")
    git("add", "--", "data")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        log("no aggregate data changes to publish")
        return
    git("commit", "-m", "Weekly atlas data update [skip ci]")
    git("push", "origin", "HEAD:refs/heads/master")
    log("published aggregate data to master")


def seed_private(state_path: Path, sync_state_path: Path, token: str) -> None:
    if not state_path.exists():
        raise sync.SyncError(f"missing atlas cache: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.setdefault("strava", {})
    state.setdefault("garmin", {})
    normalize_cache_keys(state["strava"], "strava")
    normalize_cache_keys(state["garmin"], "garmin")
    message = f"Seed atlas sync state {dt.datetime.now(dt.timezone.utc).date().isoformat()}"
    seeded = json.dumps(state, indent=1, ensure_ascii=False).encode("utf-8")
    github_put_file(ATLAS_STATE_NAME, seeded, token, message)

    if sync_state_path.exists():
        original = sync.STATE_PATH
        try:
            sync.STATE_PATH = sync_state_path.expanduser()
            github_put_file(SYNC_STATE_NAME, sync_state_for_storage(), token, message)
        finally:
            sync.STATE_PATH = original
    else:
        github_put_file(
            SYNC_STATE_NAME,
            (json.dumps({"version": 1, "sources": {}, "runs": []}) + "\n").encode(),
            token,
            message,
        )
    log(f"seeded private repo {PRIVATE_REPO}")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="rebuild locally without uploading state or publishing")
    subparsers = parser.add_subparsers(dest="command")
    seed = subparsers.add_parser("seed-private", help="upload the current local cache to the private repo")
    seed.add_argument("--state", type=Path, default=REPO_ROOT / "private" / "atlas_state.json")
    seed.add_argument("--sync-state", type=Path, default=REPO_ROOT / "private" / "sync_state.json")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "seed-private":
        seed_private(
            args.state.expanduser(),
            args.sync_state.expanduser(),
            github_token(local_seed=True),
        )
        return 0

    token = github_token()
    with tempfile.TemporaryDirectory(prefix="atlas-cloud-") as directory:
        temp = Path(directory)
        atlas_state = load_private_state(temp, token)
        ticks = write_ticks_secret(temp)
        config = cloud_config(temp, ticks)
        sync_sources(atlas_state, config, temp)
        write_atlas_state(atlas_state, temp)
        run_build(temp, ticks)
        if args.dry_run:
            log("dry run: no state upload or publish")
            return 0
        save_private_state(atlas_state, temp, token)
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
