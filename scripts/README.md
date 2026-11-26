# Adventure Atlas

`build_atlas.py` produces the atlas layers plus the shared data used by every
Jekyll page:

| File | Layer | Contents |
| --- | --- | --- |
| `data/activity_density.geojson` | Heatmap | Anonymised coarse-cell counts (no coordinates or tracks) |
| `data/regions.geojson` | Choropleth | One state/province/country polygon with **aggregate** counts only |
| `data/featured_routes.geojson` | Markers / lines | Hand-curated, blog-linked routes and crags |
| `data/activities.json` | Site cards | One canonical record per curated story (slug, title, date, sport, region, image, summary) |
| `data/region_pages.json` | Region nav | Generated region page definitions and nav groups; only regions with a featured story are listed |
| `region/*.html` | Region pages | Static pages generated from `data/region_pages.json`; empty region pages are removed |

Jekyll reads these files through `data_sources` in `_config.yml`, so Home,
Activities, the type pages, and region pages all use the same activity records.
`js/books.json` is a separate hand-curated tree used only by the Books page and
the three recent-book cards on Home.

Everything the browser sees is generated from:

- YAML front matter in `*_posts/*.md` (the public blog), and/or
- private Strava/Garmin exports and a Mountain Project ticks CSV that stay on your machine.

The atlas is global: Natural Earth admin-1 polygons cover US states and Canadian
provinces, and admin-0 polygons cover every other country, so California,
Costa Rica, Iceland, Georgia (the country), Japan, and everywhere else in the
exports can appear.

## Privacy model

Raw GPS traces and tick exports are the sensitive part of an adventure log: they
reveal home addresses, habitual loops, notes, and exact times. The pipeline is
built so they never enter the repository or the published site.

> **Never put a Strava export or a Mountain Project ticks CSV in `data/`.**
> `data/` is copied verbatim into `_site/` by Jekyll. Keep private exports in
> `private/` at the repo root: it is gitignored and excluded from the build.

- Raw GPX/FIT files are streamed, reduced to distance/gain, and discarded. No
  track geometry, timestamps, or file names are written.
- Mountain Project ticks are reduced to a count per state/province/country. Route
  names, notes, personal ratings, style, and URLs stay in the private CSV. Only
  public crag coordinates (from `scripts/crag_coords.json`) are committed.
- `regions.geojson` contains only admin polygons plus aggregate numbers
  (`activity_count`, per-sport `stats`, `featured_story_count`). Ski stats include
  only the count and share of tours / alpine / Nordic days, never individual
  activities.
- Route geometry is written **only** for tracks you explicitly list in a blogged
  GPX manifest, i.e. tracks already published with a trip report. Those lines
  are trimmed 500 m at each end and written without timestamps.
- When `total_vert_ft` or `total_distance_mi` only covers some activities, the
  `vert_coverage` / `distance_coverage` fields say so, and the site labels the
  legend accordingly.

## Setup

```bash
python3 -m pip install -r scripts/requirements.txt
```

The browser map uses the free, keyless **OpenFreeMap Positron** style for its
basemap and AWS Terrarium tiles for 3D terrain — no API keys or tile-policy
blocking. Credits are added automatically by MapLibre.

The watershed source is HydroBASINS **global** level 8 (v1c): one ~5–50 MB zip
per continent, downloaded on demand and cached in `~/.cache/klepikhina-atlas`
(override with `KLEPIKINA_ATLAS_CACHE`). Public Natural Earth country polygons
are cached there too, for naming regions outside the blog.

## Weekly cloud sync

`.github/workflows/atlas-sync.yml` runs every **Monday at 06:15 UTC** and can also
be started manually with **Run workflow**. It runs in GitHub Actions, so your
Mac does not need to be on. Each run:

1. polls Garmin (and Strava when configured) for new activities,
2. reads the Mountain Project ticks CSV from an encrypted secret,
3. rebuilds the atlas from a compact parsed cache in a private repo,
4. commits only aggregate `data/*.geojson` directly to `master`, and
5. lets GitHub Pages rebuild the site.

Routine activity updates **do not create a PR**.

One-time setup:

1. Create an empty **private** GitHub repo for the sync state, for example
   `klepikhina/atlas-private`. If you use a different name, add a repository
   variable `ATLAS_PRIVATE_REPO` with that value.
