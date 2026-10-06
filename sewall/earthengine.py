"""Bounded Earth Engine summaries computed where the imagery is held. No imagery is downloaded.

Two operations reduce public collections over a fixed, named site inside Earth
Engine and return only small summaries:

- chlorophyll_timeseries: per-scene mean NDCI over Sentinel-2 water pixels.
- alphaearth_context: the annual mean of the 64 AlphaEarth embedding bands.

The planner names a site from SITES; it never supplies a geometry, a
collection, an expression or code. Each call issues one getInfo request.
Earth Engine needs the optional earthengine-api package, existing credentials
(`earthengine authenticate` or a service account) and a registered Cloud
project in EARTHENGINE_PROJECT. The project ID is never written to a result.

Collections (Earth Engine Data Catalog, consulted 2026-10-05):
https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED
https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL
"""

from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
import re


S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
ALPHAEARTH_COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
S2_ATTRIBUTION = "Contains modified Copernicus Sentinel data, processed in Google Earth Engine."
ALPHAEARTH_ATTRIBUTION = "The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind."
PROJECT_VARIABLE = "EARTHENGINE_PROJECT"
# Named sites only. A bounding box is a coarse study window, not a surveyed boundary.
SITES = {
    "lafayette-river": {
        "label": "Lafayette River, Norfolk, Virginia (tidal tributary of the Elizabeth River)",
        "bbox": [-76.32, 36.88, -76.25, 36.915],
        "note": "Approximate bounding box drawn for this prototype; it includes shoreline and land, "
                "so Sentinel-2 summaries keep only pixels classified as water.",
    },
}
MAX_SCENES = 60
MAX_WINDOW_DAYS = 366
MAX_CLOUD_PERCENT = 60
S2_FIRST_DATE = date(2017, 3, 28)
ALPHAEARTH_FIRST_YEAR = 2017
S2_SCALE_METERS = 20
ALPHAEARTH_SCALE_METERS = 10
DEADLINE_MILLISECONDS = 60_000
BANDS = tuple(f"A{index:02d}" for index in range(64))
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_YEAR = re.compile(r"20[0-9]{2}\Z", re.ASCII)
_PROJECT = re.compile(r"(?:[a-z][a-z0-9-]{4,28}[a-z0-9])\Z", re.ASCII)
_IMAGE_ID = re.compile(r"[A-Za-z0-9_]{1,100}\Z", re.ASCII)


class EarthEngineError(RuntimeError):
    """Earth Engine is unavailable, or it returned an unusable result."""


def configured() -> bool:
    """Return True when a project is named and the earthengine-api package imports."""
    if not _PROJECT.fullmatch(os.environ.get(PROJECT_VARIABLE, "")):
        return False
    try:
        import ee  # noqa: F401
    except ImportError:
        return False
    return True


def check_site(site: str) -> dict:
    if site not in SITES:
        raise ValueError("site must be one of: " + ", ".join(sorted(SITES)))
    return SITES[site]


def check_window(start: str, end: str, today=None) -> tuple:
    """Validate an inclusive ISO date window of at most 366 days."""
    try:
        if not (_DATE.fullmatch(start) and _DATE.fullmatch(end)):
            raise ValueError
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise ValueError("start and end must be dates written YYYY-MM-DD") from None
    today = today or datetime.now(timezone.utc).date()
    if not S2_FIRST_DATE <= first <= last <= today:
        raise ValueError(f"the window must fall between {S2_FIRST_DATE.isoformat()} and today, with start before end")
    if (last - first).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"the window may span at most {MAX_WINDOW_DAYS} days")
    return first, last


def check_year(year: str, today=None) -> int:
    today = today or datetime.now(timezone.utc).date()
    if not isinstance(year, str) or not _YEAR.fullmatch(year) or not ALPHAEARTH_FIRST_YEAR <= int(year) < today.year:
        raise ValueError(f"year must be a completed year from {ALPHAEARTH_FIRST_YEAR}, written YYYY")
    return int(year)


