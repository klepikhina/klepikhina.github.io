#!/usr/bin/env python3
"""Build small local thumbnails for the curated story photos.

The blog images live on S3 at full resolution (often 5000px+ / several MB).
The photo map uses them as 38px pins and the card grid as small tiles, so
serving the originals makes the page download well over 100 MB. This script
creates two compact versions per activity, keyed by activity id:

  images/story-thumbs/<id>-pin.jpg   96x96 cover   (map pins)
  images/story-thumbs/<id>-card.jpg  480x360 cover (cards and popups)

Existing thumbnails are left alone, so the weekly sync only downloads new
photos. A manifest at data/story-thumbs.json lists the ids with complete sets
so the Jekyll templates can prefer thumbnails and fall back to the originals.
Originals are never copied into the repository or published.
"""

from __future__ import annotations

import argparse
import io
import json
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from PIL import Image, ImageOps
except ImportError as exc:  # pragma: no cover - dependency missing
    raise SystemExit("Pillow is required: pip install -r scripts/requirements.txt") from exc

ROOT = Path(__file__).resolve().parent.parent
ACTIVITIES = ROOT / "data" / "activities.json"
OUT_DIR = ROOT / "images" / "story-thumbs"
MANIFEST = ROOT / "data" / "story-thumbs.json"

SIZES = {
    "pin": (96, 96),
    "card": (480, 360),
}
USER_AGENT = "klepikhina-atlas-thumbnails/1.0"


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def thumb_path(activity_id: str, variant: str) -> Path:
    return OUT_DIR / f"{activity_id}-{variant}.jpg"


def missing_variants(activity_id: str) -> list[str]:
    return [name for name in SIZES if not thumb_path(activity_id, name).is_file()]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, context=ssl_context(), timeout=90) as response:
        return response.read()


def make_thumbnails(url: str, activity_id: str, variants: list[str]) -> Tuple[str, int]:
    raw = fetch(url)
    with Image.open(io.BytesIO(raw)) as source:
        source = ImageOps.exif_transpose(source)
        for variant in variants:
            size = SIZES[variant]
            image = ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
            target = thumb_path(activity_id, variant)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "JPEG", quality=80, optimize=True, progressive=True)
    return activity_id, len(raw)


def load_activities() -> list[Dict[str, Any]]:
    payload = json.loads(ACTIVITIES.read_text(encoding="utf-8"))
    return payload.get("activities", [])


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process this many activities (useful for a smoke test).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Number of parallel downloads (default: 6).",
    )
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = []
    for activity in load_activities():
        activity_id = activity.get("id")
        image = activity.get("image")
        if not activity_id or not image:
            continue
        variants = missing_variants(activity_id)
        if variants:
            jobs.append((image, activity_id, variants))
        if args.limit and len(jobs) >= args.limit:
            break

    if not jobs:
        print(f"thumbnails up to date ({OUT_DIR})")
        write_manifest()
        return 0

    print(f"building thumbnails for {len(jobs)} activities")
    created = 0
    failed = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(make_thumbnails, *job): job[1] for job in jobs}
        for future in as_completed(futures):
            activity_id = futures[future]
            try:
                _, _ = future.result()
                created += 1
            except Exception as exc:  # keep going; a missing photo is not fatal
                failed.append(f"{activity_id}: {exc}")
                print(f"failed {activity_id}: {exc}", file=sys.stderr)

    print(f"created {created} thumbnail sets; {len(failed)} failures")
    write_manifest()
    return 1 if failed and created == 0 else 0


def write_manifest() -> None:
    """Publish the ids that have complete thumbnail sets for Liquid templates."""
    ready = sorted(
        activity_id
        for activity in load_activities()
        if (activity_id := activity.get("id")) and not missing_variants(activity_id)
    )
    payload = (json.dumps({"ids": ready}, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    if MANIFEST.exists() and MANIFEST.read_bytes() == payload:
        return
    MANIFEST.write_bytes(payload)
    print(f"updated {MANIFEST.relative_to(ROOT)} ({len(ready)} ids)")


if __name__ == "__main__":
    raise SystemExit(main())
