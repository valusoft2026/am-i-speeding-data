"""Match official DOT speed-limit segments onto OpenStreetMap road lines.

A road point takes the DOT speed of the nearest DOT segment within MAX_DIST meters that runs
roughly parallel (so cross streets at an intersection don't pick it up). Roads are split where
the DOT speed changes, so a road entering a town can go 55 -> 35 mid-way.
"""
import math

MAX_DIST = 12.0     # meters
MAX_ANGLE = 35.0    # degrees between road and DOT segment
STEP = 20.0         # meters between sample points along a road
MIN_RUN = 3         # samples; shorter speed blips are merged into the neighbor
CELL = 0.0005       # degrees (~50 m) grid for the spatial index

_KY = 110540.0


def _kx(lat):
    return 111320.0 * math.cos(math.radians(lat))


class DotIndex:
    def __init__(self, segments):
        self.grid = {}
        self.pieces = []
        for seg in segments:
            p = seg["p"]
            for a, b in zip(p, p[1:]):
                i = len(self.pieces)
                self.pieces.append((a[0], a[1], b[0], b[1], seg["s"], seg.get("t")))
                for cell in self._cells(a, b):
                    self.grid.setdefault(cell, []).append(i)

    @staticmethod
    def _cells(a, b):
        i0, i1 = sorted((int(a[0] // CELL), int(b[0] // CELL)))
        j0, j1 = sorted((int(a[1] // CELL), int(b[1] // CELL)))
        if (i1 - i0 + 1) * (j1 - j0 + 1) > 400:  # absurdly long piece: index endpoints only
            return {(int(a[0] // CELL), int(a[1] // CELL)), (int(b[0] // CELL), int(b[1] // CELL))}
        return {(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}

    def lookup(self, lat, lon, bearing):
        """(car, truck) speed of the best DOT piece near this point, or None."""
        i, j = int(lat // CELL), int(lon // CELL)
        kx = _kx(lat)
        best, best_d = None, MAX_DIST
        seen = set()
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for k in self.grid.get((i + di, j + dj), ()):
                    if k in seen:
                        continue
                    seen.add(k)
                    alat, alon, blat, blon, s, t = self.pieces[k]
                    ax, ay = (alon - lon) * kx, (alat - lat) * _KY
                    bx, by = (blon - lon) * kx, (blat - lat) * _KY
                    dx, dy = bx - ax, by - ay
                    l2 = dx * dx + dy * dy
                    if l2 == 0:
                        continue
                    if bearing is not None:
                        pb = math.degrees(math.atan2(dx, dy)) % 180
                        diff = abs(pb - bearing % 180)
                        if min(diff, 180 - diff) > MAX_ANGLE:
                            continue
                    tt = max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
                    d = math.hypot(ax + tt * dx, ay + tt * dy)
                    if d < best_d:
                        best_d, best = d, (s, t)
        return best


def _densify(coords):
    """Points every <= STEP meters along the line, each with the bearing of its segment."""
    out = []
    for (alat, alon), (blat, blon) in zip(coords, coords[1:]):
        kx = _kx(alat)
        dx, dy = (blon - alon) * kx, (blat - alat) * _KY
        length = math.hypot(dx, dy)
        bearing = math.degrees(math.atan2(dx, dy)) % 360 if length > 0 else None
        n = max(1, int(math.ceil(length / STEP)))
        for k in range(n):
            f = k / n
            out.append((alat + (blat - alat) * f, alon + (blon - alon) * f, bearing))
    last = coords[-1]
    out.append((last[0], last[1], out[-1][2] if out else None))
    return out


def split_by_dot(coords, index):
    """Return [(coords, car_mph|None, truck_mph|None), ...] covering the whole road."""
    pts = _densify(coords)
    vals = [index.lookup(la, lo, b) for la, lo, b in pts]
    if not any(vals):
        return [(coords, None, None)]
    # runs of equal values
    runs = []
    for i, v in enumerate(vals):
        if runs and runs[-1][2] == v:
            runs[-1][1] = i
        else:
            runs.append([i, i, v])
    # merge short blips into the previous run (or the next, for the first one)
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for r in range(len(runs)):
            a, b, v = runs[r]
            if b - a + 1 < MIN_RUN:
                if r > 0:
                    runs[r - 1][1] = b
                else:
                    runs[1][0] = a
                runs.pop(r)
                # re-join neighbors that now have the same value
                for q in range(len(runs) - 1, 0, -1):
                    if runs[q][2] == runs[q - 1][2]:
                        runs[q - 1][1] = runs[q][1]
                        runs.pop(q)
                changed = True
                break
    out = []
    for r, (a, b, v) in enumerate(runs):
        end = b + 1 if r < len(runs) - 1 else b  # share the boundary point with the next run
        line = [(la, lo) for la, lo, _ in pts[a:end + 1]]
        if len(line) >= 2:
            out.append((line, v[0] if v else None, v[1] if v else None))
    return out or [(coords, None, None)]
