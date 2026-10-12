import json

from lxml import etree

from conftest import FIXTURES
from stac2csw import iso19139 as iso

NS = iso.NS
X = lambda root, path: root.xpath(path, namespaces=NS)
ITEMS = json.loads((FIXTURES / "items.json").read_text())["features"]
COLLS = {c["id"]: c for c in json.loads((FIXTURES / "collections.json").read_text())["collections"]}


def parse(b):
    return etree.fromstring(b)


def test_item_record_core_fields():
    it = ITEMS[0]
    r = parse(iso.item_record(it, COLLS[it["collection"]], "https://api.example"))
    assert X(r, "string(gmd:fileIdentifier/gco:CharacterString)") == f"{it['collection']}:{it['id']}"
    assert X(r, "string(gmd:parentIdentifier/gco:CharacterString)") == it["collection"]
    assert X(r, "string(gmd:hierarchyLevel/gmd:MD_ScopeCode/@codeListValue)") == "dataset"
    assert X(r, "string(gmd:metadataStandardName/gco:CharacterString)") == "ISO 19115"


def test_bbox_axis_order_is_west_east_south_north():
    it = {"id": "a", "collection": "c", "bbox": [70.0, 10.0, 80.0, 20.0], "properties": {"datetime": "2023-01-01T00:00:00Z"}}
    r = parse(iso.item_record(it, None, "https://api.example"))
    g = "gmd:identificationInfo/gmd:MD_DataIdentification/gmd:extent/gmd:EX_Extent/gmd:geographicElement/gmd:EX_GeographicBoundingBox/"
    assert float(X(r, f"string({g}gmd:westBoundLongitude/gco:Decimal)")) == 70.0
    assert float(X(r, f"string({g}gmd:eastBoundLongitude/gco:Decimal)")) == 80.0
    assert float(X(r, f"string({g}gmd:southBoundLatitude/gco:Decimal)")) == 10.0
    assert float(X(r, f"string({g}gmd:northBoundLatitude/gco:Decimal)")) == 20.0


def test_bbox_derived_from_geometry_when_missing():
    it = {"id": "a", "collection": "c", "properties": {"datetime": "2023-01-01T00:00:00Z"},
          "geometry": {"type": "Polygon", "coordinates": [[[1, 2], [5, 2], [5, 9], [1, 9], [1, 2]]]}}
    assert iso.bbox_of(it) == [1, 2, 5, 9]


def test_six_value_bbox_is_reduced_to_2d():
    assert iso.bbox_of({"bbox": [1, 2, 0, 5, 9, 100]}) == [1, 2, 5, 9]


def test_bbox_with_south_and_north_swapped_is_normalised():
    # Observed on a live Bhoonidhi item (EOS-06_OCM-GAC_L2C-CHL, 2026-10-11):
    # the provider's own bbox field carries [west, north, east, south], the
    # opposite of the STAC-standard [west, south, east, north]. Confirmed by
    # comparing against the item's own geometry, whose min/max latitude
    # contradicted the bbox field's positional order.
    bb = [-134.194354, 8.082571, -117.484377, -7.675314]
    assert iso.bbox_of({"bbox": bb}) == [-134.194354, -7.675314, -117.484377, 8.082571]


def test_missing_fields_are_omitted_not_invented():
    it = {"id": "bare", "collection": "c", "properties": {}}
    r = parse(iso.item_record(it, None, "https://api.example"))
    assert not X(r, "//gmd:EX_GeographicBoundingBox")
    assert not X(r, "//gmd:EX_TemporalExtent")


def test_collection_open_ended_temporal_extent():
    c = COLLS["Novasar-1_AIS"]
    r = parse(iso.collection_record(c, "https://api.example"))
    assert X(r, "string(gmd:hierarchyLevel/gmd:MD_ScopeCode/@codeListValue)") == "series"
    assert not X(r, "gmd:parentIdentifier")
    end = X(r, "//gml:endPosition")[0]
    assert end.get("indeterminatePosition") == "now"


def test_distribution_links_present():
    it = ITEMS[0]
    r = parse(iso.item_record(it, COLLS[it["collection"]], "https://api.example"))
    urls = X(r, "//gmd:CI_OnlineResource/gmd:linkage/gmd:URL/text()")
    assert any("/download?id=" in u and f"collection={it['collection']}" in u for u in urls)


def test_metadata_contact_is_not_the_provider():
    it = ITEMS[0]
    r = parse(iso.item_record(it, None, "https://api.example"))
    meta_org = X(r, "string(gmd:contact//gmd:organisationName/gco:CharacterString)")
    data_org = X(r, "string(//gmd:MD_DataIdentification/gmd:pointOfContact//gmd:organisationName/gco:CharacterString)")
    assert "unofficial" in meta_org and "NRSC" in data_org


def test_topic_rules():
    assert iso.topic_for("CartoSat-1_PAN_CartoDEM_30m") == "elevation"
    assert iso.topic_for("EOS-06_OCM-GAC_L2C-CHL") == "oceans"
    assert iso.topic_for("Sentinel-1A_SAR-IW_GRD") == iso.DEFAULT_TOPIC


def test_special_characters_are_escaped():
    it = {"id": "x&y", "collection": "c<d>", "properties": {"title": "A & B <C>", "datetime": "2023-01-01T00:00:00Z"}}
    parse(iso.item_record(it, None, "https://api.example"))  # must remain well-formed
