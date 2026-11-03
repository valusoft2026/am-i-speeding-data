# Official speed-limit GIS data — per-state survey

Goal: for each state (+DC), find whether the state DOT (or the state's open-data portal) publishes
POSTED SPEED LIMITS as downloadable GIS road segments that a small paid phone app could use to
improve its speed-limit data. The app's build pipeline (a monthly GitHub Action) would download the data
and match it onto OpenStreetMap roads.

Write one JSON object per state to /home/claude/am-i-speeding-data/sources/states/XX.json:

```json
{
  "code": "VA",
  "available": true,
  "dataset": "VDOT Posted Speed Limits",
  "publisher": "Virginia DOT",
  "url": "landing/catalog page you read",
  "service_url": "ArcGIS FeatureServer/MapServer layer URL, or direct download URL (zip/geojson/shp/gdb)",
  "format": "arcgis-featureserver | arcgis-mapserver | geojson | shapefile | gdb | csv | other",
  "coverage": "e.g. all state-maintained roads incl. secondary | state highways only | NHS only | statewide all public roads",
  "fields": "speed field name(s), e.g. CAR_SPEED_LIMIT, TRUCK_SPEED_LIMIT",
  "updated": "last update date seen",
  "license": "license or terms text/URL; say 'none stated' if none",
  "commercial_ok": "yes | unclear | no",
  "notes": "anything important (needs registration, rate limits, only partial counties, etc.)",
  "confidence": "high | medium | low"
}
```

Rules:
- Only count data you actually found (a page or service you opened). Don't guess URLs.
- If the state publishes nothing usable, set available=false and say what you found (e.g. "only a PDF map").
- Prefer official state sources. Search terms that work: "<state> DOT speed limit GIS", "<state> open data speed limit
  feature service", "<state> posted speed limits shapefile", site:arcgis.com "<state>" speed limit.
- Budget: about 3 web searches per state. WebFetch of a URL found by search is fine.