2. Add these Actions secrets to this repository:

   | Secret | Purpose |
   | --- | --- |
   | `GARMIN_API_KEY` | Garmin Connect Personal API key (required) |
   | `ATLAS_PRIVATE_REPO_TOKEN` | Token with write access to the private state repo |
   | `MOUNTAIN_PROJECT_TICKS_B64` | Base64 of `private/mountain-project-ticks.csv` |
   | `STRAVA_CLIENT_ID` | Optional Strava app client ID |
   | `STRAVA_CLIENT_SECRET` | Optional Strava app client secret |
   | `STRAVA_REFRESH_TOKEN` | Optional Strava refresh token |

   The built-in `GITHUB_TOKEN` pushes the aggregate data to `master`.

3. Seed the private state repo from your Mac once:

   ```bash
   ATLAS_PRIVATE_REPO_TOKEN=... python3 scripts/cloud_sync.py seed-private
   ```

   This uploads only `atlas_state.json` (parsed summaries) and `sync_state.json`
   (activity IDs and token expiry). No raw GPX/FIT files or ticks CSVs are
   uploaded.

4. Create the Mountain Project secret after each new CSV export:

   ```bash
   gzip -c private/mountain-project-ticks.csv | base64 | tr -d '\n' | pbcopy
   ```

   Gzip keeps the secret well under GitHub's size limit; the sync decompresses it automatically.

   Paste that value into the `MOUNTAIN_PROJECT_TICKS_B64` Actions secret.

- **Garmin:** free Personal API key from Garmin Connect → Settings → Personal
  API Token. The cloud job requests a fresh 24-hour token each week and imports
  activities from the last 14 days.
- **Strava:** optional; new API apps may require a paid subscription. A 403
  during token setup usually means that.
- **Garmin wins:** if an outing appears in both Strava and Garmin (same local
  date, activity filter, and distance within 12% / 0.5 mi), the Strava copy is
  dropped. Blog posts are still de-duplicated against the winner.
- **Privacy:** raw FIT/GPX files and the ticks CSV exist only in the runner's
  temporary directory and are discarded. The public repo receives only the
  aggregate/coarse-cell GeoJSON already shown on the site.

The older `sync_sources.py` commands still work for one-off local imports, but
the launchd scheduler and PR flow are no longer the recommended path.

## Build from the blog only

This is how the committed sample files were produced. It needs nothing except
the repository:

```bash
python3 scripts/build_atlas.py --download-watersheds
```

## Build with private activity data

For ongoing updates, use the **Weekly cloud sync** above. For a one-time manual
import, export from Strava (**Settings → My Account → Download or Delete Your
Account → Request your archive**) and download your Mountain Project ticks CSV.
Put them in `private/`, which is gitignored and excluded from Jekyll:

```bash
mkdir -p private
mv ~/Downloads/mountain-project-ticks.csv private/mountain-project-ticks.csv
mv ~/Downloads/strava_export private/strava_export

# Mountain Project CSVs have a location hierarchy but no coordinates.
# Resolve them once from public route pages (cached, polite delays):
python3 scripts/fetch_crag_coords.py \
    --ticks private/mountain-project-ticks.csv

python3 scripts/build_atlas.py \
    --strava-dir private/strava_export \
    --ticks-csv private/mountain-project-ticks.csv \
    --download-watersheds
```

Notes:

- `activities.csv` from the Strava archive supplies activity type, name, and
  date. If it is missing, the script falls back to the FIT session sport and the
  first GPS timestamp.
- TCX files are skipped with a warning; re-export as GPX or FIT.
- The ticks CSV is matched to coordinates via `scripts/crag_coords.json`, which
  is generated from public Mountain Project route pages and contains no personal
  data. If a new crag is missing, add it there as `"crag name": [lon, lat]` (or
  re-run `fetch_crag_coords.py` after a refresh).
- Ticks that cannot be matched to a state/province/country are ignored. Climbing
  counts come from ticks; distance and vert still come only from activities where
  those values were recorded (see `vert_coverage`).
- A blog post for the same day and sport as a private record is dropped to avoid
  double counting. Add `--include-posts` to keep every blog post as well.

## How regions are chosen

