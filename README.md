# stac2csw

**An OGC CSW 2.0.2 catalogue service for STAC catalogues, demonstrated on ISRO's Bhoonidhi.**

Bhoonidhi exposes its holdings only through a STAC API behind a login. `stac2csw` harvests that
catalogue, converts every collection and item into an **ISO 19115 / ISO 19139** metadata record, and
serves the result through a standards-compliant **OGC Catalogue Service for the Web** (pycsw), so
CSW-speaking tools (QGIS MetaSearch, GeoNetwork, OWSLib, INSPIRE-style harvesters) can discover
Bhoonidhi data with no STAC-specific code.

> **Unofficial.** Student project. Not affiliated with, or endorsed by, NRSC/ISRO. Only *metadata* is
> copied; data products stay on Bhoonidhi and still need a Bhoonidhi account to download.

---

## 1. Gap identified

| | |
|---|---|
| **Dataset / function** | The Bhoonidhi *metadata catalogue* (58 collections listed in the API specification, across ResourceSat-2/2A, EOS-04, EOS-06, Sentinel-1A, NovaSAR-1 AIS, CartoDEM, NISAR) |
| **Today** | Discovery is possible only through `bhoonidhi-api.nrsc.gov.in/data/*`, a STAC API that needs a JWT from `/auth/token`. The published API specification covers authentication, collections, search, download, rate limits and errors, and documents no OGC catalogue service and no ISO 19115 metadata. |
| **Why that matters** | STAC is a catalogue *specification*, not an OGC catalogue *service*. Spatial Data Infrastructure tooling speaks CSW + ISO 19139: QGIS MetaSearch, GeoNetwork harvesting, national geoportals, INSPIRE. None of it can discover Bhoonidhi data. Every consumer must write STAC-specific code, and the holdings never appear in any interoperable catalogue. |
| **This is the "Find" problem from the course** | Encoding standard = metadata (ISO 19115/19139); interface standard = catalogue service (CSW). Bhoonidhi has the content but neither standard. |

### How the gap was checked (and its limits)

* `scripts/probe_gap.py` sends `GetCapabilities` for CSW, WMS, WFS, WCS and WPS to a list of likely paths on
  both Bhoonidhi hosts. A host counts as a service **only** if the body is a real capabilities document, so a
  200 response carrying a catch-all HTML page is correctly recorded as "no". The script writes
  `docs/gap-evidence.md`; run it yourself and commit the output; this README makes no claim about what it will show.
* The detector was validated with a positive control: pointed at this repository's own CSW it reports
  `YES` for CSW and `no` for the other four services.
* The API specification (https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/) was read in full for this project.
* **Limit:** absence of evidence is not proof. A CSW could exist on a path or host that was not probed or that
  is not publicly reachable. The statement is "none found at the probed locations and none documented".

## 2. Service design

```
 Bhoonidhi STAC API                     stac2csw                                 clients
┌────────────────────┐   ┌─────────────────────────────────────────┐   ┌─────────────────────────┐
│ /auth/token  (JWT) │   │ harvest (client.py)                     │   │ QGIS MetaSearch         │
│ /data/collections  │──►│  auth, refresh, throttle (3 req/s),     │   │ GeoNetwork (harvest)    │
│ /data/search (next)│   │  paging, 429/5xx back-off               │   │ OWSLib / curl / browser │
└────────────────────┘   │            │                            │   └────────────▲────────────┘
                         │ map (iso19139.py)                       │                │ CSW 2.0.2
                         │  STAC Collection -> ISO "series"        │   ┌────────────┴────────────┐
                         │  STAC Item       -> ISO "dataset"       │   │ pycsw 2.6.2 (APISO)     │
                         │            │ ISO 19139 XML files        │──►│ GetCapabilities         │
                         │ load  (pycsw-admin) -> SQLite           │   │ DescribeRecord          │
                         └─────────────────────────────────────────┘   │ GetRecords              │
                                                                       │ GetRecordById           │
                                                                       └─────────────────────────┘
```

