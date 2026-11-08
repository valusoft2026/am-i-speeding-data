#!/usr/bin/env python3
"""Why does a street get the wrong limit? Compare the official sources against our road file.

Usage: python3 dot_debug.py "Ira Hoffman" [out/VA.sqlite]
Prints: our road pieces for that street; VDOT speed segments near it (with distance from our
line); and Virginia statewide centerline (VGIN RCL) records with SPEED_MPH / VDOT_SPD.
"""
import json
import math
import sqlite3
import sys
import urllib.parse
import urllib.request

from build_state import unpack

VDOT = "https://services.arcgis.com/p5v98VHDX9Atv3l7/ArcGIS/rest/services/VDOT_Posted_Speed_Limits/FeatureServer/0"
RCL = "https://vginmaps.vdem.virginia.gov/arcgis/rest/services/VA_Base_Layers/VBMP_RCL/FeatureServer"
UA = {"User-Agent": "AmISpeeding-data-builder/1.0"}


def get(url, params):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def dist_to_lines(pt, lines):
    lat, lon = pt
    kx, ky = 111320 * math.cos(math.radians(lat)), 110540
    best = 1e9
    for line in lines:
        for a, b in zip(line, line[1:]):
            ax, ay = (a[1] - lon) * kx, (a[0] - lat) * ky
            bx, by = (b[1] - lon) * kx, (b[0] - lat) * ky
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy or 1e-9
            t = max(0, min(1, -(ax * dx + ay * dy) / l2))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def main():
    street = sys.argv[1]
    db = sqlite3.connect(sys.argv[2] if len(sys.argv) > 2 else "out/VA.sqlite")
    ours = []
    print(f"== Our road file: '{street}'")
    for sp, src, cls, g in db.execute("SELECT speed_mph, src, class, geom FROM roads WHERE name LIKE ?", (f"%{street}%",)):
        p = unpack(g)
        ours.append(p)
        print(f"  {p[0][0]:.5f},{p[0][1]:.5f} -> {p[-1][0]:.5f},{p[-1][1]:.5f}  limit {sp}  src {src}  class {cls}")
    if not ours:
        sys.exit("street not found in road file")
    lats = [q[0] for p in ours for q in p]
    lons = [q[1] for p in ours for q in p]
    env = f"{min(lons) - 0.003},{min(lats) - 0.003},{max(lons) + 0.003},{max(lats) + 0.003}"
    geo = {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
           "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "true", "f": "json"}

    print("\n== VDOT speed segments in that area (closest to our street first)")
    r = get(VDOT + "/query", dict(geo, where="1=1", outFields="*", resultRecordCount=200))
    rows = []
    for f in r.get("features", []):
        a = f["attributes"]
        paths = [[[q[1], q[0]] for q in path] for path in f.get("geometry", {}).get("paths", [])]
        if not paths:
            continue
        d = min(dist_to_lines(q, ours) for path in paths for q in path[:: max(1, len(path) // 8)])
        name = a.get("ROUTE_COMMON_NAME") or a.get("RTE_NM") or ""
        rows.append((d, a.get("CAR_SPEED_LIMIT"), name, a.get("SPEEDZONE_TYPE_DSC") or "", paths[0][0], paths[-1][-1]))
    for d, sp, name, typ, s, e in sorted(rows)[:25]:
        print(f"  {d:6.0f} m away  {sp} mph  {name}  {typ}  {s[0]:.5f},{s[1]:.5f} -> {e[0]:.5f},{e[1]:.5f}")

    print("\n== Virginia statewide centerlines (VGIN RCL) for that street")
    info = get(RCL, {"f": "json"})
    for lyr in info.get("layers", []):
        try:
            li = get(f"{RCL}/{lyr['id']}", {"f": "json"})
            names = [f["name"] for f in li.get("fields", [])]
            nf = next((n for n in names if n.upper() in ("ST_FULLNAME", "FULLNAME", "STREET_NAME", "RD_NAME", "STREET")), None)
            spd = [n for n in names if "SPD" in n.upper() or "SPEED" in n.upper()]
            print(f"  layer {lyr['id']} '{lyr['name']}': name field {nf}, speed fields {spd}")
            if not nf or not spd:
                continue
            q = get(f"{RCL}/{lyr['id']}/query", dict(geo, where=f"UPPER({nf}) LIKE '%{street.upper()}%'",
                                                    outFields=",".join([nf] + spd), returnGeometry="false"))
            for f in q.get("features", [])[:15]:
                print("     ", f["attributes"])
        except Exception as e:  # keep going on any layer error
            print(f"  layer {lyr.get('id')}: {e}")


if __name__ == "__main__":
    main()
