#!/usr/bin/env python3
"""Gap evidence: does the target host expose an OGC CSW (or another OGC service)?

    python scripts/probe_gap.py                          # probes Bhoonidhi, writes docs/gap-evidence.md
    python scripts/probe_gap.py --hosts http://localhost:8000   # positive control against our own CSW

A host counts as a CSW only if the response body is a csw:Capabilities XML document.
A 200 that returns a catch-all HTML page is recorded as NOT a CSW (the failure mode seen
on Bhoonidhi hosts), so the result cannot be a false positive from a lenient status check.
"""
import argparse
import datetime as dt
import re
import sys

import requests

HOSTS = ["https://bhoonidhi.nrsc.gov.in", "https://bhoonidhi-api.nrsc.gov.in"]
PATHS = ["/", "/csw", "/pycsw/csw", "/geoserver/csw", "/geoserver/ows", "/geonetwork/srv/eng/csw", "/ows", "/wms", "/wfs", "/wcs"]
SERVICES = {
    "CSW": ("2.0.2", r"<(\w+:)?Capabilities[^>]*opengis\.net/cat/csw"),
    "WMS": ("1.3.0", r"<(\w+:)?WMS_Capabilities|<WMT_MS_Capabilities"),
    "WFS": ("2.0.0", r"<(\w+:)?WFS_Capabilities"),
    "WCS": ("2.0.1", r"<(\w+:)?Capabilities[^>]*opengis\.net/wcs"),
    "WPS": ("1.0.0", r"<(\w+:)?Capabilities[^>]*opengis\.net/wps"),
}
DOC = "https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/"


def probe(host, path, svc, ver):
    url = host.rstrip("/") + path
    try:
        r = requests.get(url, params={"service": svc, "version": ver, "request": "GetCapabilities"}, timeout=20, headers={"User-Agent": "stac2csw-gap-probe"})
        body = r.text[:4000]
        found = bool(re.search(SERVICES[svc][1], body))
        return r.status_code, r.headers.get("Content-Type", "?").split(";")[0], found, body[:60].replace("\n", " ")
    except requests.RequestException as e:
        return "ERR", type(e).__name__, False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", nargs="*", default=HOSTS)
    ap.add_argument("--out", default="docs/gap-evidence.md")
    ap.add_argument("--skip-docs", action="store_true")
    a = ap.parse_args()
    lines = [f"# Gap evidence\n\nRun: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n"]
    anyfound = False

    lines.append("## 1. Endpoint probes (GetCapabilities)\n\n| host | path | service | HTTP | content-type | real OGC capabilities? |\n|---|---|---|---|---|---|")
    for h in a.hosts:
        for p in PATHS:
            for svc, (ver, _) in SERVICES.items():
                code, ctype, found, _ = probe(h, p, svc, ver)
                anyfound |= found
                lines.append(f"| {h} | {p} | {svc} | {code} | {ctype} | {'**YES**' if found else 'no'} |")

    if not a.skip_docs:
        lines.append("\n## 2. API specification keyword count\n")
        try:
            t = requests.get(DOC, timeout=30).text
            text = re.sub(r"<[^>]+>", " ", t)
            lines.append(f"Source: {DOC}\n\n| term | mentions |\n|---|---|")
            for term in ["STAC", "CSW", "WMS", "WFS", "WCS", "WPS", "OGC API", "ISO 19115", "19139"]:
                lines.append(f"| {term} | {len(re.findall(re.escape(term), text))} |")
        except requests.RequestException as e:
            lines.append(f"Could not fetch the specification: {e}")

    lines.append(f"\n## Verdict\n\n{'At least one real OGC endpoint answered - REVIEW the table before claiming a gap.' if anyfound else 'No probed host returned a valid OGC capabilities document.'}")
    from pathlib import Path
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if anyfound and "bhoonidhi" in "".join(a.hosts) else 0


if __name__ == "__main__":
    sys.exit(main())
