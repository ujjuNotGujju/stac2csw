# Technical discussion

Every non-obvious technical decision in `stac2csw`, recorded as **Decision → Alternatives
considered → Why this one → Trade-offs accepted**, plus the evidence that each decision held up
once tested against the real Bhoonidhi API. This is the companion document to `README.md`
(which states the decisions) and `EXPLANATION.md` (which narrates the project end to end) —
this document is the argument for each decision on its own.

---

## 1. Gap-verification methodology

**Decision:** write a script (`scripts/probe_gap.py`) that sends live `GetCapabilities`
requests for five OGC service types across every conventional service path to both Bhoonidhi
hosts, accepts a hit only if the response body is a genuine capabilities XML document for that
service (regex-matched on the namespace/root element), and validate the detector itself with a
positive control before trusting any negative result.

**Alternatives considered:**
- *Just assert the gap from reading the API docs.* Faster, but the brief explicitly requires
  verification that the service doesn't already exist, and a documentation gap is weaker
  evidence than a live network test — docs can be outdated or incomplete in either direction.
- *Check HTTP status code only (200 = found, else not found).* Rejected: many servers return
  `200` for *any* path, including unknown ones, if they serve a single-page app with a catch-all
  route. A naive status check would have produced a false "CSW exists" on exactly this kind of
  server.
- *Check a single well-known path only (e.g. `/csw`).* Rejected: CSW/pycsw/GeoServer/GeoNetwork
  deployments conventionally differ in mount path; checking one path risks a false negative.

**Why this one:** body-content matching plus a positive control is the only version of this
check that can't be fooled by either failure mode (false positive from a lenient status check,
false negative from checking the wrong path). The positive control is what makes the negative
result trustworthy — a detector that says "no" everywhere, including places it should say "yes,"
proves nothing.

**Trade-offs accepted:** the probe still cannot prove *global* absence — a CSW could exist on an
unprobed path or a non-public host. The README and `docs/gap-evidence.md` state this limit
explicitly rather than overclaiming. The "limit stated honestly" is itself treated as part of the
evidence, not a weakness to hide.

**Evidence:** `docs/gap-evidence.md`, generated 2026-10-12 — zero real OGC capabilities documents
found on either Bhoonidhi host across 10 paths × 5 service types; positive control against this
project's own CSW correctly returned `YES` for CSW and `no` for the other four.

---

## 2. Service framework: pycsw, not a hand-rolled CSW server

**Decision:** implement the CSW interface with **pycsw 2.6.2** (APISO profile), not a custom
HTTP handler implementing the CSW wire protocol directly.

**Alternatives considered:**
- *Hand-rolled CSW server.* Full control over every XML byte, but the assignment's marking
  explicitly weights "does the service correctly implement the standard's operations" at 10/20 —
  the highest-value criterion. Re-implementing `GetRecords` paging, CQL/OGC Filter parsing, and
  exception reporting from scratch is where a hand-rolled project is most likely to have subtle
  protocol bugs, and that effort would not demonstrate anything the assignment is testing for.
- *GeoServer's CSW extension.* Also a valid "standard framework" per the brief, but it's a much
  heavier Java stack to run and configure for a catalogue-only use case, and its CSW support is
  secondary to its primary WMS/WFS role.
- *GeoNetwork.* A full catalogue *application* (UI, user management, harvesting scheduler) —
  more than this project needs; its CSW server is one component of something much larger to
  deploy and operate.

**Why this one:** pycsw is lightweight, Python-native (so it shares a language and dependency
story with the harvester/mapper), is the reference CSW implementation referenced in OGC's own
conformance testing, and lets effort concentrate on the part of this project that is actually
novel — the STAC→ISO mapping — rather than re-proving a solved protocol problem.

**Trade-offs accepted:** pycsw 2.6.2 pins old dependency versions (`sqlalchemy<2`, Python ≤3.12)
and carries real operational quirks that had to be discovered and worked around (§7, §8 below) —
buying correctness on the protocol in exchange for inheriting someone else's dependency and
compatibility surface.