`regions_config.json` uses `"mode": "dynamic"` with `"region_basis": "admin"`.
There is no hand-maintained region list and no watershed polygon is drawn:

1. Public Natural Earth admin-1 polygons supply US states and Canadian provinces
   (`Colorado`, `California`, `British Columbia`, ...). Everywhere else, the
   Natural Earth admin-0 country polygon is the region (`Costa Rica`, `Colombia`,
   `Iceland`, `Georgia`, `Japan`, ...). A state sharing a country's name, such as
   Georgia (US), is labelled `Georgia (US)` to keep the ids distinct.
2. Every geocoded private activity, Mountain Project tick, and blog post is
   assigned to one of those polygons by point-in-polygon. Only polygons with at
   least one activity or story are written to `regions.geojson`.
3. The browser shades them green, draws their labels and borders, and puts the
   warm density heatmap on top. Clicking a region zooms to it and opens a drawer
   with only that region's stats.

Posts with nested `coordinates:` front matter are supported as well as flat
`latitude` / `longitude` keys.

To restore the older hydrologic regions, set `"region_basis": "watershed"` and
supply HydroBASINS level-8 shapefiles via `--watersheds` (or the cached global
dataset).

## Density heatmap

`data/activity_density.geojson` is a coarse grid (default `0.5°`, about 55 km)
of **cell counts only**. Private cells with fewer than two activities are
suppressed; public blog locations are always kept. No individual coordinates,
timestamps, or tracks are written. Each cell carries only counts (plus summed
vert / distance for the matching shading metrics). The browser draws this as a
warm heatmap on top of the green admin regions, which keep the hover stats.
The browser scales the heat from the data's quantiles (not one dense hometown),
keeps one- and two-activity cells faintly visible, and switches to a darker
local scale as you zoom in, so a state such as Kentucky gets its own contrast.

Change `density_cell_deg` / `density_min_count` in `regions_config.json` to make
the grid coarser or stricter.

## Stats shown in the browser

The map's activity-type filter changes both the tooltip/drawer stats and the available
shading metrics:

| Filter | Stats shown | Shading options |
| --- | --- | --- |
| All | Activities, stories | Activities |
| Bike / Hike / Run | Activities, vert, distance | Activities, vert, distance |
| Climb / Water | Activities | Activities |
| Ski | Activities, vert skied, % touring / alpine / Nordic | Activities, vert, touring, alpine, Nordic |

Ski style comes from Strava's `Activity Type` (`Backcountry Ski` → touring,
`Alpine Ski` / `Snowboard` → alpine, `Nordic Ski` → Nordic) and is read from
`activities.csv`, so enabling it does not force a re-parse of GPX/FIT files.
Water includes all Strava water sport types — `Stand Up Paddling`, `Swimming`,
`Kayaking`, `Canoeing`, `Rowing`, `Surfing`, `Water Sport`, windsurfing,
kitesurfing, sailing, rafting, diving, and similar names. Watersports are
classified before snow sports so `Waterskiing` is not counted as skiing.

## Incremental rebuilds

Parsed private data is cached in `private/atlas_state.json` (gitignored and
Jekyll-excluded). The first build has to read every GPX/FIT file once — a full
Strava archive can take several minutes — but later builds only parse what is
new or changed:

- **Strava:** a file is reused when its path, size, and modification time match
  the cache. Drop new `.gpx`/`.fit`/`.fit.gz` files into
  `private/strava_export/activities/` and rerun the same command; only those
  files are parsed. Re-exporting overwrites files with new mtimes, so those are
  parsed once and then cached again.
- **Mountain Project:** rows are keyed by the fields that affect the aggregates
  (route, date, location, rating, length, pitches, style). Append or replace the
  CSV with a full export and rerun; unchanged rows are reused, and removed rows
  are dropped from the cache. Editing `scripts/crag_coords.json` re-resolves all
  ticks once.
- **Dry runs:** `--dry-run` validates and prints the report but does not write
  `data/` or update the cache. Do a normal build to populate it.

To force a complete reparse, add `--no-cache`. To move the cache, use
`--state-path PATH`.

`build_atlas.py` also writes privacy-safe aggregates to `data/insights/`:

- `daily.json` — per-day, per-sport distance/vert totals; region and cell ids are
  stripped so a date can never be tied to a location
