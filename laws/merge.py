#!/usr/bin/env python3
"""Check every laws/states/XX.json and merge them into laws/laws.json.

Usage: python3 laws/merge.py      (exit code 1 if any problems)
The app downloads laws.json from:
https://raw.githubusercontent.com/valusoft2026/am-i-speeding-data/master/laws/laws.json
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
         "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY").split()
CLASSES = ["motorway", "trunk", "primary", "secondary", "tertiary",
           "unclassified", "residential", "living_street"]
MODELS = {"per_mph", "brackets", "county_schedule", "judge_discretion"}


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def normalize(d):
    """Some researchers wrote a bracket's base amount as 'base' instead of 'fine'."""
    for b in (d.get("fine") or {}).get("brackets") or []:
        if b.get("fine") is None and b.get("base") is not None:
            b["fine"] = b["base"]
        b.pop("base", None)


def estimate(d, over):
    """Same math the app uses (see SCHEMA.md). Returns (low, high) or None."""
    f = d.get("fine") or {}
    if f.get("model") == "per_mph" and num(f.get("per_mph")):
        v = (f.get("base") or 0) + f["per_mph"] * over
        return (v, v)
    if f.get("model") == "brackets":
        for b in f.get("brackets") or []:
            lo, hi = b.get("min_over"), b.get("max_over")
            if num(lo) and over >= lo and (hi is None or over <= hi):
                if b.get("fine") is not None or b.get("per_mph"):
                    v = (b.get("fine") or 0) + (b.get("per_mph") or 0) * over
                    return (v, v)
                if b.get("range"):
                    return tuple(b["range"])
                break
    tr = f.get("typical_range") or [None, None]
    if num(tr[0]) or num(tr[1]):
        return (tr[0], tr[1])
    if num(f.get("max_fine")):
        return (None, f["max_fine"])
    return None


def check(code, d):
    p = []
    for over in (5, 15, 25, 35):
        e = estimate(d, over)
        if e and any(num(x) and x < 0 for x in e):
            p.append(f"negative fine estimate at {over} over: {e}")
    if d.get("code") != code:
        p.append(f"code field is {d.get('code')!r}")
    dl = d.get("default_limits") or {}
    for c in CLASSES:
        v = dl.get(c)
        if v is not None and not (num(v) and 5 <= v <= 85):
            p.append(f"default_limits.{c}={v!r}")
    if all(dl.get(c) is None for c in CLASSES):
        print(f"  note: {code} has no statewide default limits (app uses posted limits only)")
    f = d.get("fine") or {}
    if f.get("model") not in MODELS:
        p.append(f"fine.model={f.get('model')!r}")
    for b in (f.get("brackets") or []) + ((d.get("points") or {}).get("brackets") or []):
        if not num(b.get("min_over")):
            p.append(f"bracket missing min_over: {b}")
    t = d.get("tiers") or {}
    court, arrest = t.get("court") or {}, t.get("arrest") or {}
    for name, tier in (("court", court), ("arrest", arrest)):
        for k in ("min_over", "min_speed"):
            v = tier.get(k)
            if v is not None and not (num(v) and 1 <= v <= 150):
                p.append(f"tiers.{name}.{k}={v!r}")
    co, ao = court.get("min_over"), arrest.get("min_over")
    if num(co) and num(ao) and co > ao:
        p.append(f"court tier ({co} over) is above arrest tier ({ao} over)")
    if not d.get("sources"):
        p.append("no sources")
    if d.get("confidence") not in ("high", "medium", "low"):
        p.append(f"confidence={d.get('confidence')!r}")
    return p


def main():
    states, problems = {}, {}
    for code in CODES:
        path = os.path.join(HERE, "states", f"{code}.json")
        if not os.path.exists(path):
            problems[code] = ["file missing"]
            continue
        try:
            d = json.load(open(path))
        except json.JSONDecodeError as e:
            problems[code] = [f"bad JSON: {e}"]
            continue
        normalize(d)
        p = check(code, d)
        if p:
            problems[code] = p
        states[code] = d
    out = {
        "schema": 1,
        "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": "Estimates only. Actual fines, costs and penalties vary by court and circumstances. Not legal advice.",
        "states": states,
    }
    json.dump(out, open(os.path.join(HERE, "laws.json"), "w"), indent=1, ensure_ascii=False)
    print(f"{len(states)}/{len(CODES)} states merged into laws/laws.json")
    for code, p in problems.items():
        print(f"  {code}: " + "; ".join(p))
    if "--table" in sys.argv:
        def fmt(e):
            if not e:
                return "—"
            lo, hi = e
            return f"${lo:g}" if lo == hi else f"${lo if lo is not None else '?'}–{hi if hi is not None else '?'}"
        print(f"\n{'ST':3}{'5 over':>12}{'15 over':>12}{'25 over':>12}{'35 over':>12}  arrest")
        for code, d in states.items():
            a = (d.get("tiers") or {}).get("arrest") or {}
            ar = " / ".join(x for x in (f"{a['min_over']}+ over" if a.get("min_over") else "",
                                        f"{a['min_speed']}+ mph" if a.get("min_speed") else "") if x) or "none by speed"
            print(f"{code:3}" + "".join(f"{fmt(estimate(d, o)):>12}" for o in (5, 15, 25, 35)) + f"  {ar}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