def _initialize():
    project = os.environ.get(PROJECT_VARIABLE, "")
    if not _PROJECT.fullmatch(project):
        raise EarthEngineError(f"Earth Engine is not configured; set {PROJECT_VARIABLE} to a registered Cloud project")
    try:
        import ee
    except ImportError:
        raise EarthEngineError("Earth Engine needs the optional package: pip install 'sewall[earthengine]'") from None
    try:
        ee.Initialize(project=project)
        if callable(getattr(getattr(ee, "data", None), "setDeadline", None)):
            ee.data.setDeadline(DEADLINE_MILLISECONDS)
    except Exception:
        raise EarthEngineError("Earth Engine initialization failed; check existing credentials and project registration") from None
    return ee


def _fetch(computed):
    try:
        info = computed.getInfo()
    except Exception as exc:
        raise EarthEngineError(f"Earth Engine request failed ({type(exc).__name__}); no retry attempted") from None
    try:
        raw = json.dumps(info, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raise EarthEngineError("Earth Engine returned a non-JSON result") from None
    if len(raw) > 1_048_576:
        raise EarthEngineError("Earth Engine result exceeded the byte limit")
    if not isinstance(info, dict):
        raise EarthEngineError("Earth Engine returned an unexpected result")
    return info, {"utility": "earthengine_getInfo", "raw_sha256": hashlib.sha256(raw).hexdigest(),
                  "response_bytes": len(raw), "attempts": 1}


def _number(value, low=-1e9, high=1e9):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise EarthEngineError("Earth Engine returned an invalid number")
    return value


def _features(value):
    if isinstance(value, dict) and value.get("type") == "FeatureCollection":
        value = value.get("features")
    if not isinstance(value, list) or len(value) > MAX_SCENES:
        raise EarthEngineError("Earth Engine returned an invalid scene list")
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("properties"), dict):
            raise EarthEngineError("Earth Engine returned an invalid scene summary")
    return [item["properties"] for item in value]


def chlorophyll_timeseries(site: str, start: str, end: str, max_scenes: int = 20, compute=None) -> dict:
    """Per-scene mean NDCI, (B5 - B4) / (B5 + B4), over Sentinel-2 pixels classified as water."""
    spec = check_site(site)
    first, last = check_window(start, end)
    if type(max_scenes) is not int or not 1 <= max_scenes <= MAX_SCENES:
        raise ValueError(f"max_scenes must be an integer from 1 to {MAX_SCENES}")
    if compute is None:
        ee = _initialize()
        region = ee.Geometry.Rectangle(spec["bbox"])
        everything = (ee.ImageCollection(S2_COLLECTION).filterBounds(region)
                      .filterDate(first.isoformat(), date.fromordinal(last.toordinal() + 1).isoformat()))
        candidates = everything.filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", MAX_CLOUD_PERCENT))
        chosen = candidates.sort("system:time_start").limit(max_scenes)

        def summarize(image):
            water = image.select("SCL").eq(6)
            ndci = image.normalizedDifference(["B5", "B4"]).rename("ndci").updateMask(water)
            stats = ndci.reduceRegion(reducer=ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True),
                                      geometry=region, scale=S2_SCALE_METERS, maxPixels=10_000_000)
            return ee.Feature(None, {"image_id": image.get("system:index"),
                                     "time_start": image.get("system:time_start"),
                                     "cloudy_pixel_percentage": image.get("CLOUDY_PIXEL_PERCENTAGE"),
                                     "ndci_mean": stats.get("ndci_mean"), "water_pixels": stats.get("ndci_count")})

        compute = ee.Dictionary({"scenes_in_window": everything.size(), "scenes_under_cloud_limit": candidates.size(),
                                 "scenes": chosen.map(summarize).toList(max_scenes)})
    info, provenance = _fetch(compute)
    scenes = []
    for item in _features(info.get("scenes")):
        image_id, started = item.get("image_id"), item.get("time_start")
        if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id) or type(started) is not int:
            raise EarthEngineError("Earth Engine returned an invalid scene identifier or time")
        pixels = _number(item.get("water_pixels"), 0, 1e8)
        scenes.append({"image_id": image_id,
                       "date": datetime.fromtimestamp(started / 1000, timezone.utc).date().isoformat(),
                       "cloudy_pixel_percentage": _number(item.get("cloudy_pixel_percentage"), 0, 100),
                       "water_pixels": int(pixels) if pixels is not None else 0,
                       "ndci_mean": None if item.get("ndci_mean") is None else round(_number(item["ndci_mean"], -1, 1), 6)})
    totals = {key: _number(info.get(key), 0, 1e6) for key in ("scenes_in_window", "scenes_under_cloud_limit")}
    if any(type(value) is not int for value in totals.values()) or len(scenes) > totals["scenes_under_cloud_limit"]:
        raise EarthEngineError("Earth Engine returned inconsistent scene counts")
    valid = [scene["ndci_mean"] for scene in scenes if scene["ndci_mean"] is not None]
    return {"operation": "chlorophyll_timeseries", "dataset": S2_COLLECTION, "site": site, "site_label": spec["label"],
            "bbox": spec["bbox"], "start": first.isoformat(), "end": last.isoformat(),
            "index": "NDCI = (B5 - B4) / (B5 + B4)", "mask": "Scene classification (SCL) class 6, water",
            "scale_meters": S2_SCALE_METERS, "cloud_limit_percent": MAX_CLOUD_PERCENT, **totals,
            "scenes_returned": len(scenes), "scenes_with_water_pixels": len(valid),
            "ndci_range": [min(valid), max(valid)] if valid else None, "scenes": scenes,
            "attribution": S2_ATTRIBUTION, "requests": [provenance], "response_bytes": provenance["response_bytes"]}


