#!/usr/bin/env python3
"""Download official state DOT posted speed limits as simple line segments.

Usage: python3 dot_sources.py <STATE> <out.json>
Writes {"state", "credit", "fetched_utc", "segments": [{"s": car_mph, "t": truck_mph|null,
"p": [[lat, lon], ...]}, ...]}. Exits 0 with no file if the state has no source.

Downloads politely: public endpoints only, paged, with a pause between requests.
"""
import datetime
import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import zipfile

UA = "AmISpeeding-data-builder/1.0 (+https://github.com/valusoft2026/am-i-speeding-data)"
PAUSE = 0.4  # seconds between requests

SOURCES = {
    "VA": {
        "kind": "arcgis",
        "url": "https://services.arcgis.com/p5v98VHDX9Atv3l7/ArcGIS/rest/services/VDOT_Posted_Speed_Limits/FeatureServer/0",
        "speed": "CAR_SPEED_LIMIT",
        "truck": "TRUCK_SPEED_LIMIT",
        "credit": "Virginia Department of Transportation (VDOT), Posted Speed Limits",
    },
    "OH": {
        "kind": "arcgis",
        "url": "https://tims.dot.state.oh.us/ags/rest/services/Roadway_Information/Road_Inventory/MapServer/0",
        "speed": "SPEED_LIMIT_NBR",
        "truck": None,
        "credit": "Ohio Department of Transportation (ODOT), TIMS Road Inventory",
    },
    "FL": {
        "kind": "shapefile_zip",
        "url": "https://ftp.fdot.gov/public/file/k6lvcs-0pegq78ghurk2aa/maxspeed.zip",
        "speed": "SPEED",
        "truck": None,
        "credit": "Florida Department of Transportation (FDOT), Roadway Characteristics Inventory",
    },
}


