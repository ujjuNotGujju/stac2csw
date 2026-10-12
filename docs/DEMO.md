# Live demo script

How to present `stac2csw` live, end to end, in roughly 5–8 minutes. Written to be followed
cold — read it once before the real demo, then use it as a cue sheet during.

---

## Before anyone's watching: setup

```bash
cd stac2csw
source .venv/bin/activate
scripts/run_local.sh &
```

Leave that terminal tab open and visible for the whole demo — it's the running server. Open a
**second terminal tab** for everything you'll actually type while presenting, and have a browser
tab ready.

Sanity-check the server is actually up before anyone's watching:

```bash
python3 scripts/smoke_test.py http://127.0.0.1:8000/csw
```

`7/7 checks passed` means you're ready. If anything fails here, fix it before the demo starts —
see "If something breaks" at the end of this document.

---

## The walkthrough, in order

### 1. State the gap (≈30 seconds)

Say it plainly: *"Bhoonidhi only exposes a STAC API behind a login — no CSW, no ISO 19115
metadata. Standard GIS tooling like QGIS and GeoNetwork can't discover it."*

Back it with evidence, not just the claim — open `docs/gap-evidence.md` or state the headline:
zero real OGC capabilities documents found across both Bhoonidhi hosts and every conventional
service path, and the detector itself was validated with a positive control (pointed at this
project's own CSW, it correctly says yes) before trusting the negative result elsewhere.

### 2. Browser — raw proof it's a real CSW (≈30 seconds)

Paste into the address bar:

```
http://127.0.0.1:8000/csw?service=CSW&version=2.0.2&request=GetCapabilities
```

Raw XML renders in the browser. Point at `ows:ServiceIdentification` and the advertised
operations list (`GetCapabilities`, `DescribeRecord`, `GetRecords`, `GetRecordById`, …) — this
is the server stating, in a standard format, what it can do.

### 3. Terminal — walk the operations with curl (≈2–3 minutes)

Use `scripts/demo_requests.sh` as your script rather than typing from memory. In order:

- **`DescribeRecord`** — the record schema:
  ```bash
  curl "http://localhost:8000/csw?service=CSW&version=2.0.2&request=DescribeRecord&typeName=csw:Record"
  ```
- **`GetRecords`, keyword filter** — show it return real harvested records:
  ```bash
  curl -G "http://localhost:8000/csw" --data-urlencode service=CSW --data-urlencode version=2.0.2 \
    --data-urlencode request=GetRecords --data-urlencode typeNames=csw:Record \
    --data-urlencode resultType=results --data-urlencode elementSetName=summary \
    --data-urlencode constraintLanguage=CQL_TEXT \
    --data-urlencode "constraint=csw:AnyText like '%EOS-06%'" --data-urlencode maxRecords=10
  ```
- **`GetRecordById`, full ISO output** — one complete record, point at `fileIdentifier` and
  `EX_GeographicBoundingBox`:
  ```bash
  curl -G "http://localhost:8000/csw" --data-urlencode service=CSW --data-urlencode version=2.0.2 \
    --data-urlencode request=GetRecordById \
    --data-urlencode "id=CartoSat-1_PAN_CartoDEM_30m" \
    --data-urlencode outputSchema=http://www.isotc211.org/2005/gmd --data-urlencode elementSetName=full
  ```

If you want a spatial query too, `scripts/demo_requests.sh` has the OGC Filter XML `POST`
examples for bbox search — worth including if time allows, since spatial filtering is a core
CSW capability and the one with the strongest independent verification (bbox results are
checked against an independent Shapely calculation in the test suite).

### 4. QGIS MetaSearch — the payoff visual (≈1–2 minutes)

This is the strongest single moment in the demo — it's the literal proof that a tool which
couldn't see Bhoonidhi before, now can, with zero custom code written against Bhoonidhi's API.

Web ▸ MetaSearch ▸ Services ▸ New ▸ URL `http://localhost:8000/csw` ▸ Search.

Records appear with titles, abstracts, and bounding-box previews drawn directly on the map
canvas. Click one to show the full metadata panel.

### 5. Close with the smoke test (≈30 seconds)

```bash
python3 scripts/smoke_test.py http://127.0.0.1:8000/csw
```

`7/7 checks passed` as your closing line — it compresses "every required operation works,
right now, against this exact running server" into one visible result.

---

## If you have more time

- Show a real harvested record's lineage statement (`gmd:dataQualityInfo/.../gmd:statement`) —
  it states exactly when and from where the record was harvested, which is good evidence the
  data on screen is real, not fabricated for the demo.
- Mention the bbox bug found during live verification (§10 of `docs/TECHNICAL_DISCUSSION.md`) —
  a concrete example of the project being tested against reality, not just against itself.

## Likely questions, and where the answer lives

| Question | Answer location |
|---|---|
| "How do you know the gap is real?" | `docs/gap-evidence.md`; the positive-control methodology |
| "Why pycsw instead of building it yourself?" | `docs/TECHNICAL_DISCUSSION.md` §2 |
| "Is this tested against the real API, or just mocked?" | `docs/EXPLANATION.md` §6a — including a real bug found and fixed on live data |
| "What isn't done / what are the limitations?" | README §8 — volunteer this before being asked |
| "Why localhost and not a public URL?" | `docs/TECHNICAL_DISCUSSION.md` §14 — the assignment explicitly allows localhost, and public hosting raises a Bhoonidhi terms-of-use question this project doesn't need to resolve |

---

## If something breaks mid-demo

- **Server not responding** — check it's still running: `ps aux | grep gunicorn`. Restart with
  `scripts/run_local.sh &` from the repo root with the venv active.
- **`curl` returns an exception report instead of results** — check the typo in the query first;
  pycsw's `ows:ExceptionReport` output (seen correctly in the smoke test's invalid-request check)
  is itself a sign the server is working correctly, just rejecting a malformed request.
- **QGIS MetaSearch shows no records** — confirm the URL is exactly `http://localhost:8000/csw`
  (not `127.0.0.1`, if QGIS's own network stack treats them differently) and that the server
  responded to the browser check in step 2 first.
- **Nothing in the above works** — fall back to the smoke test output already captured before
  the demo; narrate from it rather than losing the room to a live debugging session.
