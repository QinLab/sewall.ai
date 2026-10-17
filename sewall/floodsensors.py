"""Bounded live readings from public water-level and flood sensors over a named Chesapeake area.

One operation, flood_sensors, takes a named area from AREAS and queries three
public services over its fixed bounding box:

- NOAA National Water Prediction Service (NWPS) gauges: the latest observed
  stage and the flood category NWS assigns from the gauge's own thresholds.
- NOAA CO-OPS tide stations from a fixed Chesapeake list: the latest
  preliminary water level and the station's NOS flood thresholds, both in feet
  above station datum (STND), so they compare directly.
- USGS latest continuous values for gage height (00065), water surface
  elevation NAVD88 (62620) and estuary elevation NAVD88 (72279). USGS returns
  no flood thresholds, so these readings carry no flood status.

The planner names an area; it never supplies a URL, a bounding box or a station.
Hosts are fixed, redirects and retries are disabled, and each response is
size-limited. One service failing is recorded and the others still report;
one malformed gauge or series entry is skipped and counted, and the rest of
that service still reports. Only readings that are present and no older than
STALE_AFTER count as current flooding. USGS returns one page of at most
USGS_PAGE_LIMIT series; a larger area is reported as truncated.
A call makes at most 2 + 2 * MAX_COOPS_STATIONS requests.

Service documentation (consulted 2026-10-14):
https://api.water.noaa.gov/nwps/v1/docs/
https://api.tidesandcurrents.noaa.gov/api/prod/
https://api.tidesandcurrents.noaa.gov/mdapi/prod/
https://api.waterdata.usgs.gov/ogcapi/v0/
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


NWPS = "https://api.water.noaa.gov/nwps/v1/gauges"
COOPS_DATA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
COOPS_META = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/"
USGS = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/latest-continuous/items"
ATTRIBUTION = ("Water levels and flood categories from NOAA NWS National Water Prediction Service and NOAA CO-OPS; "
               "continuous values from the USGS Water Data APIs. Most recent data are provisional.")
MAX_RESPONSE_BYTES = 1_048_576
TIMEOUT_SECONDS = 15
MAX_GAUGES = 30
MAX_USGS_SERIES = 30
USGS_PAGE_LIMIT = MAX_USGS_SERIES + 20
MAX_COOPS_STATIONS = 4
STALE_AFTER = timedelta(hours=2)
USGS_PARAMETERS = {"00065": "gage height", "62620": "water surface elevation, NAVD88",
                   "72279": "estuary or ocean water surface elevation, NAVD88"}
# A bounding box is a coarse search window, not a jurisdiction or watershed boundary.
AREAS = {
    "norfolk": {"label": "Norfolk, the Elizabeth River and the Chesapeake Bay Bridge-Tunnel, Virginia",
                "bbox": [-76.40, 36.75, -76.05, 37.05]},
    "lafayette-river": {"label": "Lafayette River, Norfolk, Virginia (same box as the Earth Engine site)",
                        "bbox": [-76.32, 36.88, -76.25, 36.915]},
    "hampton-newport-news": {"label": "Hampton and Newport News, Virginia", "bbox": [-76.60, 36.95, -76.25, 37.15]},
    "york-river-lower": {"label": "Lower York River, Virginia (same box as the Earth Engine site)",
                         "bbox": [-76.66, 37.22, -76.38, 37.42]},
    "annapolis": {"label": "Annapolis and the Severn River, Maryland", "bbox": [-76.60, 38.90, -76.40, 39.05]},
    "baltimore": {"label": "Baltimore Harbor and the Patapsco River, Maryland", "bbox": [-76.70, 39.15, -76.45, 39.35]},
}
# CO-OPS water-level stations in the Chesapeake Bay region, from the CO-OPS station list (2026-10-14).
COOPS_STATIONS = {
    "8571421": ("Bishops Head", 38.22, -76.0383), "8571892": ("Cambridge", 38.5725, -76.06167),
    "8573364": ("Tolchester Beach", 39.213444, -76.24455), "8574680": ("Baltimore", 39.266693, -76.57831),
    "8575512": ("Annapolis", 38.983883, -76.480034), "8577330": ("Solomons Island", 38.3167, -76.4517),
    "8594900": ("Washington", 38.873333, -77.02167), "8632200": ("Kiptopeke", 37.1652, -75.9884),
    "8635027": ("Dahlgren", 38.31975, -77.0366), "8635750": ("Lewisetta", 37.9961, -76.4644),
    "8636580": ("Windmill Point", 37.6155, -76.28978), "8637689": ("Yorktown USCG Training Center", 37.2265, -76.478806),
    "8638610": ("Sewells Point", 36.94278, -76.32861), "8638901": ("CBBT, Chesapeake Channel", 37.0329, -76.0833),
    "8639348": ("Money Point", 36.778194, -76.301865),
}
FLOOD_ORDER = ("action", "minor", "moderate", "major")
_LID = re.compile(r"[A-Z0-9]{3,8}\Z", re.ASCII)
_CATEGORY = re.compile(r"[a-z_]{2,30}\Z", re.ASCII)
_USGS_SITE = re.compile(r"USGS-[0-9]{8,15}\Z", re.ASCII)
_COOPS_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}\Z", re.ASCII)


class FloodSensorError(RuntimeError):
    """Retrieval failed or a service returned an unusable response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_area(area):
    if area not in AREAS:
        raise ValueError("area must be one of: " + ", ".join(sorted(AREAS)))
    return AREAS[area]


