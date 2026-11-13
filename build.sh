#!/usr/bin/env bash
# Build one state's road file.  Usage: ./build.sh VA virginia
# Output: out/VA.sqlite.gz
set -euo pipefail
cd "$(dirname "$0")"
CODE="$1"; NAME="$2"
PY="${PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
mkdir -p raw out
SRC="raw/$NAME.osm.pbf"
# Geofabrik sometimes refuses some GitHub runners; fall back to the OpenStreetMap France mirror.
if [ ! -f "$SRC" ]; then
  ok=0
  for URL in "https://download.geofabrik.de/north-america/us/$NAME-latest.osm.pbf" \
             "https://download.openstreetmap.fr/extracts/north-america/us/$NAME-latest.osm.pbf" \
             "https://download.openstreetmap.fr/extracts/north-america/us/${NAME//-/_}-latest.osm.pbf"; do
    if curl -fL --retry 3 --retry-delay 20 --retry-all-errors -o "$SRC.part" "$URL"; then
      mv "$SRC.part" "$SRC"; ok=1; echo "Map data from $URL"; break
    fi
    echo "WARNING: $URL failed, trying the next source"
  done
  [ "$ok" = 1 ] || { echo "ERROR: no map source reachable for $NAME"; exit 1; }
fi
osmium tags-filter "$SRC" \
  w/highway=motorway,trunk,primary,secondary,tertiary,unclassified,residential,living_street,motorway_link,trunk_link,primary_link,secondary_link,tertiary_link \
  -o "raw/$CODE-roads.osm.pbf" --overwrite --no-progress
# Official state DOT speed limits, where the state publishes them (skipped otherwise).
# If the DOT server is down, build from OpenStreetMap alone rather than fail.
rm -f "raw/dot_$CODE.json"
"$PY" dot_sources.py "$CODE" "raw/dot_$CODE.json" || echo "WARNING: $CODE DOT download failed; using OpenStreetMap only"
"$PY" build_state.py "raw/$CODE-roads.osm.pbf" "out/$CODE.sqlite" "$CODE" "raw/dot_$CODE.json"
# Known fixed speed cameras (OpenStreetMap, plus city open data where published).
osmium tags-filter "$SRC" n/highway=speed_camera -o "raw/$CODE-cams.osm.pbf" --overwrite --no-progress
"$PY" cameras.py "$CODE" "raw/$CODE-cams.osm.pbf" "out/$CODE.sqlite" || echo "WARNING: $CODE cameras failed; road file has none"
gzip -9 -f "out/$CODE.sqlite"
ls -lh "out/$CODE.sqlite.gz"
