# State law file format (one file per state: laws/states/XX.json)

Used by the "Am I Speeding?" app to show, for the driver's current state:
the default speed limit when a road has no posted limit, the estimated fine,
court costs, license points, and when speeding becomes a criminal/arrestable offense.

Every number must come from a source you actually read (statute, state court
fine schedule, DMV page). Prefer official .gov sources. If you cannot find a
number, use null and explain in "notes" — never guess. Speeds are mph.
"over" always means mph over the limit.

```json
{
  "code": "VA",
  "name": "Virginia",
  "researched": "2026-11-02",

  "default_limits": {
    "_comment": "Statutory limit that applies when no sign is posted, mapped to OpenStreetMap road classes. null if no statutory default applies to that class.",
    "motorway": 55, "trunk": 55, "primary": 55, "secondary": 55, "tertiary": 55,
    "unclassified": 55, "residential": 25, "living_street": 25,
    "basis": "Va. Code § 46.2-870 (55 general), § 46.2-874 (25 business/residence districts)"
  },

  "fine": {
    "model": "per_mph | brackets | county_schedule | judge_discretion",
    "per_mph": 6,                      "_comment1": "model per_mph: fine = base + per_mph * over",
    "base": 0,
    "brackets": [ {"min_over": 1, "max_over": 9, "fine": 0} ],
                                       "_comment2": "model brackets: max_over null = and up",
    "typical_range": [null, null],     "_comment3": "always fill when model is county_schedule/judge_discretion",
    "max_fine": 250,                   "_comment4": "statutory max for an ordinary (non-criminal) speeding ticket",
    "notes": "short plain-English note"
  },

  "court_costs": {
    "amount": 51, "range": [null, null],
    "percent_of_fine": null,           "_comment5": "optional: extra assessment as % of the fine (WA: 105)",
    "notes": "fixed statewide / varies by court / included in fine"
  },

  "points": {
    "system": true,
    "brackets": [ {"min_over": 1, "max_over": 9, "points": 3} ],
    "notes": "e.g. 'no points system; violations counted instead'"
  },

  "zones": {
    "work_zone": "e.g. 'up to $500 extra fine when workers present'",
    "school_zone": "..."
  },

  "tiers": {
    "_comment": "Drives the cop meter. ticket = any amount over. court = where consequences jump (mandatory court appearance, much higher fine bracket, possible license suspension). arrest = criminal/arrestable speeding (reckless driving, dangerous excessive speeding, etc.). A tier triggers if over >= min_over OR speed >= min_speed (either may be null).",
    "court":  {"min_over": 20, "min_speed": null, "reason": "..."},
    "arrest": {"min_over": 20, "min_speed": 86, "offense": "Reckless driving by speed (Class 1 misdemeanor)",
               "max_jail_days": 365, "max_fine": 2500, "license": "up to 6 months suspension", "citation": "Va. Code § 46.2-862"}
  },

  "trucks": {
    "different_limits": false,
    "notes": "e.g. trucks over 10,000 lb or vehicles towing: 55 on all highways (CA)"
  },

  "sources": [ {"title": "Va. Code § 46.2-862", "url": "https://law.lis.virginia.gov/vacode/46.2-862/"} ],
  "confidence": "high | medium | low",
  "notes": "anything the app or a reviewer should know"
}
```

Fine math (how the app computes an estimate):
- model per_mph: fine = base + per_mph × over (base/per_mph default 0).
- model brackets: find the bracket where min_over <= over <= max_over (max_over null = no top).
  fine = bracket.fine + bracket.per_mph × over  (each defaults to 0; "over" is the FULL mph over,
  so a rule like "$480 + $10 per mph beyond 31" is stored as fine 170, per_mph 10).
  A bracket with "range": [lo, hi] and no fine means the judge decides; show the range.
- model county_schedule / judge_discretion: show typical_range.
- Court costs shown = amount (or range) + percent_of_fine% of the fine.
- If the matching bracket has no fine, per_mph or range, fall back to typical_range, then max_fine.

Tier rule: a tier triggers if over >= min_over OR speed >= min_speed.
Exception: if a tier has "and_min_speed", the min_over part only counts when speed >= and_min_speed
(Vermont: 30+ over AND 60+ mph).

Rules:
- The Virginia values above are ILLUSTRATIVE placeholders showing the shape only. Do not copy them; verify every value from sources.
- min_speed means "at or above this speed". "Over 85" = min_speed 86.
- If the state has no criminal-by-speed threshold (speeding is only reckless when combined with other conduct), set tiers.arrest to null-valued fields and explain — do not invent one.
- If arrest exposure exists only via a general reckless-driving statute with no speed number, say so in notes and leave min_over/min_speed null.
- Values are for a first offense, passenger car, ordinary road (not work/school zone).
- Keep every note short — the app shows them on a phone.
