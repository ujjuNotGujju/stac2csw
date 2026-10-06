"""End-to-end: fixtures -> ISO 19139 -> pycsw repository -> real CSW 2.0.2 requests.

The bbox test compares pycsw's answer with an independent shapely computation, so it
checks that the harvested extents are actually correct, not only that a query returns rows.
"""
import json
import os
import re
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

pytest.importorskip("pycsw")
owslib_csw = pytest.importorskip("owslib.csw")
from owslib.fes import And, BBox, PropertyIsLike  # noqa: E402
from shapely.geometry import box, shape  # noqa: E402

from conftest import FIXTURES, ROOT  # noqa: E402
from stac2csw import cli  # noqa: E402

CRS = "urn:ogc:def:crs:EPSG::4326"


class Quiet(WSGIRequestHandler):
    def log_message(self, *a): ...


@pytest.fixture(scope="module")
def endpoint(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("csw")
    cfg = (ROOT / "config" / "pycsw.cfg").read_text().replace("sqlite:///data/records.db", f"sqlite:////{tmp}/records.db")
    cfg_path = tmp / "pycsw.cfg"
    cfg_path.write_text(cfg)
    recs = tmp / "records"
    assert cli.main(["harvest", "--offline", str(FIXTURES), "--out", str(recs), "--clean"]) == 0
    assert cli.main(["load", "--config", str(cfg_path), "--records", str(recs), "--reset"]) == 0
    os.environ["PYCSW_CONFIG"] = str(cfg_path)
    os.environ["PYCSW_ROOT"] = str(tmp)
    from pycsw.wsgi import application
    srv = make_server("127.0.0.1", 0, application, handler_class=Quiet)
    url = f"http://127.0.0.1:{srv.server_port}/"
    # OWSLib follows the operation URLs advertised in GetCapabilities. Advertise THIS
    # server, otherwise requests would leak to whatever listens on the configured port
    # (e.g. a dev server on :8000) and the tests would not be isolated.
    cfg_path.write_text(cfg_path.read_text().replace("url=http://localhost:8000/csw", f"url={url}"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield url
    srv.shutdown()


def truth(items, colls, west, south, east, north):
    q = box(west, south, east, north)
    ids = {f"{i['collection']}:{i['id']}" for i in items if shape(i["geometry"]).intersects(q)}
    ids |= {c["id"] for c in colls if box(*c["extent"]["spatial"]["bbox"][0]).intersects(q)}
    return ids


def test_capabilities_lists_the_csw_operations(endpoint):
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    ops = {o.name for o in c.operations}
    assert {"GetCapabilities", "DescribeRecord", "GetRecords", "GetRecordById"} <= ops
    assert c.identification.type == "CSW"


def test_describe_record_returns_schema(endpoint):
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    c.describerecord(typename="csw:Record")
    assert "csw:Record" in c.response.decode() or "Record" in c.response.decode()


def test_all_records_harvested(endpoint):
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    c.getrecords2(esn="brief", maxrecords=1)
    assert c.results["matches"] == 24   # 4 collections + 20 items


def test_keyword_search(endpoint):
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    c.getrecords2(constraints=[PropertyIsLike("csw:AnyText", "%CartoDEM%")], esn="brief", maxrecords=50)
    assert c.results["matches"] == 6 and all("CartoSat" in k for k in c.records)


@pytest.mark.parametrize("bb", [(70, 10, 80, 20), (60, -5, 100, 25), (-170, -80, -150, -60), (90, 30, 98, 37)])
def test_bbox_search_matches_independent_geometry(endpoint, bb):
    items = json.loads((FIXTURES / "items.json").read_text())["features"]
    colls = json.loads((FIXTURES / "collections.json").read_text())["collections"]
    w, s, e, n = bb
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    c.getrecords2(constraints=[BBox([s, w, n, e], crs=CRS)], esn="brief", maxrecords=100)   # EPSG:4326 axis order: lat,lon
    assert set(c.records) == truth(items, colls, w, s, e, n)


def test_combined_keyword_and_bbox(endpoint):
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    c.getrecords2(constraints=[And([PropertyIsLike("csw:AnyText", "%AIS%"), BBox([10, 70, 20, 80], crs=CRS)])], esn="brief", maxrecords=50)
    assert c.results["matches"] > 0 and all("Novasar" in k for k in c.records)


def test_paging(endpoint):
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    seen = set()
    start = 1
    while True:
        c.getrecords2(esn="brief", startposition=start, maxrecords=10)
        seen |= set(c.records)
        if c.results["nextrecord"] in (0, None) or c.results["nextrecord"] > c.results["matches"]:
            break
        start = c.results["nextrecord"]
    assert len(seen) == 24


def test_get_record_by_id_returns_iso_with_parent_and_links(endpoint):
    c = owslib_csw.CatalogueServiceWeb(endpoint, version="2.0.2")
    rid = "CartoSat-1_PAN_CartoDEM_30m:SYNTH_CartoSat-1_001"
    c.getrecordbyid(id=[rid], outputschema="http://www.isotc211.org/2005/gmd")
    r = c.records[rid]
    assert r.parentidentifier == "CartoSat-1_PAN_CartoDEM_30m"
    assert r.hierarchy == "dataset"
    assert any("/download?id=" in (o.url or "") for o in r.distribution.online)


def test_invalid_requests_return_ows_exceptions(endpoint):
    import requests
    r = requests.get(endpoint, params={"service": "CSW", "version": "2.0.2", "request": "GetRecordById", "id": "does-not-exist"}, timeout=20)
    assert r.status_code in (200, 400, 404)
    bad = requests.get(endpoint, params={"service": "CSW", "version": "2.0.2", "request": "Nope"}, timeout=20)
    assert "ExceptionReport" in bad.text and re.search(r'exceptionCode="(InvalidParameterValue|OperationNotSupported)"', bad.text)
