# stac2csw — how it works and why

A reference note for later. This explains the assignment problem, the standards involved,
every design decision in this codebase, and the things that broke during the build. It is
written so that *future you*, with no memory of this project, can reconstruct the reasoning
in one read.

---

## 1. The assignment, restated

The brief (CyberGIS mid-sem, Step 1–4) asks for four things:

1. **Explore** a real geospatial platform (Bhoonidhi, ISRO/NRSC's satellite data portal) and
   understand how a developer accesses its data today.
2. **Identify a gap**: some function that *should* be exposed through a standard OGC web
   service but currently is not — verified to actually be missing, not assumed.
3. **Build and deploy** an implementation of the missing OGC service, using a standard
   framework rather than writing the protocol by hand.
4. **Document and demo**: a GitHub repo, a README explaining the gap/design/choices/limits,
   and a live service anyone can hit with real HTTP requests.

Marking is 20 points: 5 for the gap being real and well-argued, 10 for correctly implementing
the chosen OGC standard's operations, 5 for the deployment actually working during the demo.

The key conceptual move the brief wants you to make is the distinction between two different
kinds of "standard":

| | What it standardises | Examples |
|---|---|---|
| **Encoding / content standard** | *How a record describing data is structured* | ISO 19115/19139, Dublin Core, GeoJSON |
| **Service / interface standard** | *How you ask a server for those records over HTTP* | CSW, WMS, WFS, WCS, WPS, OGC API |

A platform can have great data and even decent self-describing JSON (which STAC is) and still
have **no OGC service interface** — meaning no standard tool can discover or query it without
someone hand-writing code against that platform's bespoke API. That is exactly Bhoonidhi's
situation, and it's the gap this project fixes.

---

## 2. The gap, precisely

Bhoonidhi exposes its catalogue only via `bhoonidhi-api.nrsc.gov.in`, a **STAC API** —
`/data/collections`, `/data/search`, auth via `/auth/token`. STAC (SpatioTemporal Asset
Catalog) is itself a fine *encoding* for geospatial metadata (it's GeoJSON-based), but it is
not an OGC service interface, and it is not what SDI (Spatial Data Infrastructure) tooling
speaks.

The specific tools that are locked out:
- **QGIS MetaSearch** (the standard way GIS users browse catalogues from inside QGIS)
- **GeoNetwork** harvesting (what national/institutional geoportals use to aggregate catalogues)
- **INSPIRE-style harvesters** (EU and India's own NSDI-style infrastructure expects CSW + ISO 19139)
- Any **OWSLib**-based script, or generic CSW client

All of these speak **CSW (Catalogue Service for the Web)**, querying records encoded in
**ISO 19115 / ISO 19139**. Bhoonidhi has neither the service interface nor the encoding —
only a REST-ish JSON API behind a login.

**This was verified, not assumed** — see `scripts/probe_gap.py` and the generated
`docs/gap-evidence.md`. The script:
1. Sends real `GetCapabilities` requests for CSW, WMS, WFS, WCS, WPS to both Bhoonidhi hosts,
   across the path conventions those services conventionally live at (`/csw`, `/ows`,
   `/geoserver/ows`, `/geonetwork/srv/eng/csw`, …).
2. Only counts a hit as "yes" if the response body is a genuine OGC capabilities XML document
   (regex-matched against each service's capabilities root element/namespace) — a `200` that
   serves the SPA's catch-all HTML page does **not** count. This matters because naively
   checking HTTP status alone would produce false positives on any server that returns 200 for
   unknown routes.
3. Was validated with a **positive control**: pointed at this project's own live CSW, it
   correctly reports `YES` for CSW and `no` for the other four — proving the detector works
   before trusting a negative result elsewhere.
4. Also greps the published API specification text for standards keywords (`CSW`, `WMS`,
   `ISO 19115`, `19139`, `OGC API`) — all zero, `STAC` appears 12 times.

Run on 2026-10-12 against the live hosts: **no OGC capabilities document at any probed path on
either host.** (The one caveat stated honestly in the README: absence of evidence at the probed
paths is not proof of absence everywhere — a CSW could theoretically exist somewhere unprobed
or non-public. The claim is scoped to what was actually checked.)

---

## 3. What was built

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

**The core architectural decision: batch-harvest, then serve — never proxy live.**
A CSW `GetRecords` request never triggers a call to Bhoonidhi. Instead:
1. `stac2csw harvest` walks the STAC API once, writes one ISO 19139 XML file per collection
   and per item to disk.
2. `stac2csw load` (a thin wrapper around `pycsw-admin.py load_records`) bulk-loads those XML
   files into pycsw's own SQLite/Postgres repository.
3. The CSW HTTP endpoint (`pycsw.wsgi:application` under gunicorn) only ever reads from that
   local repository.

Reasons this matters, not just convenience:
- Bhoonidhi's `/auth/token` endpoint is rate-limited to **20 requests/hour/IP**, and search to
  **3 requests/second/IP**. A live-proxying catalogue would blow through that under any real
  query load; a harvest-once design makes one controlled pass.
- Bhoonidhi requires a login; a CSW client (QGIS, GeoNetwork) has no way to supply Bhoonidhi
  credentials through the CSW protocol. Harvesting with your own credentials once, then serving
  metadata openly, is the only way to make the catalogue reachable by standard tooling at all.
- Clients get fast, deterministic answers even if Bhoonidhi itself is slow, down, or revokes
  access later — the catalogue is a snapshot, which is also why "known limitations" calls out
  that freshness equals "time of last harvest."

### Why pycsw, not a hand-rolled CSW server

The rubric gives 10 of 20 marks for *correctly implementing the chosen standard's operations*.
pycsw is the reference open-source implementation of CSW 2.0.2 with the **APISO** profile
(ISO 19115/19139 queryables + output schema) — i.e. it already correctly implements
`GetCapabilities`, `DescribeRecord`, `GetRecords` (KVP + XML POST, CQL + OGC Filter, paging,
Dublin Core or ISO output), `GetRecordById`, `GetDomain`. Re-implementing that protocol by hand
would spend the whole project reinventing a solved problem instead of doing the part that's
actually novel here: **the STAC → ISO 19139 mapping**, which is where effort went
(`src/stac2csw/iso19139.py`, 309 lines, and its test coverage).

`Harvest`/`Transaction` (CSW-T, which lets *remote* servers push records in) are deliberately
left disabled in `config/pycsw.cfg` — the catalogue is read-only from the outside; the only way
to update it is to run `stac2csw harvest` + `load` again, under your own control. Enabling CSW-T
is one config line, but it would let any caller write into the catalogue.

### The STAC → ISO 19139 mapping (`iso19139.py`)

This is the actual engineering work of the project. Two rules govern it, stated in the module
docstring and enforced by tests:

1. **Nothing is invented.** A STAC field that's absent is *omitted* from the ISO record, never
   guessed or defaulted to a placeholder. The only defaults that exist are structural facts that
   are true by specification, not guesses — e.g. GeoJSON geometry is WGS84 / EPSG:4326 per
   RFC 7946, so `referenceSystemInfo` can state that safely for *any* valid STAC item.
2. **The metadata contact is not the data provider.** ISO 19115 distinguishes `gmd:contact`
   (who is responsible for *this metadata record* — here, the harvester/operator, marked
   "unofficial") from `identificationInfo/pointOfContact` (who owns the *actual data* — NRSC/
   ISRO). Collapsing these would misattribute authorship of a record you auto-generated to the
   original data provider.

Mapping table (STAC field → ISO element):

| ISO 19139 element | STAC Item (→ `dataset`) | STAC Collection (→ `series`) |
|---|---|---|
| `fileIdentifier` | `<collection id>:<item id>` | collection id |
| `parentIdentifier` | collection id (mirrors STAC's own hierarchy) | not set |
| `hierarchyLevel` | `dataset` | `series` |
| `citation/title` | `properties.title`, else item id | `title`, else id |
| `abstract` | `properties.description`, else a generated line | `description`, else title |
| `EX_GeographicBoundingBox` | `bbox` if present, else derived by walking `geometry.coordinates` recursively and taking min/max | `extent.spatial.bbox[0]` |
| `EX_TemporalExtent` | `start_datetime`/`end_datetime`, falling back to `datetime` for both | `extent.temporal.interval[0]`; an open-ended collection gets `indeterminatePosition="now"` on the end |
| `referenceSystemInfo` | EPSG:4326 always (GeoJSON is WGS84 by spec) | same |
| `descriptiveKeywords` | collection id + `platform`/`constellation`/`instruments`/`sensor`/`product_type` when present | `keywords` + `summaries.platform` |
| `topicCategory` | small prefix lookup table (`TOPIC_RULES`), e.g. `CartoSat-1_PAN_CartoDEM*` → `elevation`, `EOS-06_OCM*`/`EOS-06_SCAT*` → `oceans`, else `imageryBaseMapsEarthCover` | same |
| `distributionInfo/onLine` | link back to the STAC item + a Bhoonidhi `/download?...` link | link back to the STAC collection + portal URL |
| `resourceConstraints` | `restricted`, text says a Bhoonidhi account is required, plus the STAC `license` field if present | same |
| `dataQualityInfo/lineage` | "harvested from `<API>` by stac2csw on `<timestamp>`; metadata only" | same |
| `contact` (the *record's* author) | the harvester operator, configurable via `--contact-name`/`--contact-email` | same |
| `identificationInfo/pointOfContact` (the *data's* owner) | NRSC, ISRO, hardcoded as `PROVIDER` | same |

Implementation detail worth remembering: the XML is built with `lxml.etree` directly (helper
functions `_el`, `_cs` for `<tag><gco:CharacterString>…</gco:CharacterString></tag>` wrapping,
`_code` for ISO codelist-valued elements, `_responsible_party` for `CI_ResponsibleParty`
blocks) rather than through a templating engine. That keeps namespace handling
(`gmd`, `gco`, `gml`, `xlink`, `xsi`) correct by construction and made the bbox/time edge cases
(6-value bbox with elevation, geometry-derived bbox, open-ended time) straightforward to
unit-test in isolation (`tests/test_mapper.py`).

### The Bhoonidhi client (`client.py`)

Two implementations behind the same interface (`collections()`, `search()`):

- **`BhoonidhiClient`** — the real one. Handles:
  - Login via `/auth/token` (`userId`/`password`/`grant_type=password`), caching the access
    token and, critically, **preferring the refresh token** over a fresh password login when
    the access token expires (`_renew()`) — because the token endpoint itself is rate-limited
    to 20/hour, so re-logging-in on every expiry would burn that budget fast. Tested
    explicitly: a harvest run that triggers token expiry mid-run makes exactly **one** token
    request total, via refresh.
  - Throttling to stay under 3 req/s (`min_interval=0.4s` between calls, enforced in
    `_throttle()` regardless of which endpoint is called).
  - Exponential-ish back-off on `429`/`5xx`, honoring `Retry-After` when the server sends one.
  - STAC API item-search **paging** by following `links[].rel == "next"` — which per the STAC
    API spec can itself change HTTP method and body between pages (`nxt.get("method")`,
    `nxt.get("merge")` controlling whether the next body replaces or merges with the current
    one). This generality was kept even though the fixtures don't exercise every variant,
    because the live API's actual paging shape wasn't observable without credentials.
- **`FixtureClient`** — same two-method interface, backed by local JSON
  (`tests/fixtures/collections.json`, `items.json`). This is what makes the entire pipeline
  (harvest → map → load → serve → query) exercisable **offline**, with no Bhoonidhi account,
  both for the demo and for CI.

### Serving (`config/pycsw.cfg`, `scripts/run_local.sh`)

pycsw itself is unmodified; all behaviour is config:
- `[repository] database` — SQLite file path (swap for PostgreSQL by changing one line; noted
  in the README as the scaling path past "tens of thousands of records").
- `[manager] transactions=false` — CSW-T disabled (see above).
- `[metadata:main]` — service identification fields (title, abstract, contact) shown in
  `GetCapabilities`.
- Served via `gunicorn pycsw.wsgi:application`, not pycsw's own dev server, because gunicorn is
  the production-appropriate WSGI runner and this is what `docker-compose.yml` also uses — one
  code path for local dev and container deployment.

**A real bug found and fixed during testing:** pycsw 2.6.2's WSGI wrapper, when
`gzip_compresslevel` is set, gzips the response body but still reports the *uncompressed*
`Content-Length`. Every client that enforces Content-Length (OWSLib, QGIS) then sees the
connection as truncated (`IncompleteRead`) and fails, silently, on every single request. The
fix is simply to never set `gzip_compresslevel` — this is called out explicitly in both the
README and `config/pycsw.cfg` so it doesn't get "optimised" back in later.

---

## 4. How this was tested, and what each test layer actually proves

28 tests total, three files, no network access required for any of them:

- **`tests/test_mapper.py`** — pure unit tests of `iso19139.py`: correct ISO element structure
  and order, bbox-from-`bbox`-field vs bbox-derived-from-`geometry`, the 6-value-bbox case
  (elevation present), missing optional fields are *omitted* (not defaulted), open-ended
  collection time gets `indeterminatePosition="now"`, links are built correctly, the
  contact-vs-provider distinction holds, and XML special characters are escaped properly.
- **`tests/test_client.py`** — `BhoonidhiClient` against a local mock HTTP server standing in
  for Bhoonidhi: POST-based paging, GET-`next`-with-`merge` paging, `max_items` cutoff, `429`
  back-off, token refresh on expiry using the refresh token (not a new login), **exactly one**
  token request across a whole run, and that a bad password raises `AuthError`.
- **`tests/test_integration.py`** — the real end-to-end path: fixtures → ISO XML → pycsw →
  actual CSW HTTP requests via OWSLib. Confirms all expected operations are advertised, all 24
  fixture records (4 collections + 20 items) are present, keyword search and paging work,
  `GetRecordById` returns full ISO with the parent link and a download link, a malformed
  request returns a proper `ows:ExceptionReport`, and — the strongest check in the suite —
  **bbox query results are compared against an independent Shapely intersection** computed
  directly from the fixture geometries, for four query boxes including one over the Pacific
  that must return nothing. This guards against the CSW spatial filter silently matching the
  wrong records (axis order bugs being the classic failure mode here — see the EPSG:4326
  axis-order note in the README: with `urn:ogc:def:crs:EPSG::4326`, a bbox is **lat,lon**, i.e.
  `miny,minx,maxy,maxx`, the opposite of the more common lon,lat convention).

**A process lesson worth keeping:** an early integration test run reported 28/28 passing, but
it turned out several integration tests were silently hitting a pycsw instance left running on
`:8000` from earlier manual testing, rather than the ephemeral server the test fixture was
supposed to spin up — so the tests were "passing" without actually proving the code under test
worked in isolation. This was caught by re-running with nothing listening on `:8000` (which
then correctly failed), fixed by having the test fixture advertise its own dynamically-bound
URL rather than assuming a fixed port, and re-verified with a **decoy** process bound to `:8000`
that answers every request with HTTP 500 — the suite still passes, proving the tests are now
really talking to their own isolated server instance and not whatever happens to be on that
port. General lesson: a passing integration test suite is not proof of isolation; prove
isolation adversarially (make the wrong target fail loudly) rather than assuming it.

`scripts/smoke_test.py <url>` is a separate, deliberately non-pytest script: it runs the same
six checks (`GetCapabilities`, `DescribeRecord`, `GetRecords` all/keyword/bbox, `GetRecordById`,
an invalid-request exception check) against *any* live URL, so it can be pointed at the actual
demo endpoint moments before presenting, as a final live sanity check distinct from the offline
test suite.

---

## 5. Environment notes (things that will bite again on a fresh machine)

- **`pycsw` needs `setuptools<81`.** pycsw 2.6.2 does `import pkg_resources`, which newer
  `setuptools` (≥81 on this machine, installed by default in a fresh venv) no longer ships —
  `pkg_resources` was split out and is being removed. Symptom:
  `ModuleNotFoundError: No module named 'pkg_resources'` the moment anything imports `pycsw`.
  Fixed by pinning `setuptools<81` in `pyproject.toml`'s `server` extra. If this breaks again on
  a future machine, it means `pkg_resources` has been removed entirely and either pycsw needs
  upgrading or `setuptools` needs an even older pin.
- **Requires Python 3.10–3.12**, not whatever `python3` happens to resolve to. This machine's
  default `python3` was 3.14, which is too new for the pinned `sqlalchemy<2` /
  `pycsw==2.6.2` stack; `python3.12 -m venv .venv` was used explicitly instead.
- `gzip_compresslevel` must stay unset in `config/pycsw.cfg` — see §3 above.
- Credentials are **only** read from `BHOONIDHI_USER`/`BHOONIDHI_PASSWORD` environment
  variables, never accepted as CLI arguments — so they never land in shell history or `ps`
  output.

---

## 6a. What live verification against the real API found

Section 4B was run for real afterward, with real Bhoonidhi credentials, against `CartoSat-1_PAN_CartoDEM_30m`
(a static DEM product) and `EOS-06_OCM-GAC_L2C-CHL` (a dated ocean-colour product). Two things worth knowing:

- **`collections()` and `search()` both work end-to-end against production**, including real paging and
  real auth — the client built from the spec alone needed no changes to talk to the real server.
- **One real bug surfaced and was fixed.** `GET /data/collections` confirmed the actual catalogue uses
  real ids like `EOS-06_OCM-GAC_L2C-CHL`, not the invented `EOS-06_OCM-GAC_Chlorophyll_8day_4km` the README
  had used as an example (now corrected). More importantly, a real item's own `bbox` field turned out to
  carry `[west, north, east, south]` — the opposite of the STAC-standard `[west, south, east, north]`.
  This was only caught because the item's `geometry` field *disagreed* with its `bbox` field's apparent
  latitude order: cross-checking one against the other is what exposed it, not inspection of either field
  alone. `bbox_of()` and `_collection_bbox()` in `iso19139.py` now resolve south/north by `min`/`max` rather
  than trusting bbox field position — had this gone unfixed, any item with this shape would have written an
  inverted `EX_GeographicBoundingBox` into its ISO record, silently breaking CSW `BBOX` spatial queries for
  that record. A regression test (`test_bbox_with_south_and_north_swapped_is_normalised` in
  `tests/test_mapper.py`) pins the exact real-world values that exposed it.
- **Temporal mapping (`properties.datetime`) was confirmed correct** on the chlorophyll item — the earlier
  DEM item's missing dates were a real property of that product (a static elevation layer), not a mapper gap.
- **Not exhaustively checked**: only a handful of items across two of the catalogue's 62 collections were
  sampled. Harvesting more broadly before a demo is still worthwhile — the lesson from the bbox bug is that
  Bhoonidhi's real field conventions sometimes diverge from the documented STAC spec in ways only live data
  reveals, and the mapper's "read optional fields defensively" design means an unseen quirk degrades to a
  thinner record rather than a crash — but it won't necessarily surface itself the way the bbox swap did,
  which needed an independent check (geometry vs. bbox) to catch.

## 6. What's still unverified (be honest about this in the demo)

- **Demo data is synthetic.** `tests/fixtures/*.json` use real Bhoonidhi collection ids but
  invented titles/descriptions/geometries, labelled `SYNTH_`/`SYNTHETIC` throughout so they're
  never mistaken for real holdings.
- **Docker is unverified** — no Docker daemon was available in the build environment;
  `Dockerfile`/`docker-compose.yml` mirror the same commands that *were* verified as plain
  Python processes, but build it yourself before relying on it.
- **No formal ISO 19139 XSD validation** was run (schema fetch wasn't available at build time).
  Element order follows the schema and pycsw itself parses every record without error, but
  `pycsw-admin.py -c validate_xml -x record.xml -s gmd.xsd` has not been run.

---

## 7. Mapping this project back onto the rubric

| Rubric line | Where it's satisfied |
|---|---|
| Gap identification (5) | §2 above; evidence-generating script + its actual output in `docs/gap-evidence.md`, validated with a positive control, not just asserted |
| OGC compliance (10) | pycsw 2.6.2 APISO profile implements all required CSW 2.0.2 operations; `tests/test_integration.py` (via real HTTP + OWSLib) and `scripts/smoke_test.py` (against the live demo URL) both exercise them end-to-end |
| Working deployment (5) | `scripts/run_local.sh` → `gunicorn pycsw.wsgi:application`, confirmed live and answering real HTTP `GetCapabilities`/`GetRecords`/etc. requests, not just passing in-process tests |
| Deployed service URL | `http://127.0.0.1:8000/csw` (rubric explicitly allows localhost) |
| Code repository | `github.com/ujjuNotGujju/stac2csw` — code, harvester, mapper, tests, data-prep scripts, README |
| README.md | gap, design (with diagram), implementation choices, known limitations — all present |
| Live demo | `scripts/smoke_test.py http://127.0.0.1:8000/csw` or any `curl`/QGIS MetaSearch/OWSLib session against the running endpoint |