---

## 3. Output encoding: ISO 19139, not ISO 19115-3 or Dublin Core

**Decision:** map every record to ISO 19139 (the XML encoding of ISO 19115:2003), with Dublin
Core available as pycsw's built-in secondary `outputSchema` for `GetRecords`.

**Alternatives considered:**
- *ISO 19115-3 (the newer XML encoding, ISO 19115-1:2014/19115-3:2016).* More current as a
  standard, but pycsw's APISO query/index profile is built around 19139's queryables and element
  structure, and this is also what QGIS MetaSearch and GeoNetwork harvesting expect by default —
  choosing the newer standard would have meant fighting the server and the client tooling both,
  for a gain (standard currency) the demo doesn't need.
- *Dublin Core only.* Simpler (15 flat elements), already pycsw's default `csw:Record` schema,
  but far too thin to carry a bounding box, temporal extent, or lineage statement with any
  structure — would have made the "service design/implementation choices" half of the grading
  criteria much weaker, since there'd be little mapping work to defend.

**Why this one:** ISO 19139 is what the SDI ecosystem this project is arguing Bhoonidhi should
join — QGIS, GeoNetwork, INSPIRE-style infrastructure — actually consumes by default; choosing
anything else would have undercut the interoperability argument the whole gap identification
rests on.

**Trade-offs accepted:** records state `metadataStandardVersion = 2003/Cor 1:2006`, an older
corrigendum, by design — not validated against the formal ISO XSD in this build environment
(schema fetch unavailable), stated as a known limitation rather than silently assumed correct.

---

## 4. Architecture: batch harvest-then-serve, never live-proxy

**Decision:** `stac2csw harvest` reads the whole target slice of the STAC API once and writes
ISO XML to disk; `stac2csw load` bulk-inserts it into pycsw's own repository; the CSW HTTP
endpoint only ever reads from that local repository and never calls Bhoonidhi per-request.

**Alternatives considered:**
- *Live proxy — translate each incoming CSW request into one or more Bhoonidhi STAC calls on
  the fly.* Would keep the catalogue perfectly fresh with no separate harvest step, but breaks
  against two hard constraints: Bhoonidhi's token endpoint is capped at 20 requests/hour/IP and
  search at 3 requests/second/IP, both far below what a CSW server under any real multi-client
  load (QGIS, GeoNetwork, scripted queries all polling) would need; and a CSW client has no
  protocol-level way to supply Bhoonidhi login credentials, so a live proxy would need the
  server operator's credentials embedded for every request anyway — no cleaner than harvesting
  with those same credentials up front.
- *Hybrid: cache with TTL, re-fetch on miss.* Reduces staleness vs. pure batch, but reintroduces
  the same rate-limit exposure under concurrent client load, just probabilistically rather than
  deterministically — worse for reasoning about correctness under the stated API limits.

**Why this one:** the only design that structurally cannot violate Bhoonidhi's documented rate
limits regardless of CSW client load, and the only one where CSW clients get fast, deterministic
answers independent of whether Bhoonidhi itself is up.

**Trade-offs accepted:** catalogue freshness equals time-of-last-harvest; no incremental update
mechanism exists yet (a full `--clean` harvest is the only refresh path). Stated explicitly in
the README's limitations rather than left implicit.

---

## 5. Repository backend: SQLite, with a stated Postgres upgrade path

**Decision:** `config/pycsw.cfg` points at a local SQLite file by default.

**Alternatives considered:** PostgreSQL/PostGIS from the start — pycsw supports it natively, and
it's the production-appropriate choice at scale.

**Why SQLite for this project:** zero external service dependency for grading/demo — the
database is a single file created by `stac2csw load`, nothing to provision or keep running
separately. For the demo's data volume (dozens to low hundreds of records), SQLite's spatial
query performance is not a bottleneck.

**Trade-offs accepted, stated explicitly:** SQLite is fine for tens of thousands of records; a
production deployment serving the full 62-collection catalogue at scale should point `[repository]
database` at PostgreSQL instead — a one-line config change, not a code change, since pycsw
abstracts the backend via SQLAlchemy.

