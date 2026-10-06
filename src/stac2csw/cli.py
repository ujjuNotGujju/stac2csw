"""stac2csw command line.

  stac2csw harvest --out records/ [--offline tests/fixtures] [--collections A,B]
                   [--datetime START/END] [--max-items 200] [--collections-only]
  stac2csw load    --config config/pycsw.cfg --records records/

Credentials are read from BHOONIDHI_USER / BHOONIDHI_PASSWORD (never from argv).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import iso19139
from .client import DEFAULT_BASE_URL, BhoonidhiClient, FixtureClient

log = logging.getLogger("stac2csw")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def cmd_harvest(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.clean:
        for f in out.glob("*.xml"):
            f.unlink()

    if a.offline:
        client = FixtureClient(a.offline)
    else:
        client = BhoonidhiClient(
            base_url=a.base_url,
            user=os.environ.get("BHOONIDHI_USER"),
            password=os.environ.get("BHOONIDHI_PASSWORD"),
        )
    contact = (a.contact_name, a.contact_email)
    stamp = iso19139._now()

    colls = client.collections()
    wanted = [c.strip() for c in a.collections.split(",")] if a.collections else None
    if wanted:
        colls = [c for c in colls if c["id"] in wanted]
        missing = set(wanted) - {c["id"] for c in colls}
        if missing:
            log.warning("collections not found in catalogue: %s", ", ".join(sorted(missing)))
    by_id = {c["id"]: c for c in colls}

    n_coll = n_item = 0
    for c in colls:
        (out / f"collection__{_safe(c['id'])}.xml").write_bytes(
            iso19139.collection_record(c, client.base_url, contact, stamp)
        )
        n_coll += 1

    if not a.collections_only:
        for c in colls:
            count = 0
            for item in client.search([c["id"]], datetime=a.datetime, max_items=a.max_items):
                item.setdefault("collection", c["id"])
                (out / f"item__{_safe(c['id'])}__{_safe(item['id'])}.xml").write_bytes(
                    iso19139.item_record(item, by_id.get(c["id"]), client.base_url, contact, stamp)
                )
                count += 1
            n_item += count
            log.info("%-40s %d items", c["id"], count)

    print(f"wrote {n_coll} collection record(s) and {n_item} item record(s) to {out}/")
    return 0


def _admin(*args: str) -> None:
    exe = shutil.which("pycsw-admin.py") or shutil.which("pycsw-admin")
    if not exe:
        sys.exit("pycsw-admin.py not found. Install with: pip install 'stac2csw[server]'")
    subprocess.run([exe, *args], check=True)


def cmd_load(a: argparse.Namespace) -> int:
    cfg = str(a.config)
    if a.reset:
        _admin("-c", "setup_db", "-f", cfg)
    _admin("-c", "load_records", "-f", cfg, "-p", str(a.records), "-r", "-y")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stac2csw", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="STAC API -> ISO 19139 XML files")
    h.add_argument("--out", default="records")
    h.add_argument("--base-url", default=DEFAULT_BASE_URL)
    h.add_argument("--offline", metavar="DIR", help="read collections.json/items.json from DIR instead of the live API")
    h.add_argument("--collections", help="comma-separated collection ids (default: all)")
    h.add_argument("--datetime", help="RFC 3339 instant or START/END range for items")
    h.add_argument("--max-items", type=int, default=100, help="max items per collection (default 100)")
    h.add_argument("--collections-only", action="store_true", help="harvest collection-level records only")
    h.add_argument("--clean", action="store_true", help="delete existing XML in --out first")
    h.add_argument("--contact-name", default="stac2csw harvester (unofficial)",
                   help="organisation responsible for THIS metadata record")
    h.add_argument("--contact-email")
    h.set_defaults(func=cmd_harvest)

    l = sub.add_parser("load", help="load ISO XML files into the pycsw repository")
    l.add_argument("--config", default="config/pycsw.cfg")
    l.add_argument("--records", default="records")
    l.add_argument("--reset", action="store_true", help="(re)create the database first")
    l.set_defaults(func=cmd_load)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