**Batch harvest, then serve.** Harvesting is a separate step that fills a pycsw repository; the CSW
endpoint then serves from that database. Reasons: Bhoonidhi needs credentials and is rate-limited
(token endpoint 20 requests/hour, search 3 requests/second), so catalogue queries must never trigger live
upstream calls; and clients get fast, predictable answers even if Bhoonidhi is slow or down.

### STAC to ISO 19115 mapping

| ISO 19139 element | Item (`dataset`) | Collection (`series`) |
|---|---|---|
| `fileIdentifier` | `<collection>:<item id>` | collection id |
| `parentIdentifier` | collection id | not set |
| `hierarchyLevel` | `dataset` | `series` |
| `citation/title` | `properties.title`, else item id | `title`, else id |
| `abstract` | `properties.description`, else generated line | `description`, else title |
| `EX_GeographicBoundingBox` | `bbox`, else derived from `geometry` | `extent.spatial.bbox[0]` |
| `EX_TemporalExtent` | `start/end_datetime` or `datetime` | `extent.temporal.interval[0]`; open end becomes `indeterminatePosition="now"` |
| `referenceSystemInfo` | EPSG:4326 (the CRS of the bbox; GeoJSON is WGS84 by RFC 7946) | same |
| `descriptiveKeywords` | collection id, platform, constellation, instruments, sensor, product_type when present | `keywords`, `summaries.platform` |
| `topicCategory` | rule table by collection prefix (heuristic, see `iso19139.TOPIC_RULES`) | same |
| `distributionInfo/onLine` | STAC item link + Bhoonidhi `/download?id=…&collection=…` | STAC collection link + portal |
| `resourceConstraints` | restricted: registered Bhoonidhi account required (+ STAC `license` if present) | same |
| `dataQualityInfo/lineage` | "harvested from <API> by stac2csw on <date>; metadata only" | same |
| `contact` (the metadata record) | the harvester operator (configurable, "unofficial") | same |
| `identificationInfo/pointOfContact` | NRSC, ISRO (the data provider) | same |

Two rules govern the mapper: **nothing is invented** (a missing STAC field is omitted from the record, never
filled with a guess; unit-tested), and **the metadata contact is not the data provider** (ISO distinguishes
who maintains the *record* from who owns the *data*).

## 3. OGC compliance

Implemented by **pycsw 2.6.2** with the `apiso` profile (ISO 19115/19139 queryables and output schema).

| CSW 2.0.2 operation | Status | Notes |
|---|---|---|
| `GetCapabilities` | yes | service identification, provider, operations, filter capabilities |
| `DescribeRecord` | yes | `csw:Record` schema |
| `GetRecords` | yes | KVP and XML POST; CQL text and OGC Filter; `brief`/`summary`/`full`; paging (`startPosition`, `maxRecords`, `nextRecord`); outputSchema Dublin Core **or** ISO 19139 |
| `GetRecordById` | yes | by `fileIdentifier`; ISO 19139 output |
| `GetDomain`, `GetRepositoryItem` | yes | provided by pycsw |
| `Harvest` / `Transaction` | **disabled** | the catalogue is read-only; the repository is refreshed only by `stac2csw harvest`. Enabling them is a one-line change in `config/pycsw.cfg` but would let anyone modify the catalogue. |

Supported filters, all verified by tests: free text (`csw:AnyText LIKE`), spatial (`ogc:BBOX`), logical
`And`, and combinations of these. Errors are returned as standard `ows:ExceptionReport` documents.

## 4. Quick start

```bash
git clone https://github.com/ujjuNotGujju/stac2csw && cd stac2csw
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server,dev]"
```

**A. Offline demo (no credentials; synthetic data):**

```bash
python tests/make_fixtures.py                                  # already committed; regenerates
stac2csw harvest --offline tests/fixtures --out records --clean
stac2csw load --config config/pycsw.cfg --records records --reset
scripts/run_local.sh                                           # http://127.0.0.1:8000/csw
python scripts/smoke_test.py http://127.0.0.1:8000/csw
```

**B. Live Bhoonidhi (needs a Bhoonidhi account):**

