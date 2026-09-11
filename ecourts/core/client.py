"""
ecourts.core.client
===================
PacedSession implementation enforcing inter-request rate pacing, standard
eCourts XHR headers, automatic token injection, cookie preservation, and
socket retry handling.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests

log = logging.getLogger("ecourts.core.client")

DEFAULT_BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"
DEFAULT_IMG_BASE = "https://services.ecourts.gov.in"

DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://services.ecourts.gov.in",
    "Referer": "https://services.ecourts.gov.in/",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "delimeter": "bnvfgffy6",
    "Juihjrtr778": "bnvfgffy6",
}


class PacedSession:
    """Encapsulates requests.Session with inter-request pacing guardrails,

    automatic token injection (ajax_req=true, app_token), cookie preservation,
    and automatic retries on socket/timeout errors.
    """

    def __init__(
        self,
        min_delay: float = 2.0,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: Optional[str] = None,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.min_delay = max(0.0, float(min_delay))
        self.base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.max_retries = max_retries
        self.app_token = ""
        self._last_request_time = 0.0
        self._next_allowed_time = 0.0
        self._lock = threading.Lock()

        self.session = session or requests.Session()
        headers = dict(DEFAULT_HEADERS)
        if user_agent:
            headers["User-Agent"] = user_agent
        self.session.headers.update(headers)

    def _enforce_pacing(self) -> None:
        """Wait if needed to ensure requests are spaced by at least min_delay seconds,
        using an atomic reservation schedule under lock to prevent concurrent burst dispatches.
        """
        if self.min_delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            dispatch_time = max(now, self._next_allowed_time)
            self._next_allowed_time = dispatch_time + self.min_delay
        sleep_dur = dispatch_time - now
        if sleep_dur > 0:
            log.debug("Pacing guardrail active: sleeping for %.3fs", sleep_dur)
            time.sleep(sleep_dur)

    def _mark_request_complete(self) -> None:
        with self._lock:
            self._last_request_time = time.monotonic()

    def _resolve_url(self, endpoint: str) -> str:
        """Resolves endpoint to a full URL.

        If endpoint starts with http:// or https://, use as-is.
        If endpoint is like 'casestatus/fillDistrict', route to '{base_url}?p={endpoint}'.
        If endpoint starts with '?', append to base_url.
        """
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if endpoint.startswith("?"):
            return f"{self.base_url.rstrip('/')}{endpoint}"
        return f"{self.base_url}?p={endpoint}"

    def init_session(self) -> str:
        """Fetch casestatus/index to initialize session cookies and extract the app_token."""
        url = self._resolve_url("casestatus/index")
        nav_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        self._enforce_pacing()
        try:
            resp = self.session.get(url, headers=nav_headers, timeout=30)
            self._mark_request_complete()
            import re
            m = re.search(r'name=["\']app_token["\'][^>]*value=["\']([a-zA-Z0-9_-]+)["\']', resp.text)
            if not m:
                m = re.search(r'id=["\']app_token["\'][^>]*value=["\']([a-zA-Z0-9_-]+)["\']', resp.text)
            if m:
                self.app_token = m.group(1)
                log.info("Initialized session with app_token=%s", self.app_token)
            return self.app_token
        except Exception as exc:
            self._mark_request_complete()
            log.debug("Session initialization skipped or failed: %s", exc)
            return self.app_token

    def post(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
        timeout: float = 45.0,
        **kwargs: Any,
    ) -> requests.Response:
        """Send a POST request with pacing, default headers, ajax_req=true, and app_token."""
        url = self._resolve_url(endpoint)
        max_attempts = retries if retries is not None else self.max_retries
        payload: Dict[str, Any] = {
            "ajax_req": "true",
            "app_token": self.app_token,
        }
        if data:
            payload.update(data)

        last_err: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            self._enforce_pacing()
            try:
                resp = self.session.post(url, data=payload, timeout=timeout, **kwargs)
                self._mark_request_complete()
                try:
                    data_json = resp.json()
                    if isinstance(data_json, dict) and data_json.get("app_token"):
                        self.app_token = data_json["app_token"]
                except Exception:
                    pass
                return resp
            except (requests.ConnectionError, requests.Timeout, requests.RequestException) as exc:
                self._mark_request_complete()
                last_err = exc
                log.warning(
                    "POST %s failed (attempt %d/%d): %s",
                    url,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    backoff = (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                    time.sleep(backoff)

        raise RuntimeError(f"POST {url} failed after {max_attempts} attempts: {last_err}") from last_err

    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
        timeout: float = 45.0,
        **kwargs: Any,
    ) -> requests.Response:
        """Send a GET request with pacing and retry handling."""
        url = self._resolve_url(endpoint)
        max_attempts = retries if retries is not None else self.max_retries

        last_err: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            self._enforce_pacing()
            try:
                resp = self.session.get(url, params=params, timeout=timeout, **kwargs)
                self._mark_request_complete()
                return resp
            except (requests.ConnectionError, requests.Timeout, requests.RequestException) as exc:
                self._mark_request_complete()
                last_err = exc
                log.warning(
                    "GET %s failed (attempt %d/%d): %s",
                    url,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    backoff = (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                    time.sleep(backoff)

        raise RuntimeError(f"GET {url} failed after {max_attempts} attempts: {last_err}") from last_err

    def get_image(self, image_url_or_path: str, timeout: float = 45.0) -> bytes:
        """Fetch binary image bytes from relative or absolute URL."""
        if image_url_or_path.startswith("/"):
            url = f"{DEFAULT_IMG_BASE}{image_url_or_path}"
        elif not (image_url_or_path.startswith("http://") or image_url_or_path.startswith("https://")):
            url = urljoin(self.base_url, image_url_or_path)
        else:
            url = image_url_or_path

        self._enforce_pacing()
        try:
            resp = self.session.get(
                url,
                headers={"Accept": "image/avif,image/webp,image/png,*/*", "Sec-Fetch-Dest": "image"},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.content
        finally:
            self._mark_request_complete()

    def mount(self, prefix: str, adapter: requests.adapters.BaseAdapter) -> None:
        """Mount a transport adapter to the underlying requests.Session."""
        self.session.mount(prefix, adapter)

    def close(self) -> None:
        """Close the underlying session."""
        self.session.close()

    def __enter__(self) -> PacedSession:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
