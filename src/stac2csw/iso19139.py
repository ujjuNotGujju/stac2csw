"""Map STAC Collections / Items to ISO 19139 (XML encoding of ISO 19115:2003).

Design rules
* Only core STAC fields are relied on (id, geometry/bbox, properties.datetime or
  start_datetime/end_datetime, collection, links, assets, extent, title,
  description, keywords, license). Anything else is optional.
* Nothing is invented: a missing field is omitted, never filled with a guess.
  The only defaults are the stated ones (EPSG:4326 for GeoJSON geometry, per
  RFC 7946, and the provider/contact organisations).
* Collections become hierarchyLevel "series"; items become "dataset" and carry
  parentIdentifier = collection id, mirroring STAC's own hierarchy.
"""
from __future__ import annotations

from datetime import datetime, timezone

from lxml import etree

NS = {
    "gmd": "http://www.isotc211.org/2005/gmd",
    "gco": "http://www.isotc211.org/2005/gco",
    "gml": "http://www.opengis.net/gml",
    "xlink": "http://www.w3.org/1999/xlink",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
CODELIST = "http://standards.iso.org/ittf/PubliclyAvailableStandards/ISO_19139_Schemas/resources/Codelist/ML_gmxCodelists.xml"

PROVIDER = {
    "organisation": "National Remote Sensing Centre (NRSC), ISRO",
    "url": "https://bhoonidhi.nrsc.gov.in",
}

# Heuristic topic categories (ISO 19115 MD_TopicCategoryCode) by collection prefix.
TOPIC_RULES = [
    ("CartoSat-1_PAN_CartoDEM", "elevation"),
    ("Novasar-1_AIS", "transportation"),
    ("EOS-06_OCM", "oceans"),
    ("EOS-06_SCAT", "oceans"),
]
DEFAULT_TOPIC = "imageryBaseMapsEarthCover"


def _q(tag: str) -> str:
    p, t = tag.split(":")
    return f"{{{NS[p]}}}{t}"


def _el(parent, tag, text=None, **attrs):
    e = etree.SubElement(parent, _q(tag))
    for k, v in attrs.items():
        e.set(k, v)
    if text is not None:
        e.text = str(text)
    return e


def _cs(parent, tag, text):
    """<tag><gco:CharacterString>text</gco:CharacterString></tag>"""
    e = _el(parent, tag)
    _el(e, "gco:CharacterString", text)
    return e


def _code(parent, tag, list_name, value):
    e = _el(parent, tag)
    c = _el(e, f"gmd:{list_name}", value)
    c.set("codeList", f"{CODELIST}#{list_name}")
    c.set("codeListValue", value)
    return e


def _responsible_party(parent, tag, org, role, url=None, email=None):
    wrap = _el(parent, tag)
    rp = _el(wrap, "gmd:CI_ResponsibleParty")
    _cs(rp, "gmd:organisationName", org)
    if url or email:
        ci = _el(_el(rp, "gmd:contactInfo"), "gmd:CI_Contact")
        if email:
            addr = _el(_el(ci, "gmd:address"), "gmd:CI_Address")
            _cs(addr, "gmd:electronicMailAddress", email)
        if url:
            ore = _el(_el(ci, "gmd:onlineResource"), "gmd:CI_OnlineResource")
            _el(_el(ore, "gmd:linkage"), "gmd:URL", url)
    _code(rp, "gmd:role", "CI_RoleCode", role)


def topic_for(collection_id: str | None) -> str:
    for prefix, topic in TOPIC_RULES:
        if collection_id and collection_id.startswith(prefix):
            return topic
    return DEFAULT_TOPIC


# --------------------------------------------------------------- geometry
def _coords(obj):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            yield obj
        else:
            for o in obj:
                yield from _coords(o)


def bbox_of(item: dict) -> list[float] | None:
    """[west, south, east, north] from item.bbox, else from item.geometry.

    Bhoonidhi's own `bbox` field has been observed to carry latitude as
    [west, north, east, south] rather than the STAC-standard
    [west, south, east, north] (confirmed against a live item where
    geometry-derived min/max latitude contradicted the bbox field's order).
    south/north are therefore always resolved by min/max rather than trusted
    positionally, so an inverted record never reaches pycsw's BBOX index.
    """
    bb = item.get("bbox")
    if bb and len(bb) in (4, 6):
        west, lat_a, east, lat_b = (bb[0], bb[1], bb[3], bb[4]) if len(bb) == 6 else bb
        south, north = min(lat_a, lat_b), max(lat_a, lat_b)
        return [west, south, east, north]
    geom = item.get("geometry")
    if geom and geom.get("coordinates"):
        pts = list(_coords(geom["coordinates"]))
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _collection_bbox(coll: dict) -> list[float] | None:
    try:
        bb = coll["extent"]["spatial"]["bbox"][0]
        west, lat_a, east, lat_b = (bb[0], bb[1], bb[3], bb[4]) if len(bb) == 6 else bb
        south, north = min(lat_a, lat_b), max(lat_a, lat_b)
        return [west, south, east, north]
    except (KeyError, IndexError, TypeError):
        return None


def _item_times(item: dict) -> tuple[str | None, str | None]:
    p = item.get("properties") or {}
    start = p.get("start_datetime") or p.get("datetime")
    end = p.get("end_datetime") or p.get("datetime")
    return start, end


def _collection_times(coll: dict) -> tuple[str | None, str | None]:
    try:
        s, e = coll["extent"]["temporal"]["interval"][0]
        return s, e
    except (KeyError, IndexError, TypeError, ValueError):
        return None, None


# -------------------------------------------------------------- core builder
def _build(
    *,
    file_id,
    parent_id,
    level,
    title,
    abstract,
    bbox,
    begin,
    end,
    open_end,
    keywords,
    topic,
    links,
    license_text,
    cite_date,
    harvest_source,
    metadata_contact,
    stamp,
):
    root = etree.Element(_q("gmd:MD_Metadata"), nsmap={k: v for k, v in NS.items() if k != "xlink"})
    root.set(_q("xsi:schemaLocation"), "http://www.isotc211.org/2005/gmd http://schemas.opengis.net/iso/19139/20060504/gmd/gmd.xsd")

    _cs(root, "gmd:fileIdentifier", file_id)
    _el(_el(root, "gmd:language"), "gmd:LanguageCode", "eng").attrib.update(
        {"codeList": "http://www.loc.gov/standards/iso639-2/", "codeListValue": "eng"}
    )
    _code(root, "gmd:characterSet", "MD_CharacterSetCode", "utf8")
    if parent_id:
        _cs(root, "gmd:parentIdentifier", parent_id)
    _code(root, "gmd:hierarchyLevel", "MD_ScopeCode", level)

    org, mail = metadata_contact
    _responsible_party(root, "gmd:contact", org, "pointOfContact", email=mail)
    _el(_el(root, "gmd:dateStamp"), "gco:DateTime", stamp)
    _cs(root, "gmd:metadataStandardName", "ISO 19115")
    _cs(root, "gmd:metadataStandardVersion", "2003/Cor 1:2006")

    rs = _el(_el(_el(_el(root, "gmd:referenceSystemInfo"), "gmd:MD_ReferenceSystem"),
                 "gmd:referenceSystemIdentifier"), "gmd:RS_Identifier")
    _cs(rs, "gmd:code", "4326")
    _cs(rs, "gmd:codeSpace", "EPSG")

    ident = _el(_el(root, "gmd:identificationInfo"), "gmd:MD_DataIdentification")
    cit = _el(_el(ident, "gmd:citation"), "gmd:CI_Citation")
    _cs(cit, "gmd:title", title)
    if cite_date:
        d = _el(_el(cit, "gmd:date"), "gmd:CI_Date")
        _el(_el(d, "gmd:date"), "gco:DateTime", cite_date)
        _code(d, "gmd:dateType", "CI_DateTypeCode", "creation")
    _cs(ident, "gmd:abstract", abstract)
    _responsible_party(ident, "gmd:pointOfContact", PROVIDER["organisation"], "originator", url=PROVIDER["url"])

    if keywords:
        dk = _el(_el(ident, "gmd:descriptiveKeywords"), "gmd:MD_Keywords")
        for kw in keywords:
            _cs(dk, "gmd:keyword", kw)
        _code(dk, "gmd:type", "MD_KeywordTypeCode", "theme")

    cons = _el(_el(ident, "gmd:resourceConstraints"), "gmd:MD_LegalConstraints")
    _code(cons, "gmd:accessConstraints", "MD_RestrictionCode", "restricted")
    _cs(cons, "gmd:otherConstraints", license_text)

    _el(_el(ident, "gmd:language"), "gmd:LanguageCode", "eng").attrib.update(
        {"codeList": "http://www.loc.gov/standards/iso639-2/", "codeListValue": "eng"}
    )
    _el(_el(ident, "gmd:topicCategory"), "gmd:MD_TopicCategoryCode", topic)

    ext = _el(_el(ident, "gmd:extent"), "gmd:EX_Extent")
    if bbox:
        gb = _el(_el(ext, "gmd:geographicElement"), "gmd:EX_GeographicBoundingBox")
        for tag, val in zip(
            ("gmd:westBoundLongitude", "gmd:eastBoundLongitude", "gmd:southBoundLatitude", "gmd:northBoundLatitude"),
            (bbox[0], bbox[2], bbox[1], bbox[3]),
        ):
            _el(_el(gb, tag), "gco:Decimal", repr(float(val)))
    if begin or end:
        tp = _el(_el(_el(_el(ext, "gmd:temporalElement"), "gmd:EX_TemporalExtent"), "gmd:extent"), "gml:TimePeriod")
        tp.set(_q("gml:id"), "tp1")
        _el(tp, "gml:beginPosition", begin or "")
        e = _el(tp, "gml:endPosition", end or "")
        if open_end and not end:
            e.set("indeterminatePosition", "now")

    if links:
        tr = _el(_el(_el(root, "gmd:distributionInfo"), "gmd:MD_Distribution"), "gmd:transferOptions")
        dto = _el(tr, "gmd:MD_DigitalTransferOptions")
        for name, desc, proto, url in links:
            ore = _el(_el(dto, "gmd:onLine"), "gmd:CI_OnlineResource")
            _el(_el(ore, "gmd:linkage"), "gmd:URL", url)
            _cs(ore, "gmd:protocol", proto)
            _cs(ore, "gmd:name", name)
            _cs(ore, "gmd:description", desc)

    dq = _el(_el(root, "gmd:dataQualityInfo"), "gmd:DQ_DataQuality")
    scope = _el(_el(dq, "gmd:scope"), "gmd:DQ_Scope")
    _code(scope, "gmd:level", "MD_ScopeCode", level)
    lin = _el(_el(dq, "gmd:lineage"), "gmd:LI_Lineage")
    _cs(lin, "gmd:statement", harvest_source)
    return root


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lineage(base_url: str, stamp: str) -> str:
    return (
        f"Metadata harvested from the STAC API at {base_url} by stac2csw on {stamp}. "
        "Only metadata is copied; data products remain at the provider."
    )


def collection_record(coll: dict, base_url: str, metadata_contact=("stac2csw harvester (unofficial)", None), stamp=None) -> bytes:
    stamp = stamp or _now()
    cid = coll["id"]
    begin, end = _collection_times(coll)
    kws = list(coll.get("keywords") or [])
    kws += [s for s in (coll.get("summaries", {}) or {}).get("platform", []) if isinstance(s, str)]
    links = [("Bhoonidhi STAC collection", "Collection description (STAC)", "WWW:LINK-1.0-http--link",
              f"{base_url}/data/collections/{cid}"),
             ("Bhoonidhi portal", "Browse and order data", "WWW:LINK-1.0-http--link", PROVIDER["url"])]
    root = _build(
        file_id=cid, parent_id=None, level="series",
        title=coll.get("title") or cid,
        abstract=coll.get("description") or coll.get("title") or cid,
        bbox=_collection_bbox(coll), begin=begin, end=end, open_end=True,
        keywords=kws, topic=topic_for(cid), links=links,
        license_text=_license(coll), cite_date=begin,
        harvest_source=_lineage(base_url, stamp), metadata_contact=metadata_contact, stamp=stamp,
    )
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def item_record(item: dict, collection: dict | None, base_url: str, metadata_contact=("stac2csw harvester (unofficial)", None), stamp=None) -> bytes:
    stamp = stamp or _now()
    cid = item.get("collection") or (collection or {}).get("id")
    iid = item["id"]
    p = item.get("properties") or {}
    begin, end = _item_times(item)
    title = p.get("title") or iid
    coll_title = (collection or {}).get("title") or cid
    abstract = p.get("description") or f"Item {iid} of collection {coll_title}."
    kws = [cid] if cid else []
    for key in ("platform", "constellation", "instruments", "sensor", "product_type"):
        v = p.get(key)
        kws += v if isinstance(v, list) else ([v] if isinstance(v, str) else [])
    links = []
    self_link = next((l["href"] for l in item.get("links", []) if l.get("rel") == "self"), None)
    links.append(("STAC item", "Item metadata (STAC)", "WWW:LINK-1.0-http--link",
                  self_link or f"{base_url}/data/collections/{cid}/items/{iid}"))
    links.append(("Download (login required)", "Product download via Bhoonidhi API",
                  "WWW:DOWNLOAD-1.0-http--download", f"{base_url}/download?id={iid}&collection={cid}"))
    root = _build(
        file_id=f"{cid}:{iid}", parent_id=cid, level="dataset",
        title=title, abstract=abstract, bbox=bbox_of(item), begin=begin, end=end, open_end=False,
        keywords=kws, topic=topic_for(cid), links=links,
        license_text=_license(collection or {}),
        cite_date=p.get("created") or p.get("updated") or begin,
        harvest_source=_lineage(base_url, stamp), metadata_contact=metadata_contact, stamp=stamp,
    )
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _license(coll: dict) -> str:
    lic = coll.get("license")
    base = "Access requires a registered Bhoonidhi account; see https://bhoonidhi.nrsc.gov.in for terms of use."
    return f"{base} STAC licence field: {lic}." if lic else base
