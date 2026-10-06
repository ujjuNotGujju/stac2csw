"""STAC clients used by the harvester.

BhoonidhiClient follows the Bhoonidhi API specification
(https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/):

* POST /auth/token  {userId, password, grant_type}  -> JWT access + refresh token
  (rate limit: 20 requests/hour/IP, so the token is reused and refreshed)
* GET  /data/collections
* POST /data/search  (STAC API item search; limit max 500; 'next' links)
  (rate limit: 3 requests/second/IP)

FixtureClient exposes the same two methods over local JSON files so the whole
pipeline can be exercised without credentials.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterator

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://bhoonidhi-api.nrsc.gov.in"


class AuthError(RuntimeError):
    pass


class BhoonidhiClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        user: str | None = None,
        password: str | None = None,
        min_interval: float = 0.4,  # 3 req/s limit -> stay under it
        max_retries: int = 5,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._user = user
        self._password = password
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self._access: str | None = None
        self._refresh: str | None = None
        self._last_call = 0.0
        self.token_requests = 0  # exposed so tests/README can show we respect the 20/h limit

    # ---------------------------------------------------------------- auth
    def _token_call(self, payload: dict) -> None:
        self.token_requests += 1
        r = self.session.post(f"{self.base_url}/auth/token", json=payload, timeout=self.timeout)
        if r.status_code == 429:
            raise AuthError("Token endpoint rate-limited (20 requests/hour). Wait and retry.")
        if r.status_code in (401, 403):
            raise AuthError(f"Authentication refused ({r.status_code}): {r.text[:200]}")
        r.raise_for_status()
        body = r.json()
        self._access = body["access_token"]
        self._refresh = body.get("refresh_token")

    def login(self) -> None:
        if not self._user or not self._password:
            raise AuthError("No credentials supplied (set BHOONIDHI_USER / BHOONIDHI_PASSWORD).")
        self._token_call({"userId": self._user, "password": self._password, "grant_type": "password"})

    def _renew(self) -> None:
        """Prefer the refresh token; fall back to a password login."""
        if self._refresh:
            try:
                self._token_call(
                    {"userId": self._user, "refresh_token": self._refresh, "grant_type": "refresh_token"}
                )
                return
            except AuthError:
                log.info("refresh token rejected, logging in again")
        self.login()

    # ------------------------------------------------------------- plumbing
    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        renewed = False
        extra_headers = dict(kw.pop("headers", {}) or {})
        for attempt in range(self.max_retries + 1):
            self._throttle()
            headers = dict(extra_headers)
            if self._access:
                headers["Authorization"] = f"Bearer {self._access}"
            r = self.session.request(method, url, headers=headers, timeout=self.timeout, **kw)
            if r.status_code == 401 and self._user and not renewed:
                renewed = True
                self._renew()
                continue
            if r.status_code == 429 or r.status_code >= 500:
                delay = float(r.headers.get("Retry-After", 2 ** attempt))
                log.warning("%s %s -> %s, retrying in %.1fs", method, url, r.status_code, delay)
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"{method} {url} failed after {self.max_retries} retries")

    # ----------------------------------------------------------------- API
    def collections(self) -> list[dict]:
        if self._user and not self._access:
            self.login()
        data = self._request("GET", f"{self.base_url}/data/collections").json()
        return data["collections"] if isinstance(data, dict) else data

    def search(
        self,
        collections: list[str],
        datetime: str | None = None,
        bbox: list[float] | None = None,
        limit: int = 100,
        max_items: int | None = None,
    ) -> Iterator[dict]:
        """Yield STAC items, following 'next' links (STAC API item-search paging)."""
        if self._user and not self._access:
            self.login()
        body: dict = {"collections": collections, "limit": min(limit, 500)}
        if datetime:
            body["datetime"] = datetime
        if bbox:
            body["bbox"] = bbox
        url, method = f"{self.base_url}/data/search", "POST"
        seen = 0
        while True:
            resp = self._request(method, url, json=body) if method == "POST" else self._request(method, url)
            page = resp.json()
            feats = page.get("features", [])
            for f in feats:
                yield f
                seen += 1
                if max_items and seen >= max_items:
                    return
            nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
            if not nxt or not feats:
                return
            url = nxt["href"]
            method = (nxt.get("method") or "GET").upper()
            if method == "POST":
                nb = nxt.get("body", {})
                body = {**body, **nb} if nxt.get("merge") else nb


class FixtureClient:
    """Same interface as BhoonidhiClient, backed by local JSON (offline demo/tests)."""

    base_url = "https://bhoonidhi-api.nrsc.gov.in"

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def collections(self) -> list[dict]:
        return json.loads((self.dir / "collections.json").read_text())["collections"]

    def search(self, collections, datetime=None, bbox=None, limit=100, max_items=None):
        items = json.loads((self.dir / "items.json").read_text())["features"]
        n = 0
        for it in items:
            if collections and it.get("collection") not in collections:
                continue
            yield it
            n += 1
            if max_items and n >= max_items:
                return