---

## 6. Record hierarchy: collections as `series`, items as `dataset`

**Decision:** every STAC Collection becomes an ISO `MD_Metadata` with `hierarchyLevel=series`;
every STAC Item becomes one with `hierarchyLevel=dataset` and `parentIdentifier` pointing at its
collection's `fileIdentifier`.

**Alternatives considered:** flatten everything to `dataset`-level records only, ignoring STAC's
own Collection/Item distinction.

**Why this one:** ISO 19115's `series`/`dataset` scope codes exist precisely to express this
"collection of related products" relationship, and STAC already carries that structure — mapping
it through rather than discarding it means a CSW client can browse top-down (search for a
collection, follow to its items) or search flat across all items, matching how STAC-native tools
already let users navigate Bhoonidhi today. Discarding it would have been strictly a loss of
information already present in the source.

**Trade-offs accepted:** none significant — this added one extra element (`parentIdentifier`)
and one extra record type per harvest run, well within the existing mapping effort.

---

## 7. CSW-Transaction / Harvest: implemented by pycsw, deliberately disabled

**Decision:** `[manager] transactions=false` in `config/pycsw.cfg`; the only way to update the
catalogue is re-running `stac2csw harvest` + `load` under operator control.

**Alternatives considered:** enable CSW-T so remote catalogues could push records directly, or
enable pycsw's `Harvest` operation so pycsw itself could pull from other CSW endpoints.

**Why disabled:** this catalogue has exactly one trusted source of truth (Bhoonidhi, pulled by
this project's own harvester) and no requirement for third parties to write into it. Enabling
CSW-T on a publicly reachable endpoint would let any caller insert, modify, or delete catalogue
records — a write surface with no corresponding use case here, and a meaningful security/
integrity liability if this were ever exposed beyond localhost.

**Trade-offs accepted:** none — enabling it later is a one-line config change if a future use
case needs it, explicitly noted in the README so the decision is legible rather than silently
hard-coded.

---

## 8. Bhoonidhi client design: refresh-token preference, fixed throttle, retry on 429/5xx

**Decision:** `BhoonidhiClient` logs in once, prefers the refresh token over a new password
login whenever the access token expires, enforces a minimum 0.4s gap between any two requests
regardless of endpoint, and retries on `429`/`5xx` honoring `Retry-After` when present.

**Alternatives considered:**
- *Re-login on every 401* — simpler code, but burns the 20-requests/hour token budget fast
  under any harvest that runs long enough to see a token expire mid-run.
- *No throttling, rely on catching 429 reactively* — simpler, but turns the documented 3 req/s
  limit into something discovered by trial and error against the real server rather than
  respected by construction; also riskier for a government-operated API where repeated
  rate-limit violations could plausibly trigger IP-level blocking.

**Why this one:** respecting published limits *by construction* (not by reactive backoff alone)
is the only version of this client that's safe to run against the real production API without
risking account or IP action — which matters more for a project explicitly built to be run
against a real institutional service with a real user's credentials.

**Trade-offs accepted:** a harvest of the full 62-collection catalogue is throttled to roughly
2.5 requests/second effective throughput by design, which is slow for very large harvests — an
intentional ceiling, not an oversight.

**Evidence this held up under real use:** `tests/test_client.py` proves exactly **one** token
request occurs across a run that spans a token expiry (via refresh, not re-login); verified
again informally during the live harvest in this session, where login succeeded once per shell
session across multiple `harvest` invocations without hitting the 20/hour cap.

---

## 9. Defensive field reading in the mapper: "nothing is invented"

**Decision:** every optional STAC field is read defensively (`.get()` with no exception path);
a missing field is omitted from the ISO record, never filled with an invented default. The only
hard-coded defaults are facts true by specification for *any* valid input (EPSG:4326 for GeoJSON
geometry, per RFC 7946) or structural metadata about the harvesting process itself (lineage
statement, metadata contact), never content about the data being described.

**Alternatives considered:** fill missing fields with placeholder text ("Unknown", "N/A") so
every ISO record has a uniform shape.

**Why this one:** a catalogue record that states something false (an invented date, a guessed
title) is worse than one that omits a field — a false record actively misleads a downstream
consumer (a researcher searching by date range, a harvester indexing by title), while an omitted
field is honestly absent and visible as such.

**Evidence this was the right call, found live:** the harvested CartoDEM item had no
`properties.datetime` at all — correctly, since it's a static elevation product, not a dated
scene. Had the mapper invented a date to keep records uniform, it would have fabricated a
false acquisition time for a borderline-undated product. The defensive design meant this came
out as a (correctly) thinner record instead of a wrong one.

---

## 10. The bbox south/north bug: why cross-checking against geometry, not just the field, mattered

**Decision:** `bbox_of()` resolves `south`/`north` by `min()`/`max()` of the two latitude
candidates in the `bbox` field, never by trusting their field position.

**Context:** a live item from `EOS-06_OCM-GAC_L2C-CHL` has `bbox = [west, 8.08, east, -7.68]`.
By the STAC/GeoJSON-standard field order `[west, south, east, north]`, that would mean
`south=8.08, north=-7.68` — south north of north, an invalid bounding box. Cross-referencing
the same item's `geometry` (whose coordinates give unambiguous min/max latitude) showed the
*true* south/north were the reverse of what field position implied: Bhoonidhi's own `bbox`
field is ordered `[west, north, east, south]`.

**Why this matters beyond one field:** this is evidence for a broader principle applied
throughout the mapper — *when a value can be derived two ways from the same record (declared
field vs. computed from geometry), checking them against each other is a stronger validation
than trusting either alone.* `bbox_of()` already had this fallback structure (prefer `bbox`,
fall back to deriving from `geometry` if `bbox` is absent); what changed is that south/north are
now resolved by `min`/`max` of the given pair rather than by position, so the fix degrades
gracefully regardless of which convention a given provider or endpoint uses, instead of only
fixing the one observed case.

**Alternatives considered:** always ignore the `bbox` field and derive strictly from `geometry`.
Rejected — not every item in this project's offline synthetic fixtures (or hypothetically, every
real item) carries a `geometry`, and the `bbox` field is cheaper to use when it's trustworthy;
throwing it away entirely would lose a fast path for the common, non-buggy case.

