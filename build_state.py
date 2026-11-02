#!/usr/bin/env python3
"""Turn an OpenStreetMap roads extract into the compact SQLite file the app reads.

Usage: python3 build_state.py <roads.osm.pbf> <out.sqlite> <STATE>

Keeps drivable roads only. Stores the posted limit when OSM has one (mph),
plus truck/trailer limits. Roads with no posted limit are stored with
speed_mph NULL and their road class; the app fills in the state's statutory
default from the state's law file, so defaults can be corrected without
rebuilding road files.

Geometry is simplified to ~3 m, packed as zigzag-varint deltas (1e-5 degrees), and
indexed with an SQLite R-tree for fast nearest-road lookup on the phone.
"""
import re
import sqlite3
import struct
import sys
import os
import datetime

import osmium

ROAD_CLASSES = {
    "motorway": 1, "motorway_link": 1,
    "trunk": 2, "trunk_link": 2,
    "primary": 3, "primary_link": 3,
    "secondary": 4, "secondary_link": 4,
    "tertiary": 5, "tertiary_link": 5,
    "unclassified": 6,
    "residential": 7,
    "living_street": 8,
    "service": 9,
}

KMH_TO_MPH = 0.621371
_num = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*$", re.I)


def parse_speed(raw):
    """Return mph as int, or None for unknown/non-numeric values."""
    if not raw:
        return None
    raw = raw.split(";")[0]          # "35 mph;45 mph" -> first value
    m = _num.match(raw)
    if not m:
        return None                  # "US:urban", "none", "signals", ...
    val = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in ("km/h", "kmh", "kph"):
        val = val * KMH_TO_MPH
    # A bare number is km/h by OSM convention, but US mappers almost always
    # mean mph. Treat bare values as mph when they look like a US posted limit.
    elif unit == "" and not (5 <= val <= 85 and val % 5 == 0):
        val = val * KMH_TO_MPH
    mph = int(round(val / 5.0) * 5) if unit != "mph" else int(round(val))
    return mph if 5 <= mph <= 90 else None


SCALE = 1e5          # 1e-5 degrees ~ 1.1 m, finer than phone GPS
TOL_M = 3.0          # simplification tolerance in meters


def simplify(pts, tol_m=TOL_M):
    """Douglas-Peucker in a local flat projection (meters)."""
    if len(pts) < 3:
        return pts
    import math
    k = math.cos(math.radians(pts[0][0]))
    xy = [(lon * 111320.0 * k, lat * 110540.0) for lat, lon in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if L2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > best:
                best, idx = d2, i
        if idx >= 0 and best > tol_m * tol_m:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k2 in zip(pts, keep) if k2]


def _varint(n, out):
    n = (n << 1) ^ (n >> 63)          # zigzag: small +/- deltas -> small ints
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)


def pack(coords):
    """Zigzag-varint delta encoding at 1e-5 degrees. First point absolute."""
    out = bytearray()
    plat = plon = 0
    for lat, lon in coords:
        ilat, ilon = int(round(lat * SCALE)), int(round(lon * SCALE))
        _varint(ilat - plat, out)
        _varint(ilon - plon, out)
        plat, plon = ilat, ilon
    return bytes(out)


def unpack(blob):
    """Reference decoder (the app does the same thing)."""
    vals, n, shift = [], 0, 0
    for byte in blob:
        n |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        vals.append((n >> 1) ^ -(n & 1))
        n, shift = 0, 0
    pts, lat, lon = [], 0, 0
    for i in range(0, len(vals), 2):
        lat += vals[i]
        lon += vals[i + 1]
        pts.append((lat / SCALE, lon / SCALE))
    return pts


class Roads(osmium.SimpleHandler):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.n = 0
        self.posted = 0

    def way(self, w):
        tags = w.tags
        cls = ROAD_CLASSES.get(tags.get("highway"))
        if cls is None:
            return
        try:
            coords = [(n.lat, n.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        coords = simplify(coords)
        fwd = parse_speed(tags.get("maxspeed:forward"))
        bwd = parse_speed(tags.get("maxspeed:backward"))
        speed = parse_speed(tags.get("maxspeed"))
        if speed is None and (fwd or bwd):
            speed = min(s for s in (fwd, bwd) if s)
        hgv = parse_speed(tags.get("maxspeed:hgv"))
        trailer = parse_speed(tags.get("maxspeed:trailer"))
        name = tags.get("name") or tags.get("ref") or None
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        self.db.execute(
            "INSERT INTO roads(id,class,speed_mph,hgv_mph,trailer_mph,oneway,name,geom)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (w.id, cls, speed, hgv, trailer,
             1 if tags.get("oneway") in ("yes", "1") else 0, name, pack(coords)))
        self.db.execute("INSERT INTO roads_idx VALUES(?,?,?,?,?)",
                        (w.id, min(lats), max(lats), min(lons), max(lons)))
        self.n += 1
        if speed:
            self.posted += 1


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    src, out, state = sys.argv[1], sys.argv[2], sys.argv[3].upper()
    if os.path.exists(out):
        os.remove(out)
    db = sqlite3.connect(out)
    db.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE roads(id INTEGER PRIMARY KEY, class INTEGER NOT NULL,
            speed_mph INTEGER, hgv_mph INTEGER, trailer_mph INTEGER,
            oneway INTEGER NOT NULL, name TEXT, geom BLOB NOT NULL);
        CREATE VIRTUAL TABLE roads_idx USING rtree(id, min_lat, max_lat, min_lon, max_lon);
    """)
    h = Roads(db)
    h.apply_file(src, locations=True)
    built = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.executemany("INSERT INTO meta VALUES(?,?)", [
        ("state", state), ("built_utc", built), ("schema", "2"),
        ("geom", "zigzag-varint delta, 1e-5 deg, first point absolute"),
        ("roads", str(h.n)), ("posted", str(h.posted)),
        ("attribution", "© OpenStreetMap contributors, ODbL"),
    ])
    db.commit()
    db.execute("VACUUM")
    db.close()
    pct = 100 * h.posted // max(h.n, 1)
    print(f"{state}: {h.n} roads, {h.posted} with posted limit ({pct}%)")
    print(f"{out}: {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