def _http_get(url):
    request = Request(url, headers={"User-Agent": "SewallFloodSensorPrototype/0.1 (public water-level readings)",
                                    "Accept": "application/json"}, method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.geturl() != url or response.getcode() != 200:
                raise FloodSensorError("Service redirected or returned a non-success status")
            return response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise FloodSensorError(f"Service returned HTTP {exc.code}; no retry attempted") from None
    except (URLError, OSError, TimeoutError) as exc:
        raise FloodSensorError(f"Request failed ({type(exc).__name__}); no retry attempted") from None


def _fetch(get, service, url, requests):
    raw = get(url)
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise FloodSensorError(service + " response exceeded the byte limit")
    requests.append({"service": service, "url": url, "raw_sha256": hashlib.sha256(raw).hexdigest(),
                     "response_bytes": len(raw), "attempts": 1})
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise FloodSensorError(service + " response was not JSON") from None
    if not isinstance(document, dict):
        raise FloodSensorError(service + " response was not a JSON object")
    return document


def _number(value, low=-1000.0, high=1000.0):
    """Return a finite float within bounds; NWPS and CO-OPS use -999 or blanks for missing values."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FloodSensorError("Reading was not numeric") from None
    if not math.isfinite(number):
        raise FloodSensorError("Reading was not finite")
    if number == -999 or not low <= number <= high:
        return None
    return round(number, 3)


def _utc(text, form):
    try:
        moment = datetime.strptime(text, form)
    except (TypeError, ValueError):
        raise FloodSensorError("Reading time was not a valid timestamp") from None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _inside(bbox, lat, lon):
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def _nwps(get, bbox, requests, now):
    url = NWPS + "?" + urlencode({"bbox.xmin": bbox[0], "bbox.ymin": bbox[1], "bbox.xmax": bbox[2],
                                  "bbox.ymax": bbox[3], "srid": "EPSG_4326"})
    document = _fetch(get, "nwps", url, requests)
    gauges = document.get("gauges")
    if not isinstance(gauges, list):
        raise FloodSensorError("NWPS response had no gauge list")
    readings, malformed = [], 0
    for gauge in gauges:
        try:
            lid, name, observed = gauge["lid"], gauge["name"], gauge["status"]["observed"]
            lat, lon = float(gauge["latitude"]), float(gauge["longitude"])
            category = observed["floodCategory"]
            if (not isinstance(lid, str) or not _LID.fullmatch(lid) or not isinstance(name, str)
                    or not isinstance(category, str) or not _CATEGORY.fullmatch(category)
                    or not math.isfinite(lat) or not math.isfinite(lon)):
                raise ValueError
            if not _inside(bbox, lat, lon):
                continue
            value = _number(observed.get("primary"))
            unit = observed.get("primaryUnit") if value is not None else None
            valid = None
            if value is not None:
                moment = _utc(observed.get("validTime"), "%Y-%m-%dT%H:%M:%SZ")
                valid = _iso(moment)
        except (KeyError, TypeError, ValueError, FloodSensorError):
            malformed += 1
            continue
        readings.append({"lid": lid, "name": name[:120], "latitude": round(lat, 5), "longitude": round(lon, 5),
                         "flood_category": category, "observed": value,
                         "unit": unit[:10] if isinstance(unit, str) else None, "valid_time": valid,
                         "stale": valid is not None and now - moment > STALE_AFTER})
    if gauges and malformed == len(gauges):
        raise FloodSensorError("Every NWPS gauge entry was malformed")
    readings.sort(key=lambda item: item["lid"])
    return readings[:MAX_GAUGES], len(readings), malformed


def _flood_level(value, thresholds):
    level = "below_action" if thresholds.get("action") is not None else "below_minor"
    for name in FLOOD_ORDER:
        if thresholds.get(name) is not None and value >= thresholds[name]:
            level = name
    return level if any(thresholds.get(name) is not None for name in FLOOD_ORDER) else "no_thresholds"


def _coops(get, station, name, requests, now):
    common = {"station": station, "application": "sewall_prototype", "format": "json"}
    data = _fetch(get, "coops_water_level", COOPS_DATA + "?" + urlencode(
        {"product": "water_level", "date": "latest", "datum": "STND", "units": "english", "time_zone": "gmt", **common}),
        requests)
    levels = _fetch(get, "coops_flood_levels", COOPS_META + station + "/floodlevels.json", requests)
    reading = {"station": station, "name": name, "datum": "STND", "unit": "ft"}
    if "error" in data:
        reading.update(value=None, time=None, flood_level="no_recent_reading", stale=None)
    else:
        try:
            latest = data["data"][-1]
            if data["metadata"]["id"] != station or not _COOPS_TIME.fullmatch(latest["t"]):
                raise ValueError
        except (KeyError, TypeError, ValueError, IndexError):
            raise FloodSensorError("CO-OPS water level response was malformed") from None
        moment = _utc(latest["t"], "%Y-%m-%d %H:%M")
        reading.update(value=_number(latest.get("v")), time=_iso(moment), quality=str(latest.get("q", ""))[:2],
                       stale=now - moment > STALE_AFTER)
    thresholds = {"action": _number(levels.get("action"))}
    for name in FLOOD_ORDER[1:]:
        thresholds[name] = _number(levels.get("nos_" + name))
        if thresholds[name] is None:
            thresholds[name] = _number(levels.get("nws_" + name))
    reading["thresholds"] = thresholds
    if reading.get("value") is not None:
        reading["flood_level"] = _flood_level(reading["value"], thresholds)
        minor = thresholds.get("minor")
        reading["feet_below_minor"] = None if minor is None else round(minor - reading["value"], 3)
    else:
        reading.setdefault("flood_level", "no_recent_reading")
    return reading


def _usgs(get, bbox, requests, now):
    url = USGS + "?" + urlencode({"f": "json", "bbox": ",".join(str(value) for value in bbox),
                                  "parameter_code": ",".join(USGS_PARAMETERS), "limit": str(USGS_PAGE_LIMIT)})
    document = _fetch(get, "usgs", url, requests)
    features = document.get("features")
    if not isinstance(features, list):
        raise FloodSensorError("USGS response had no feature list")
    if len(features) > USGS_PAGE_LIMIT:
        raise FloodSensorError("USGS returned more features than requested")
    series, malformed = [], 0
    for feature in features:
        try:
            properties, coordinates = feature["properties"], feature["geometry"]["coordinates"]
            site, code = properties["monitoring_location_id"], properties["parameter_code"]
            lon, lat = float(coordinates[0]), float(coordinates[1])
            if not isinstance(site, str) or not _USGS_SITE.fullmatch(site) or code not in USGS_PARAMETERS:
                raise ValueError
            moment = _utc(properties.get("time"), "%Y-%m-%dT%H:%M:%S%z")
            value = _number(properties.get("value"), -10_000, 100_000)
        except (KeyError, TypeError, ValueError, IndexError, FloodSensorError):
            malformed += 1
            continue
        unit = properties.get("unit_of_measure")
        status = properties.get("approval_status")
        series.append({"site": site, "parameter_code": code, "parameter": USGS_PARAMETERS[code],
                       "latitude": round(lat, 5), "longitude": round(lon, 5),
                       "value": value,
                       "unit": unit[:10] if isinstance(unit, str) else None, "time": _iso(moment),
                       "approval_status": status[:20] if isinstance(status, str) else None,
                       "stale": now - moment > STALE_AFTER})
    if features and malformed == len(features):
        raise FloodSensorError("Every USGS feature was malformed")
    # The OGC API reports numberMatched when it knows the total; otherwise a next link or a full page means more.
    matched = document.get("numberMatched")
    links = document.get("links") if isinstance(document.get("links"), list) else []
    has_next = any(isinstance(link, dict) and link.get("rel") == "next" for link in links)
    if type(matched) is int and matched >= len(features):
        truncated = matched > len(features)
    else:
        matched, truncated = None, has_next or len(features) == USGS_PAGE_LIMIT
    series.sort(key=lambda item: (item["site"], item["parameter_code"]))
    in_area = len(series) + (matched - len(features) if matched is not None else 0)
    return series[:MAX_USGS_SERIES], in_area, truncated or len(series) > MAX_USGS_SERIES, malformed


def flood_sensors(area, *, get=None, now=None):
    """Return the latest public flood-sensor readings over a named area, with request provenance."""
    spec = check_area(area)
    get = _http_get if get is None else get
    now = datetime.now(timezone.utc) if now is None else now
    bbox, requests, unavailable = spec["bbox"], [], []
    result = {"operation": "flood_sensors", "area": area, "label": spec["label"], "bbox": list(bbox),
              "retrieved_at": _iso(now), "nwps_gauges": [], "coops_stations": [], "usgs_series": []}
    try:
        result["nwps_gauges"], result["nwps_gauges_in_area"], result["nwps_malformed"] = _nwps(get, bbox, requests, now)
    except FloodSensorError as exc:
        unavailable.append({"service": "nwps", "error": str(exc)[:200]})
    stations = sorted(station for station, (_, lat, lon) in COOPS_STATIONS.items() if _inside(bbox, lat, lon))
    for station in stations[:MAX_COOPS_STATIONS]:
        try:
            result["coops_stations"].append(_coops(get, station, COOPS_STATIONS[station][0], requests, now))
        except FloodSensorError as exc:
            unavailable.append({"service": "coops", "station": station, "error": str(exc)[:200]})
    try:
        (result["usgs_series"], result["usgs_series_in_area"], result["usgs_truncated"],
         result["usgs_malformed"]) = _usgs(get, bbox, requests, now)
    except FloodSensorError as exc:
        unavailable.append({"service": "usgs", "error": str(exc)[:200]})
    if not requests or len(unavailable) == 2 + len(stations[:MAX_COOPS_STATIONS]):
        raise FloodSensorError("Every flood sensor service failed; " + "; ".join(item["error"] for item in unavailable)[:400])
    # A flood level counts as current only with a reading younger than STALE_AFTER; older ones are listed apart.
    flagged = [({"source": "nwps", "id": item["lid"], "name": item["name"], "level": item["flood_category"]},
                item["observed"] is not None and not item["stale"])
               for item in result["nwps_gauges"] if item["flood_category"] in FLOOD_ORDER]
    flagged += [({"source": "coops", "id": item["station"], "name": item["name"], "level": item["flood_level"]},
                 item.get("value") is not None and not item.get("stale"))
                for item in result["coops_stations"] if item["flood_level"] in FLOOD_ORDER]
    flooding = [entry for entry, current in flagged if current]
    not_current = [entry for entry, current in flagged if not current]
    categories = {}
    for item in result["nwps_gauges"]:
        categories[item["flood_category"]] = categories.get(item["flood_category"], 0) + 1
    result.update(nwps_categories=dict(sorted(categories.items())), at_or_above_action=flooding,
                  at_or_above_action_not_current=not_current, unavailable=unavailable, attribution=ATTRIBUTION, requests=requests,
                  response_bytes=sum(item["response_bytes"] for item in requests))
    return result


def run_operation(operation, arguments):
    """Dispatch a validated planner action; the controller passes only declared arguments."""
    if operation != "flood_sensors":
        raise FloodSensorError("Unknown flood sensor operation")
    return flood_sensors(**arguments)