**Evidence:** `tests/test_mapper.py::test_bbox_with_south_and_north_swapped_is_normalised` pins
the exact real-world values that exposed this, so a regression can't silently reappear.

**Why this is the single strongest piece of evidence for the testing strategy as a whole:** it
is a bug that *only* live data against the real API could have found — no amount of synthetic
fixture testing would have caught a provider convention violating its own documented spec,
because the synthetic fixtures were (reasonably) built to follow the spec. This is argued
explicitly in `docs/EXPLANATION.md` §6a as the main justification for why §4B (the live
verification step) was worth doing even though it wasn't strictly required to pass the offline
test suite.

---

## 11. Dependency pinning and the environment bugs they were pinned around

**Decisions, each pinned for a specific observed failure, not speculatively:**

| Pin | Failure it prevents |
|---|---|
| `pycsw==2.6.2` | the specific version whose APISO profile and config format this project is built against |
| `OWSLib<0.29` | pycsw 2.6.2 compatibility (observed in pycsw's own dependency constraints) |
| `sqlalchemy<2` | pycsw 2.x requires SQLAlchemy's 1.x API |
| `setuptools<81` | newer `setuptools` dropped `pkg_resources`, which pycsw 2.6.2 still imports at module load — found during this session's install: `ModuleNotFoundError: No module named 'pkg_resources'` on a bare `pip install` with a fresh `setuptools` |
| Python 3.10–3.12 (not whatever `python3` resolves to) | this machine's default `python3` was 3.14, incompatible with the `sqlalchemy<2` pin; `python3.12 -m venv` was used explicitly |
| `gzip_compresslevel` left **unset** in `config/pycsw.cfg` | pycsw 2.6.2's WSGI wrapper gzips the response body but reports the *uncompressed* Content-Length when this is set, which OWSLib and QGIS both reject as `IncompleteRead` — found by every CSW request silently failing client-side despite the server returning 200 |

**Why record this as a technical decision rather than just a changelog line:** each of these
pins exists because of a specific, reproduced failure, not because "pinning is good practice" in
the abstract — the table is the argument for why each pin is necessary, not just present, so a
future maintainer knows which pins are load-bearing and shouldn't be "cleaned up."

---

## 12. Credential handling: environment variables only, never CLI arguments

**Decision:** `BhoonidhiClient` only accepts `user`/`password` as constructor arguments; the CLI
(`cli.py`) reads them exclusively from `BHOONIDHI_USER`/`BHOONIDHI_PASSWORD` environment
variables, never as `argparse` flags.

**Why this one:** command-line arguments are visible in shell history and in `ps` output to any
other process on the same machine; environment variables set via `export` (or better, `read -s`
piped into `export`, which avoids even shell-history exposure) are not. This is a real-world,
not academic, concern for this project specifically, since it's designed to be run with a real
user's real institutional credentials, not just test data.

**Trade-offs accepted:** slightly more setup friction (two `export` lines instead of one `--user
foo --password bar` flag) — judged clearly worth it for this exact failure mode.

---

## 13. Testing strategy: three layers, plus an isolation fix found the hard way

**Decision:** unit tests (pure mapper logic, no I/O), client tests (against a local mock HTTP
server, no real network), and integration tests (fixtures → ISO → pycsw → real CSW HTTP via
OWSLib, including an independent Shapely cross-check of bbox query results) — kept strictly
separate from `scripts/smoke_test.py`, which is explicitly *not* part of the pytest suite because
it targets a live, externally-running endpoint rather than a disposable test fixture.

**A real process failure, corrected mid-project:** an early integration-test run reported 28/28
passing, but several of those tests were — unintentionally — hitting a pycsw instance left
running on `:8000` from earlier manual testing, rather than the ephemeral server the test
fixtures were supposed to start. The fix wasn't just "stop the stray process" — it was having the
test fixture advertise its own dynamically-bound URL rather than assuming a fixed port, so the
tests structurally cannot talk to the wrong server. This was then verified *adversarially*: a
decoy process was bound to `:8000` that answers every request with HTTP 500, and the suite was
re-run to confirm it still passes 28/28 — proof the tests are isolated, not just an assumption.

**Why this is a technical decision worth recording, not just a bug fix:** the general principle
— *a passing test suite is not proof of isolation; isolation has to be demonstrated
adversarially, by making the wrong target fail loudly and confirming the suite still passes* —
was applied nowhere else in this project only because nowhere else had an equivalent shared-state
risk. It's recorded here as the reason the test harness looks the way it does (dynamic port
binding in `tests/conftest.py`), not just as a historical incident.

---

## 14. Deployment target: localhost, by informed choice, not by default

**Decision:** the demo runs on `http://127.0.0.1:8000/csw`; no public hosting was pursued.

**Alternatives considered:** a temporary public tunnel (ngrok/cloudflared) for remote
reachability during the demo, or a persistent cloud deployment (VM/PaaS).

**Why localhost:** the assignment's own deliverables table states "publicly accessible host
**or localhost**" — localhost already satisfies the literal requirement. Beyond that, publishing
a copy of Bhoonidhi's catalogue metadata publicly raises a terms-of-use question this project
doesn't need to resolve to meet the brief: Bhoonidhi's terms govern data obtained through a
registered account, and the README states this should be checked before any public hosting is
considered. Localhost sidesteps that question entirely rather than resolving it by assumption.

**Trade-offs accepted:** the demo can only be shown live on the machine running it, not
accessed independently by a grader after the fact — acceptable given the brief's own wording and
the terms-of-use ambiguity a public deployment would introduce.