```bash
export BHOONIDHI_USER=...  BHOONIDHI_PASSWORD=...             # never passed on the command line
stac2csw harvest --out records --clean --max-items 100         # all collections, 100 items each
# narrower, e.g. one collection and one year:
stac2csw harvest --out records --clean \
  --collections EOS-06_OCM-GAC_L2C-CHL --datetime 2024-01-01T00:00:00Z/2024-12-31T23:59:59Z
stac2csw load --config config/pycsw.cfg --records records --reset
scripts/run_local.sh
python scripts/probe_gap.py                                    # regenerates docs/gap-evidence.md
```

**C. Docker (untested in the author's environment, see Limitations):**

```bash
docker compose --profile tools run --rm harvest-demo   # or harvest-live (copy .env.example to .env)
docker compose up -d csw                               # http://localhost:8000/csw
```

## 5. Using the service

```bash
CSW=http://localhost:8000/csw
# capabilities
curl "$CSW?service=CSW&version=2.0.2&request=GetCapabilities"
# schema of the record model
curl "$CSW?service=CSW&version=2.0.2&request=DescribeRecord&typeName=csw:Record"
# free-text search (CQL), summary records, first page of 10
curl -G "$CSW" --data-urlencode service=CSW --data-urlencode version=2.0.2 --data-urlencode request=GetRecords \
  --data-urlencode typeNames=csw:Record --data-urlencode resultType=results --data-urlencode elementSetName=summary \
  --data-urlencode constraintLanguage=CQL_TEXT --data-urlencode "constraint=csw:AnyText like '%CartoDEM%'" --data-urlencode maxRecords=10
# one record as full ISO 19139
curl -G "$CSW" --data-urlencode service=CSW --data-urlencode version=2.0.2 --data-urlencode request=GetRecordById \
  --data-urlencode "id=CartoSat-1_PAN_CartoDEM_30m" --data-urlencode outputSchema=http://www.isotc211.org/2005/gmd --data-urlencode elementSetName=full
```

Spatial and combined queries use OGC Filter XML (`scripts/demo_requests.sh` has POST examples), or any client:

```python
from owslib.csw import CatalogueServiceWeb
from owslib.fes import BBox, PropertyIsLike, And
c = CatalogueServiceWeb("http://localhost:8000/csw", version="2.0.2")
c.getrecords2(constraints=[And([PropertyIsLike("csw:AnyText", "%chlorophyll%"),
                                BBox([10, 70, 20, 80], crs="urn:ogc:def:crs:EPSG::4326")])], esn="brief")
print(c.results["matches"], list(c.records))
```

**QGIS:** Web ▸ MetaSearch ▸ Services ▸ New ▸ URL `http://localhost:8000/csw` ▸ Search.
**GeoNetwork:** Admin ▸ Harvesting ▸ OGC-CSW ▸ the same URL.
Note the EPSG:4326 axis order: with the `urn:ogc:def:crs:EPSG::4326` CRS a bbox is **lat,lon** (`miny,minx,maxy,maxx`).

## 6. Tests

```bash
pytest -q        # 28 tests
```

| File | What it proves |
|---|---|
| `tests/test_mapper.py` | ISO structure; bbox element order (west, east, south, north); bbox from geometry; 6-value bbox; missing fields omitted; open-ended collection time; links; contact vs provider; XML escaping |
| `tests/test_client.py` | against a local mock of the Bhoonidhi API: POST and GET `next` paging with `merge`; `max_items`; 429 back-off; expired token handled with the **refresh token** rather than a new login; exactly **one** token request per run (budget is 20/hour); bad password raises |
| `tests/test_integration.py` | fixtures to ISO to pycsw to real CSW requests via OWSLib: operations advertised, all 24 records present, keyword search, paging, `GetRecordById` returns ISO with parent and download link, bad request returns an exception report, and **bbox results equal an independent shapely intersection** for four query boxes, including one over the Pacific that must return nothing |

`scripts/smoke_test.py <url>` runs the operations against any live endpoint (use it just before a demo).

## 7. Implementation choices

* **pycsw, not a hand-rolled CSW.** The marks are for correct operations; pycsw is an OGC reference
  implementation of CSW, so effort goes into the part that does not exist: the Bhoonidhi to ISO mapping.
* **ISO 19139 (19115:2003) rather than 19115-3.** It is what pycsw's APISO profile ingests and what CSW
  clients (MetaSearch, GeoNetwork) expect. The records state `ISO 19115 / 2003/Cor 1:2006`.
* **Items *and* collections.** Collections are `series` records, items `dataset` records linked by
  `parentIdentifier`, mirroring STAC's hierarchy, so a client can browse top-down or search flat.
* **Pinned versions.** `pycsw==2.6.2`, `OWSLib<0.29`, `sqlalchemy<2` (pycsw 2.x requirement), Python 3.12.
* **`gzip_compresslevel` is deliberately unset.** In pycsw 2.6.2 the WSGI wrapper sends the *uncompressed*
  `Content-Length` with a gzip body, which breaks OWSLib/QGIS ("IncompleteRead"). Found and fixed during testing.
* **Rate limits respected by construction:** one login, refresh-token renewal, 0.4 s between calls.
* **`bbox_of()` normalises south/north by `min`/`max`, never trusts field position for latitude.**
  A live Bhoonidhi item (`EOS-06_OCM-GAC_L2C-CHL`) was found with its own `bbox` field carrying
  `[west, north, east, south]` — the opposite of the STAC-standard `[west, south, east, north]` — confirmed
  by cross-checking against that item's `geometry`. Trusting field position would have written an inverted
  `EX_GeographicBoundingBox` (`south > north`) into the ISO record, silently breaking CSW `BBOX` spatial
  queries for that item. Fixed and covered by a regression test using the real observed values.

## 8. Known limitations

* **Verified against the live Bhoonidhi API** (§4B) with real credentials: `collections()` and `search()`
  both work end-to-end against production. One real bug was found this way and fixed — see the bbox note
  in §7. Temporal mapping (`properties.datetime`) was confirmed correct on a real item. Not exhaustively
  checked: the full 62-collection catalogue was not harvested in full, only a handful of items across two
  collections (`CartoSat-1_PAN_CartoDEM_30m`, `EOS-06_OCM-GAC_L2C-CHL`), so further field-naming surprises
  in collections not yet sampled are possible — the mapper reads optional fields defensively, so the
  failure mode for an unseen quirk is a thinner record, not a crash.
* **Demo data in the repository is synthetic** (`tests/fixtures`, ids prefixed `SYNTH_`, titles say so).
  Collection ids are real; everything else is invented. Never mistake it for Bhoonidhi holdings.
* **Not a full mirror.** Bhoonidhi holds a very large number of items. The default harvests up to 100 items per
  collection; pass `--max-items`, `--datetime`, `--collections` to choose what to index. Catalogue freshness
  equals the last harvest; there is no incremental update yet.
* **Records are not validated against the ISO XSD** (the schemas could not be fetched in the build
  environment). Element order follows the 19139 schema and pycsw parses every record, but formal validation
  should be run (`pycsw-admin.py -c validate_xml -x record.xml -s gmd.xsd`).
* **Heuristics:** `topicCategory` comes from a small prefix table; the CRS recorded is that of the bbox
  (EPSG:4326), not each product's native CRS.
* **Harvest and CSW-T are disabled** by design (section 3).
* **Docker files are untested** (no Docker daemon in the author's build environment); the same commands were
  verified as plain Python processes.
* SQLite is fine for tens of thousands of records. For a full catalogue, point `[repository] database` at PostgreSQL.
* **Terms of use:** harvesting uses your own Bhoonidhi credentials and the documented API within its stated
  limits. Check Bhoonidhi's terms before publishing a catalogue of its metadata on a public host.

## 9. Repository layout

```
src/stac2csw/client.py      Bhoonidhi STAC client (auth, throttle, paging) + offline FixtureClient
src/stac2csw/iso19139.py    STAC -> ISO 19139 mapper
src/stac2csw/cli.py         stac2csw harvest | load
config/pycsw.cfg            pycsw server configuration (read-only catalogue)
scripts/run_local.sh        run the CSW endpoint
scripts/smoke_test.py       live check of every operation
scripts/probe_gap.py        gap evidence generator
scripts/demo_requests.sh    curl examples
tests/                      28 tests + synthetic fixtures
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
```

## License

MIT. Bhoonidhi and its data belong to NRSC/ISRO.
