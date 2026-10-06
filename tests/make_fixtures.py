"""Generate SYNTHETIC STAC fixtures for offline tests/demo.

Collection ids are real Bhoonidhi ids (from the public API specification); every
title, extent, date and item below is invented and labelled SYNTHETIC. They only
exercise the pipeline and must never be mistaken for Bhoonidhi holdings.
"""
import json
import random
from pathlib import Path

random.seed(7)
OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)

COLLS = {
    "EOS-06_OCM-GAC_Chlorophyll_8day_4km": ((40, -10, 110, 30), "2022-11-26T00:00:00Z"),
    "CartoSat-1_PAN_CartoDEM_30m": ((68, 6, 98, 37), "2011-01-01T00:00:00Z"),
    "Novasar-1_AIS": ((60, -5, 100, 25), "2022-01-01T00:00:00Z"),
    "ResourceSat-2_LISS3_L2": ((68, 6, 98, 37), "2011-04-20T00:00:00Z"),
}
collections = []
for cid, (bb, start) in COLLS.items():
    collections.append({
        "type": "Collection", "stac_version": "1.0.0", "id": cid,
        "title": f"{cid} (SYNTHETIC test fixture)",
        "description": f"SYNTHETIC test record standing in for collection {cid}. Not real Bhoonidhi metadata.",
        "license": "proprietary",
        "extent": {"spatial": {"bbox": [list(bb)]}, "temporal": {"interval": [[start, None]]}},
    })

items = []
for cid, (bb, start) in COLLS.items():
    for i in range(1, 6):
        w = bb[0] + random.random() * (bb[2] - bb[0] - 6)
        s = bb[1] + random.random() * (bb[3] - bb[1] - 6)
        e, n = w + 5, s + 5
        day = 1 + i * 3
        dt = f"2023-0{1 + i % 9}-{day:02d}T05:30:00Z"
        items.append({
            "type": "Feature", "stac_version": "1.0.0", "collection": cid,
            "id": f"SYNTH_{cid.split('_')[0]}_{i:03d}",
            "bbox": [round(w, 4), round(s, 4), round(e, 4), round(n, 4)],
            "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
            "properties": {"datetime": dt, "platform": cid.split("_")[0], "description": f"SYNTHETIC item {i} for {cid}."},
            "links": [], "assets": {},
        })
(OUT / "collections.json").write_text(json.dumps({"collections": collections}, indent=1))
(OUT / "items.json").write_text(json.dumps({"type": "FeatureCollection", "features": items}, indent=1))
print(f"{len(collections)} collections, {len(items)} items -> {OUT}")
