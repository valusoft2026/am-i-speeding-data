# FHWA 2022 Speed Limit Study Results (ALL States)

Service: https://geo.dot.gov/server/rest/services/Hosted/FHWA__2022_Speed_Limit_Study_Results__All_States/FeatureServer/0

**Coverage:** Only posted speed limits on the **National Highway System (NHS)**, nationwide. The extent runs from Hawaii and Alaska to Maine and appears to include Puerto Rico; I did not check per-state record counts. The linework is HPMS 2020 segments for facility types 1, 2 and 5 in the inventory direction. State-submitted HPMS speed data and posted-speed data were conflated onto that linework. It has no local or secondary roads.

**Fields:** `speed_limit`, `secondary_speed_limit`, `secondary_speed_limit_type`, `speed_limit_source`, `merge_src`, `state_code` (FIPS), `f_system`, `facility_type`, `urban_code`, `route_id`, `min_begin_point`, `max_end_point`, `year_record`. The geometry is polylines.

**Access:** This is a public ArcGIS FeatureServer with `maxRecordCount` 2000 and JSON-only query output (no geoJSON or PBF). A monthly job can page it with `where=state_code=NN` and `resultOffset`.

**Freshness:** This is a one-time 2022 study on 2020 linework. It is not maintained, so changes since then are missing.

**License:** No license is stated. The copyright text says "Dataset created by High Street under contract to the US DOT FHWA" and credits Alaska DOT&PF. As a US federal work it is probably public domain, but that is not stated, so `commercial_ok` = unclear and leans yes.

**Oddity:** The service advertises `Append` and ApplyEdits capabilities on a public endpoint. I did not test this. Treat it as read-only and do not rely on its integrity.

**Usability verdict:** It is a useful nationwide fallback for interstates and major highways, especially in states with no DOT data (WY, CA, OR, AK, HI here). The data is stale and covers only the NHS. It is best used as a low-priority source that state DOT data overrides.