- `climbing.json` — Mountain Project tick counts by grade, style, and region
- `region_timeline.json` — first activity date per coarse public region
- `books.json` — shelf counts by year and format, derived from the public bookshelf
- `book_meta.json` — public book metadata (page count, publication year, broad
  genre) cached by Goodreads id from Open Library, with Goodreads filling any
  gaps; new books are fetched incrementally as they are added to `js/books.json`
- `weather.json` — Open-Meteo weather aggregated by year+month at coarse cell centres

Add `--weather` to fetch weather for newly logged months; existing cell/month rows
are reused, so the weekly sync only fetches new activity months. No raw exports,
route names, precise coordinates, or date-to-location joins are written to these
files.

```bash
# After adding more private data, just rerun the same command:
python3 scripts/build_atlas.py \
    --strava-dir private/strava_export \
    --ticks-csv private/mountain-project-ticks.csv

# Full rebuild, ignoring the cache:
python3 scripts/build_atlas.py \
    --strava-dir private/strava_export \
    --ticks-csv private/mountain-project-ticks.csv --no-cache
```

## Curated routes and blogged GPX

`scripts/curated_routes.json` is the allow-list for the marker layer. It currently
lists every geocoded blog post, so all trips appear as markers. Remove a slug to
hide it, or add `title`, `difficulty`, `summary`, or `post_url` overrides per
entry. Region overrides are optional; the pipeline derives the region from the
post coordinates.

To draw a real route line instead of a point, create a manifest of GPX files you
have already published:

```json
{
  "routes": [
    { "slug": "BerthoudPass", "gpx": "tracks/BerthoudPass.gpx" }
  ]
}
```

Then run:

```bash
python3 scripts/build_atlas.py \
    --blogged scripts/blogged_tracks.json \
    --trim-meters 500
```

Relative paths are resolved against the manifest. Each line is trimmed at both
ends and stripped of time data before it is simplified and written.

## Configuration

`scripts/regions_config.json` controls the global atlas and the generated site
pages. It uses Natural Earth admin-1 polygons for US states and Canadian
provinces and admin-0 polygons for countries; HydroBASINS is retained only as a
backup geometry source and for the coarse density grid.

The `region_groups` list defines the aggregate pages (`usa`, `canada`, `europe`,
`asia`, `international`) and the navigation groups. A group can select regions
by `region_kinds`, explicit `region_ids`, and `exclude_region_ids`:

```json
{
  "id": "europe",
  "name": "Europe",
  "description": "European countries",
  "region_ids": ["austria", "france", "iceland", "italy", "switzerland"]
}
```

Individual region pages are generated for every `region_id` in
`data/regions.geojson`. Add a country or state to a group when you want it to
appear under that aggregate page and in the corresponding nav submenu.

## Verification

```bash
# Report aggregates without touching data/
python3 scripts/build_atlas.py --download-watersheds --dry-run
```

The run prints a per-region table, the curated route list, and validation
warnings for empty geometry, zero-activity regions, or missing slugs.

## Flags

| Flag | Purpose |
| --- | --- |
| `--download-watersheds` | Fetch cached global admin/HydroBASINS geometry when missing |
| `--watersheds PATH [PATH ...]` | Use local shapefiles instead of all cached continents |
| `--regions-geojson PATH` | Reuse region polygons from an existing file |
| `--strava-dir PATH` | Private Strava export directory |
| `--garmin-dir PATH` | Private Garmin export directory (Garmin wins overlaps) |
| `--ticks-csv PATH` | Private Mountain Project ticks CSV |
| `--crag-coords PATH` | JSON location/crag → `[lon, lat]` (default `scripts/crag_coords.json`) |
| `--blogged PATH` | Manifest of published GPX tracks |
| `--trim-meters N` | Trim each end of blogged tracks (default `500`) |
| `--include-posts` | Merge blog posts into private aggregates |
| `--simplify-deg D` | Watershed simplification (default from config) |
| `--feature-simplify-deg D` | Route line simplification (default `0.0004`) |
| `--no-featured` | Skip `featured_routes.geojson` |
| `--state-path PATH` | Incremental cache path (default `private/atlas_state.json`) |
| `--no-cache` | Ignore and do not update the cache; reparse everything |
| `--dry-run` | Validate and report only; does not write data or cache |