def alphaearth_context(site: str, year: str, compute=None) -> dict:
    """Annual mean of the 64 AlphaEarth embedding bands over a named site."""
    spec = check_site(site)
    value = check_year(year)
    if compute is None:
        ee = _initialize()
        region = ee.Geometry.Rectangle(spec["bbox"])
        images = (ee.ImageCollection(ALPHAEARTH_COLLECTION).filterBounds(region)
                  .filterDate(f"{value}-01-01", f"{value + 1}-01-01"))
        first = ee.Image(images.first())
        stats = images.mosaic().select(list(BANDS)).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=region, scale=ALPHAEARTH_SCALE_METERS, maxPixels=10_000_000)
        pixels = images.mosaic().select("A00").reduceRegion(
            reducer=ee.Reducer.count(), geometry=region, scale=ALPHAEARTH_SCALE_METERS, maxPixels=10_000_000)
        compute = ee.Dictionary({"images": images.size(), "means": stats, "pixels": pixels.get("A00"),
                                 "dataset_version": first.get("DATASET_VERSION"),
                                 "model_version": first.get("MODEL_VERSION")})
    info, provenance = _fetch(compute)
    images = _number(info.get("images"), 0, 1000)
    if type(images) is not int:
        raise EarthEngineError("Earth Engine returned an invalid image count")
    means = info.get("means") or {}
    if not isinstance(means, dict) or set(means) - set(BANDS):
        raise EarthEngineError("Earth Engine returned unexpected embedding bands")
    vector = [None if means.get(band) is None else round(_number(means[band], -1, 1), 6) for band in BANDS]
    present = [item for item in vector if item is not None]
    if present and len(present) != len(BANDS):
        raise EarthEngineError("Earth Engine returned an incomplete embedding")
    pixels = _number(info.get("pixels"), 0, 1e9)
    versions = {key: info.get(key) if isinstance(info.get(key), (str, int, float)) else None
                for key in ("dataset_version", "model_version")}
    return {"operation": "alphaearth_context", "dataset": ALPHAEARTH_COLLECTION, "site": site,
            "site_label": spec["label"], "bbox": spec["bbox"], "year": value, "images": images,
            "scale_meters": ALPHAEARTH_SCALE_METERS, "pixels": int(pixels) if pixels is not None else 0,
            "mean_embedding": vector if present else None,
            "mean_vector_norm": round(math.sqrt(sum(item * item for item in present)), 6) if present else None,
            **{key: str(item)[:100] if item is not None else None for key, item in versions.items()},
            "attribution": ALPHAEARTH_ATTRIBUTION, "requests": [provenance], "response_bytes": provenance["response_bytes"]}


OPERATIONS = {"chlorophyll_timeseries": chlorophyll_timeseries, "alphaearth_context": alphaearth_context}


def run_operation(operation: str, arguments: dict) -> dict:
    """Default dispatcher used by the controller; tests substitute their own."""
    if operation not in OPERATIONS:
        raise ValueError("Unknown Earth Engine operation")
    return OPERATIONS[operation](**arguments)
