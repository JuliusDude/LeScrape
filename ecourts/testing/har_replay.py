"""
ecourts.testing.har_replay
==========================
Offline HAR Replay Transport Adapter mounting to requests.Session.
Replays HTTP responses recorded in services.ecourts.gov.in 2.har
without requiring any live network access.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import BaseAdapter
from requests.models import Response
from requests.structures import CaseInsensitiveDict

log = logging.getLogger("ecourts.testing.har_replay")

DEFAULT_HAR_SEARCH_PATHS = [
    os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "services.ecourts.gov.in 2.har"),
    os.path.join(os.path.dirname(__file__), "..", "..", "Model2", "services.ecourts.gov.in 2.har"),
    os.path.join(os.path.dirname(__file__), "..", "..", "Model3", "services.ecourts.gov.in 2.har"),
]


class HarReplayAdapter(BaseAdapter):
    """A requests transport adapter that intercepts outbound HTTP requests and

    serves recorded responses directly from an eCourts HAR file in memory.
    """

    def __init__(self, har_path: Optional[str] = None) -> None:
        super().__init__()
        self.har_path = self._locate_har(har_path)
        self.entries: List[Dict[str, Any]] = []
        self._custom_responses: Dict[Tuple[str, str], Response] = {}
        self._load_har()

    def _locate_har(self, har_path: Optional[str]) -> Optional[str]:
        if har_path and os.path.exists(har_path):
            return os.path.abspath(har_path)

        for candidate in DEFAULT_HAR_SEARCH_PATHS:
            norm = os.path.normpath(candidate)
            if os.path.exists(norm):
                return os.path.abspath(norm)
        return None

    def _load_har(self) -> None:
        if not self.har_path or not os.path.exists(self.har_path):
            log.warning("HarReplayAdapter: HAR file not found at %s", self.har_path)
            return

        with open(self.har_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.entries = data.get("log", {}).get("entries", [])
            log.info(
                "HarReplayAdapter: Loaded %d entries from %s",
                len(self.entries),
                self.har_path,
            )

    def register_response(
        self,
        method: str,
        endpoint_or_pattern: str,
        status_code: int = 200,
        json_data: Optional[Any] = None,
        text: str = "",
        content_bytes: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Register a custom synthetic response for testing scenarios (e.g.

        mocking home/viewHistory or simulated 500/429 errors).
        """
        resp = Response()
        resp.status_code = status_code
        resp.reason = "OK" if status_code == 200 else "Custom"
        h = CaseInsensitiveDict()
        if headers:
            h.update(headers)

        if json_data is not None:
            b = json.dumps(json_data).encode("utf-8")
            h.setdefault("Content-Type", "application/json; charset=UTF-8")
        elif content_bytes is not None:
            b = content_bytes
            h.setdefault("Content-Type", "image/png")
        else:
            b = text.encode("utf-8")
            h.setdefault("Content-Type", "text/html; charset=UTF-8")

        resp._content = b
        resp.headers = h
        self._custom_responses[(method.upper(), endpoint_or_pattern)] = resp

    def _extract_body_params(self, body: Any) -> Dict[str, str]:
        if not body:
            return {}
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        if isinstance(body, str):
            parsed = parse_qs(body)
            return {k: v[0] if len(v) == 1 else ",".join(v) for k, v in parsed.items()}
        return {}

    def _match_entry(self, request: requests.PreparedRequest) -> Optional[Dict[str, Any]]:
        req_url = request.url or ""
        req_method = (request.method or "GET").upper()
        parsed_url = urlparse(req_url)
        query_dict = parse_qs(parsed_url.query)
        endpoint = query_dict.get("p", [""])[0]

        # 1. Check custom overrides first
        for (m, pat), resp in self._custom_responses.items():
            if m == req_method and (pat == endpoint or pat in req_url):
                # Return wrapped custom response
                return {"_is_custom": True, "_response": resp}

        req_body_params = self._extract_body_params(request.body)

        best_entry = None
        best_score = -1

        for entry in self.entries:
            har_req = entry.get("request", {})
            if har_req.get("method", "").upper() != req_method:
                continue

            har_url = har_req.get("url", "")
            har_parsed = urlparse(har_url)
            har_endpoint = parse_qs(har_parsed.query).get("p", [""])[0]

            score = 0

            # Match image requests
            if "securimage_show.php" in req_url and "securimage_show.php" in har_url:
                score = 50
                # Exact token match if present in query
                if parsed_url.query and parsed_url.query in har_parsed.query:
                    score += 50
                if score > best_score:
                    best_score = score
                    best_entry = entry
                continue

            # Match javascript assets
            if "securimage.js" in req_url and "securimage.js" in har_url:
                return entry

            # Match AJAX endpoints via ?p=
            if endpoint and endpoint == har_endpoint:
                score = 50

                # Score matching POST parameters
                har_post = har_req.get("postData", {})
                har_text = har_post.get("text", "")
                har_params = self._extract_body_params(har_text)

                for k, v in req_body_params.items():
                    if k in ("ajax_req", "app_token"):
                        continue
                    if k in har_params and har_params[k] == str(v):
                        score += 10
                    elif k in har_params and har_params[k] != str(v):
                        score -= 5

                if score > best_score:
                    best_score = score
                    best_entry = entry

        return best_entry

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Optional[Union[float, Tuple[float, float]]] = None,
        verify: bool = True,
        cert: Optional[Any] = None,
        proxies: Optional[Dict[str, str]] = None,
    ) -> Response:
        matched = self._match_entry(request)
        if not matched:
            raise RuntimeError(
                f"HarReplayAdapter: No matching HAR fixture found for {request.method} {request.url}"
            )

        if matched.get("_is_custom"):
            custom_resp: Response = matched["_response"]
            custom_resp.url = request.url or ""
            custom_resp.request = request
            return custom_resp

        har_resp = matched.get("response", {})
        status_code = har_resp.get("status", 200)
        status_text = har_resp.get("statusText", "OK")

        response = Response()
        response.status_code = status_code
        response.reason = status_text
        response.url = request.url or ""
        response.request = request

        headers = CaseInsensitiveDict()
        for h in har_resp.get("headers", []):
            headers[h.get("name")] = h.get("value")
        response.headers = headers

        content_dict = har_resp.get("content", {})
        mime = content_dict.get("mimeType", "")
        text = content_dict.get("text", "")
        encoding = content_dict.get("encoding", "")

        if encoding == "base64" or "image" in mime:
            try:
                response._content = base64.b64decode(text)
            except Exception as exc:
                log.warning("Base64 decode failed for image content: %s", exc)
                response._content = text.encode("utf-8")
        else:
            response._content = text.encode("utf-8")

        return response

    def close(self) -> None:
        pass
