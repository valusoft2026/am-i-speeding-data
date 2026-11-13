#!/usr/bin/env python3
"""Add known fixed speed cameras to a state's road file.

Usage: python3 cameras.py <STATE> <cameras.osm.pbf> <out.sqlite>

Sources:
  * OpenStreetMap nodes tagged highway=speed_camera (all states; © OpenStreetMap contributors, ODbL)
  * City open data where it's published as a dataset (public records):
      IL: City of Chicago, Speed Camera Locations
      DC: DC Government, Automated Safety Cameras (speed only, fixed and active)
A city camera replaces any OpenStreetMap camera within 40 m of it. A failed city download just
leaves the OpenStreetMap cameras.

Table cameras: lat, lon, dir (degrees the enforced traffic travels, -1 = unknown or both ways),
mph (limit enforced, if known), src (1 = OpenStreetMap, 4 = city data), note.
"""
import json
import math
import re
import sqlite3
import sys
import urllib.request

import osmium

CARDINAL = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315,
            "NB": 0, "EB": 90, "SB": 180, "WB": 270,
            "NORTH": 0, "EAST": 90, "SOUTH": 180, "WEST": 270,
            "NORTHBOUND": 0, "EASTBOUND": 90, "SOUTHBOUND": 180, "WESTBOUND": 270}


def parse_dir(raw):
    """Degrees, or -1 when unknown / both ways."""
    if raw is None:
        return -1
    s = str(raw).strip().upper()
    if not s or ";" in s or s in ("BOTH", "ALL", "BOTH DIRECTIONS"):
        return -1
    try:
        return int(float(s)) % 360
    except ValueError:
        pass
    return CARDINAL.get(s.replace(" ", ""), -1)


def parse_mph(raw):
    m = re.match(r"\s*(\d+)", str(raw or ""))
    if not m:
        return None
    v = int(m.group(1))
    if "km" in str(raw).lower():
        v = round(v * 0.621371 / 5) * 5
    return v if 5 <= v <= 85 else None


class Cams(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.out = []

    def node(self, n):
        if n.tags.get("highway") != "speed_camera":
            return
        self.out.append((n.location.lat, n.location.lon,
                         parse_dir(n.tags.get("direction") or n.tags.get("camera:direction")),
                         parse_mph(n.tags.get("maxspeed")), 1, None))


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "am-i-speeding-data (github.com/valusoft2026)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def chicago():
    rows = get_json("https://data.cityofchicago.org/resource/4i42-qv3h.json?$limit=5000")
    out = []
    for r in rows:
        lat, lon = r.get("latitude"), r.get("longitude")
        if (lat is None or lon is None) and isinstance(r.get("location"), dict):
            c = r["location"].get("coordinates") or [None, None]
            lon, lat = c[0], c[1]
        if lat is None or lon is None:
            continue
        # Chicago cameras usually enforce two approaches (e.g. "NB" and "SB"): store both-ways
        # unless only one approach is listed.
        a1, a2 = r.get("first_approach"), r.get("second_approach")
        d = parse_dir(a1) if a1 and not a2 else -1
        out.append((float(lat), float(lon), d, None, 4, "Chicago Children's Safety Zone camera"))
    return out


def dc():
    base = ("https://maps2.dcgis.dc.gov/DCGIS/rest/services/DCGIS_DATA/Public_Safety_WebMercator/"
            "FeatureServer/47/query?where=1%3D1&outFields=*&returnGeometry=false&f=json"
            "&resultRecordCount=1000&resultOffset=")
    out, offset = [], 0
    while True:
        feats = get_json(base + str(offset)).get("features", [])
        for f in feats:
            a = f.get("attributes", {})
            kind = str(a.get("ENFORCEMENT_TYPE") or "").lower()
            status = f"{a.get('ACTIVE_STATUS') or ''} {a.get('CAMERA_STATUS') or ''}".lower()
            mobile = "mobile" in str(a.get("DEVICE_MOBILITY") or "").lower()
            if "speed" not in kind or "inactive" in status or "decommission" in status or mobile:
                continue
            lat, lon = a.get("CAMERA_LATITUDE"), a.get("CAMERA_LONGITUDE")
            if not lat or not lon:
                continue
            desc = str(a.get("LOCATION_DESCRIPTION") or "")
            m = re.search(r"\b(NB|SB|EB|WB|NORTHBOUND|SOUTHBOUND|EASTBOUND|WESTBOUND)\b", desc.upper())
            out.append((float(lat), float(lon), parse_dir(m.group(1)) if m else -1,
                        parse_mph(a.get("SPEED_LIMIT")), 4, "DC automated safety camera"))
        if len(feats) < 1000:
            break
        offset += 1000
    return out


CITY = {"IL": [("City of Chicago", chicago)], "DC": [("DC Government (DDOT)", dc)]}


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 110540, (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])))


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    state, pbf, out = sys.argv[1].upper(), sys.argv[2], sys.argv[3]
    h = Cams()
    h.apply_file(pbf)
    osm = h.out
    city, credits = [], []
    for name, fn in CITY.get(state, []):
        try:
            got = fn()
            print(f"{state}: {len(got)} speed cameras from {name}")
            if got:
                city += got
                credits.append(name)
        except Exception as e:  # city server down or changed: keep going with OpenStreetMap
            print(f"WARNING: {state} {name} camera download failed: {e}")
    # official city points win over nearby OpenStreetMap duplicates
    osm = [c for c in osm if not any(dist_m(c, k) < 40 for k in city)]
    rows = city + osm
    db = sqlite3.connect(out)
    db.executescript("""
        DROP TABLE IF EXISTS cameras;
        CREATE TABLE cameras(id INTEGER PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL,
            dir INTEGER NOT NULL DEFAULT -1, mph INTEGER, src INTEGER NOT NULL, note TEXT);
        CREATE INDEX cameras_lat ON cameras(lat);
    """)
    db.executemany("INSERT INTO cameras(lat,lon,dir,mph,src,note) VALUES(?,?,?,?,?,?)", rows)
    db.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", [
        ("cameras", str(len(rows))), ("camera_credit", ", ".join(credits)), ("schema", "5")])
    db.commit()
    db.close()
    print(f"{state}: {len(rows)} speed cameras ({len(osm)} OpenStreetMap, {len(city)} city data)")


if __name__ == "__main__":
    main()