def _get(url, data=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:  # network hiccup: back off and retry
            if i == tries - 1:
                raise
            print(f"  retry {i + 1} after error: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))


def _json(url, params=None, post=False):
    q = urllib.parse.urlencode(params or {})
    if post:
        return json.loads(_get(url, data=q.encode()))
    return json.loads(_get(url + ("?" + q if q else "")))


def _speed(v):
    try:
        v = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None
    return v if 5 <= v <= 90 else None


# ---------------- ArcGIS FeatureServer / MapServer ----------------

def fetch_arcgis(src):
    url = src["url"]
    info = _json(url, {"f": "json"})
    if "error" in info:
        raise RuntimeError(f"layer info error: {info['error']}")
    oid = info.get("objectIdField") or next(
        (f["name"] for f in info.get("fields", []) if f.get("type") == "esriFieldTypeOID"), "OBJECTID")
    chunk = max(100, min(int(info.get("maxRecordCount") or 1000), 1000))
    where = f"{src['speed']} IS NOT NULL"
    ids = _json(url + "/query", {"where": where, "returnIdsOnly": "true", "f": "json"}, post=True)
    if "error" in ids:
        raise RuntimeError(f"id query error: {ids['error']}")
    ids = sorted(ids.get("objectIds") or [])
    print(f"  {len(ids)} features, {chunk} per request")
    fields = ",".join(f for f in (oid, src["speed"], src.get("truck")) if f)
    segs = []
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        res = _json(url + "/query", {
            "objectIds": ",".join(map(str, part)), "outFields": fields,
            "returnGeometry": "true", "outSR": "4326", "f": "json",
        }, post=True)
        if "error" in res:
            raise RuntimeError(f"query error at {i}: {res['error']}")
        for f in res.get("features", []):
            a = f.get("attributes", {})
            s = _speed(a.get(src["speed"]))
            if s is None:
                continue
            t = _speed(a.get(src["truck"])) if src.get("truck") else None
            for path in (f.get("geometry") or {}).get("paths", []):
                pts = [[round(p[1], 6), round(p[0], 6)] for p in path if len(p) >= 2]
                if len(pts) >= 2:
                    segs.append({"s": s, "t": t, "p": pts})
        if (i // chunk) % 25 == 0:
            print(f"  {min(i + chunk, len(ids))}/{len(ids)}")
        time.sleep(PAUSE)
    return segs


# ---------------- Shapefile in a zip (no extra libraries needed except pyshp) ----------------

def _utm_to_latlon(e, n, zone, north=True):
    """Inverse UTM (WGS84/NAD83 ellipsoid; difference is far below GPS accuracy)."""
    a, f = 6378137.0, 1 / 298.257222101
    k0, e2 = 0.9996, f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = e - 500000.0
    y = n if north else n - 10000000.0
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
          + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s1, c1, t1 = math.sin(p1), math.cos(p1), math.tan(p1)
    n1 = a / math.sqrt(1 - e2 * s1 ** 2)
    r1 = a * (1 - e2) / (1 - e2 * s1 ** 2) ** 1.5
    cc = ep2 * c1 ** 2
    d = x / (n1 * k0)
    lat = p1 - (n1 * t1 / r1) * (d ** 2 / 2 - (5 + 3 * t1 ** 2 + 10 * cc - 4 * cc ** 2 - 9 * ep2) * d ** 4 / 24
                                 + (61 + 90 * t1 ** 2 + 298 * cc + 45 * t1 ** 4 - 252 * ep2 - 3 * cc ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 ** 2 + cc) * d ** 3 / 6
           + (5 - 2 * cc + 28 * t1 ** 2 - 3 * cc ** 2 + 8 * ep2 + 24 * t1 ** 4) * d ** 5 / 120) / c1
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    return math.degrees(lat), math.degrees(lon0 + lon)


def fetch_shapefile_zip(src):
    import re
    import shapefile  # pip install pyshp
    raw = _get(src["url"])
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    base = next(n[:-4] for n in names if n.lower().endswith(".shp"))
    prj = z.read(next(n for n in names if n.lower() == base.lower() + ".prj")).decode("latin-1")
    m = re.search(r"UTM[_ ]Zone[_ ](\d+)([NS])?", prj, re.I)
    if m:
        zone, north = int(m.group(1)), (m.group(2) or "N").upper() == "N"
        conv = lambda x, y: _utm_to_latlon(x, y, zone, north)
    elif prj.strip().upper().startswith("GEOGCS"):
        conv = lambda x, y: (y, x)
    else:
        raise RuntimeError(f"unsupported projection: {prj[:120]}")
    r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                         shx=io.BytesIO(z.read(base + ".shx")))
    fields = [f[0] for f in r.fields[1:]]
    si = fields.index(src["speed"])
    ti = fields.index(src["truck"]) if src.get("truck") else None
    segs = []
    for sr in r.iterShapeRecords():
        s = _speed(sr.record[si])
        if s is None:
            continue
        t = _speed(sr.record[ti]) if ti is not None else None
        pts, parts = sr.shape.points, list(sr.shape.parts) + [len(sr.shape.points)]
        for a, b in zip(parts[:-1], parts[1:]):
            line = [[round(v, 6) for v in conv(x, y)] for x, y in pts[a:b]]
            if len(line) >= 2:
                segs.append({"s": s, "t": t, "p": line})
    return segs


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    state, out = sys.argv[1].upper(), sys.argv[2]
    src = SOURCES.get(state)
    if not src:
        print(f"{state}: no official speed-limit source configured")
        return
    print(f"{state}: downloading {src['credit']}")
    segs = fetch_arcgis(src) if src["kind"] == "arcgis" else fetch_shapefile_zip(src)
    if not segs:
        raise RuntimeError("no segments downloaded")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        json.dump({"state": state, "credit": src["credit"],
                   "fetched_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "segments": segs}, f)
    print(f"{state}: {len(segs)} speed-limit segments -> {out}")


if __name__ == "__main__":
    main()
