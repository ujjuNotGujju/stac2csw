#!/usr/bin/env python3
"""Exercise every CSW operation against ANY endpoint and print PASS/FAIL.

    python scripts/smoke_test.py http://localhost:8000/csw
Use it right before the demo to prove the service is live and correct.
"""
import sys

import requests
from owslib.csw import CatalogueServiceWeb
from owslib.fes import BBox, PropertyIsLike

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/csw"
results = []


def check(name, fn):
    try:
        detail = fn()
        results.append(True)
        print(f"PASS  {name}: {detail}")
    except Exception as e:  # noqa: BLE001
        results.append(False)
        print(f"FAIL  {name}: {type(e).__name__}: {e}")


c = CatalogueServiceWeb(url, version="2.0.2")
check("GetCapabilities", lambda: f"{c.identification.title!r}; ops={sorted(o.name for o in c.operations)}")


def describe():
    c.describerecord(typename="csw:Record")
    assert b"Record" in c.response
    return f"{len(c.response)} bytes of schema"


check("DescribeRecord", describe)


def count():
    c.getrecords2(esn="brief", maxrecords=1)
    return f"{c.results['matches']} records"


check("GetRecords (all)", count)


def kw():
    c.getrecords2(constraints=[PropertyIsLike("csw:AnyText", "%EOS-06%")], esn="brief", maxrecords=5)
    assert c.results["matches"] > 0
    return f"{c.results['matches']} matches for '%EOS-06%'"


check("GetRecords (keyword)", kw)


def bbox():
    c.getrecords2(constraints=[BBox([10, 70, 20, 80], crs="urn:ogc:def:crs:EPSG::4326")], esn="brief", maxrecords=5)
    return f"{c.results['matches']} matches for bbox 70-80E,10-20N"


check("GetRecords (bbox)", bbox)


def byid():
    c.getrecords2(esn="brief", maxrecords=1)
    rid = next(iter(c.records))
    c.getrecordbyid(id=[rid], outputschema="http://www.isotc211.org/2005/gmd")
    r = c.records[rid]
    return f"{rid} -> ISO 19139, hierarchy={r.hierarchy}"


check("GetRecordById (ISO 19139)", byid)


def bad():
    t = requests.get(url, params={"service": "CSW", "version": "2.0.2", "request": "Nope"}, timeout=20).text
    assert "ExceptionReport" in t
    return "OWS ExceptionReport returned"


check("Invalid request -> exception", bad)
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
