#!/usr/bin/env python3
"""Turn an OpenStreetMap roads extract into the compact SQLite file the app reads.

Usage: python3 build_state.py <roads.osm.pbf> <out.sqlite> <STATE> [dot_speeds.json]

With a DOT file (from dot_sources.py), official posted limits from the state DOT replace or fill
in OpenStreetMap's, and roads are split where the official limit changes. Column src says where a
road's limit came from: 0 = none (app uses state default), 1 = OpenStreetMap, 2 = state DOT.

Keeps drivable roads only. Stores the posted limit when OSM has one (mph),
plus truck/trailer limits. Roads with no posted limit are stored with
speed_mph NULL and their road class; the app fills in the state's statutory
default from the state's law file, so defaults can be corrected without
rebuilding road files.

Geometry is simplified to ~3 m, packed as zigzag-varint deltas (1e-5 degrees), and
indexed with an SQLite R-tree for fast nearest-road lookup on the phone.
"""
import math
import re
import sqlite3
import struct
import sys
import os
import datetime

import osmium

from dot_match import DotIndex, split_by_dot

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


CELL = 0.005          # ~500 m grid for street-density ("urban") detection
URBAN_MIN = 12        # minor streets within the 3x3 cells around a road -> urban


class Roads(osmium.SimpleHandler):
    def __init__(self, db, dot=None, dot_fill_only=False):
        super().__init__()
        self.db = db
        self.dot = dot          # dot_match.DotIndex or None
        self.fill_only = dot_fill_only  # older DOT data: only fill roads with no mapped limit
        self.n = 0
        self.posted = 0
        self.from_dot = 0
        self.cells = {}       # (i, j) -> count of minor streets (classes 6-8)
        self.minor = []       # (way id, cell) for classes 5-8, to flag urban later
        self.pieces = {}      # road name -> [[id, start, end, official speed, src, length m], ...]

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
        fwd = parse_speed(tags.get("maxspeed:forward"))
        bwd = parse_speed(tags.get("maxspeed:backward"))
        speed = parse_speed(tags.get("maxspeed"))
        if speed is None and (fwd or bwd):
            speed = min(s for s in (fwd, bwd) if s)
        hgv = parse_speed(tags.get("maxspeed:hgv"))
        trailer = parse_speed(tags.get("maxspeed:trailer"))
        name = tags.get("name") or tags.get("ref") or None
        oneway = 1 if tags.get("oneway") in ("yes", "1") else 0

        use_dot = self.dot and not (self.fill_only and speed)
        parts = split_by_dot(coords, self.dot) if use_dot else [(coords, None, None)]
        for k, (line, dot_speed, dot_truck) in enumerate(parts):
            row_id = w.id if k == 0 else w.id + (k << 40)  # extra pieces get unique ids
            if dot_speed:
                self._insert(row_id, cls, line, dot_speed, dot_truck or hgv, trailer, oneway, name, 2)
                self.from_dot += 1
            else:
                self._insert(row_id, cls, line, speed, hgv, trailer, oneway, name, 1 if speed else 0)
        if cls >= 6:
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            cell = (int(sum(lats) / len(lats) // CELL), int(sum(lons) / len(lons) // CELL))
            self.cells[cell] = self.cells.get(cell, 0) + 1

    def _insert(self, row_id, cls, coords, speed, hgv, trailer, oneway, name, src):
        coords = simplify(coords)
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        self.db.execute(
            "INSERT INTO roads(id,class,speed_mph,hgv_mph,trailer_mph,oneway,name,geom,src)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (row_id, cls, speed, hgv, trailer, oneway, name, pack(coords), src))
        self.db.execute("INSERT INTO roads_idx VALUES(?,?,?,?,?)",
                        (row_id, min(lats), max(lats), min(lons), max(lons)))
        if cls >= 5:
            cell = (int(sum(lats) / len(lats) // CELL), int(sum(lons) / len(lons) // CELL))
            self.minor.append((row_id, cell))
        if name and cls <= 6:
            # remember ends of named roads, to fill short gaps between official segments later
            length = sum(math.hypot((b[0] - a[0]) * 110540, (b[1] - a[1]) * 87000)
                         for a, b in zip(coords, coords[1:]))
            self.pieces.setdefault(name, []).append(
                [row_id, coords[0], coords[-1], speed if src == 2 else None, src, length])
        self.n += 1
        if speed:
            self.posted += 1


GAP_MAX_M = 1500   # longest unmatched piece we'll fill from its neighbors
TOUCH_M = 20       # ends closer than this count as connected


def fill_gaps(db, pieces):
    """A named road with official limits on its neighbors but none on this piece (because the DOT
    line was offset from the map line, or a short stretch didn't match) takes the neighbors' official
    limit when they agree. Marked src = 3 ("from official data on the rest of this road")."""
    def near(p, q):
        return math.hypot((p[0] - q[0]) * 110540, (p[1] - q[1]) * 87000) <= TOUCH_M
    filled = 0
    for name, plist in pieces.items():
        if not any(p[3] for p in plist):
            continue
        for _ in range(4):  # a few passes so chains of short gaps fill in
            changed = False
            for p in plist:
                if p[3] is not None or p[4] == 1 or p[5] > GAP_MAX_M:
                    continue  # already official, or has its own OpenStreetMap posted limit, or too long
                nbr = {q[3] for q in plist if q is not p and q[3] is not None and
                       (near(p[1], q[1]) or near(p[1], q[2]) or near(p[2], q[1]) or near(p[2], q[2]))}
                if len(nbr) == 1:
                    p[3] = nbr.pop()
                    p[4] = 3
                    db.execute("UPDATE roads SET speed_mph = ?, src = 3 WHERE id = ?", (p[3], p[0]))
                    filled += 1
                    changed = True
            if not changed:
                break
    return filled


def main():
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    src, out, state = sys.argv[1], sys.argv[2], sys.argv[3].upper()
    dot, credit, fill_only = None, None, False
    if len(sys.argv) == 5 and os.path.exists(sys.argv[4]):
        import json
        d = json.load(open(sys.argv[4]))
        credit = d.get("credit")
        fill_only = d.get("mode") == "fill"
        dot = DotIndex(d["segments"])
        print(f"{state}: using {len(d['segments'])} official segments from {credit}")
    if os.path.exists(out):
        os.remove(out)
    db = sqlite3.connect(out)
    db.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE roads(id INTEGER PRIMARY KEY, class INTEGER NOT NULL,
            speed_mph INTEGER, hgv_mph INTEGER, trailer_mph INTEGER,
            oneway INTEGER NOT NULL, name TEXT, geom BLOB NOT NULL,
            urban INTEGER NOT NULL DEFAULT 0, src INTEGER NOT NULL DEFAULT 0);
        CREATE VIRTUAL TABLE roads_idx USING rtree(id, min_lat, max_lat, min_lon, max_lon);
    """)
    h = Roads(db, dot, fill_only)
    h.apply_file(src, locations=True)
    # Unposted minor roads in areas dense with streets (subdivisions, town grids) are
    # "urban": the app uses the state's residential/business-district default there.
    def dense(cell):
        i, j = cell
        return sum(h.cells.get((i + a, j + b), 0) for a in (-1, 0, 1) for b in (-1, 0, 1)) >= URBAN_MIN
    urban_ids = [(wid,) for wid, cell in h.minor if dense(cell)]
    filled = fill_gaps(db, h.pieces) if dot else 0
    db.executemany("UPDATE roads SET urban = 1 WHERE id = ?", urban_ids)
    built = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.executemany("INSERT INTO meta VALUES(?,?)", [
        ("state", state), ("built_utc", built), ("schema", "4"), ("urban_roads", str(len(urban_ids))),
        ("dot_credit", credit or ""), ("dot_roads", str(h.from_dot)), ("gap_filled", str(filled)),
        ("geom", "zigzag-varint delta, 1e-5 deg, first point absolute"),
        ("roads", str(h.n)), ("posted", str(h.posted)),
        ("attribution", "© OpenStreetMap contributors, ODbL"),
    ])
    db.commit()
    db.execute("VACUUM")
    db.close()
    pct = 100 * h.posted // max(h.n, 1)
    print(f"{state}: {h.n} roads, {h.posted} with posted limit ({pct}%), {h.from_dot} from the state DOT, "
          f"{filled} gaps filled from neighboring official segments, {len(urban_ids)} minor roads marked urban")
    print(f"{out}: {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
