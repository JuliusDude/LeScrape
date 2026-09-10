"""
Unit and boundary tests for ecourts.core.client (PacedSession).
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases).
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import requests
from requests.adapters import BaseAdapter
from requests.models import Response

from ecourts.core.client import (
    DEFAULT_BASE_URL,
    DEFAULT_HEADERS,
    DEFAULT_IMG_BASE,
    PacedSession,
)


class DummyAdapter(BaseAdapter):
    """Offline test adapter returning synthetic HTTP responses."""

    def __init__(self, response_text: str = "{}", status_code: int = 200, binary_data: bytes = b""):
        super().__init__()
        self.response_text = response_text
        self.status_code = status_code
        self.binary_data = binary_data
        self.calls: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        resp = Response()
        resp.status_code = self.status_code
        resp.url = request.url
        resp.request = request
        if self.binary_data:
            resp._content = self.binary_data
            resp.headers["Content-Type"] = "image/png"
        else:
            resp._content = self.response_text.encode("utf-8")
            resp.headers["Content-Type"] = "application/json; charset=UTF-8"
        return resp

    def close(self):
        pass


class TestPacedSessionTier1(unittest.TestCase):
    """Tier 1: Feature Coverage tests for PacedSession."""

    def test_default_headers(self):
        """Verify standard eCourts XHR headers are set by default."""
        session = PacedSession()
        headers = session.session.headers
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
        self.assertIn("application/json", headers["Accept"])
        self.assertEqual(headers["Origin"], "https://services.ecourts.gov.in")
        self.assertEqual(headers["Referer"], "https://services.ecourts.gov.in/")
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")
        session.close()

    def test_custom_user_agent_override(self):
        """Verify user-agent override is respected in session headers."""
        custom_ua = "TestBot/1.0 (Testing)"
        session = PacedSession(user_agent=custom_ua)
        self.assertEqual(session.session.headers["User-Agent"], custom_ua)
        session.close()

    def test_endpoint_url_resolution(self):
        """Verify endpoint path resolution for standard, query-only, and absolute URLs."""
        session = PacedSession(base_url="https://services.ecourts.gov.in/ecourtindia_v6/")
        self.assertEqual(
            session._resolve_url("casestatus/fillDistrict"),
            "https://services.ecourts.gov.in/ecourtindia_v6/?p=casestatus/fillDistrict",
        )
        self.assertEqual(
            session._resolve_url("?p=casestatus/fillcomplex"),
            "https://services.ecourts.gov.in/ecourtindia_v6?p=casestatus/fillcomplex",
        )
        self.assertEqual(
            session._resolve_url("https://example.com/api"),
            "https://example.com/api",
        )
        session.close()

    def test_automatic_payload_injection(self):
        """Verify POST requests automatically include ajax_req='true' and app_token."""
        adapter = DummyAdapter(response_text='{"status": 1}')
        session = PacedSession(min_delay=0.0)
        session.mount("https://", adapter)
        session.app_token = "tok_test123"

        resp = session.post("casestatus/fillDistrict", data={"state_code": "3"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(adapter.calls), 1)

        body = adapter.calls[0].body
        self.assertIn("ajax_req=true", body)
        self.assertIn("app_token=tok_test123", body)
        self.assertIn("state_code=3", body)
        session.close()

    def test_pacing_delay_enforced(self):
        """Verify sequential requests maintain at least min_delay interval."""
        adapter = DummyAdapter(response_text='{"status": 1}')
        delay = 0.08
        session = PacedSession(min_delay=delay)
        session.mount("https://", adapter)

        t0 = time.monotonic()
        session.post("test1")
        session.post("test2")
        elapsed = time.monotonic() - t0

        self.assertGreaterEqual(elapsed, delay, f"Elapsed {elapsed:.4f}s should be >= {delay}s")
        session.close()

    def test_get_image_resolution_and_headers(self):
        """Verify get_image resolves relative and absolute URLs with image headers."""
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        adapter = DummyAdapter(binary_data=fake_png)
        session = PacedSession(min_delay=0.0)
        session.mount("https://", adapter)

        data = session.get_image("/ecourtindia_v6/vendor/securimage/securimage_show.php?123")
        self.assertEqual(data, fake_png)
        self.assertEqual(adapter.calls[0].url, f"{DEFAULT_IMG_BASE}/ecourtindia_v6/vendor/securimage/securimage_show.php?123")
        self.assertIn("image/", adapter.calls[0].headers["Accept"])
        session.close()

    def test_context_manager_lifecycle(self):
        """Verify context manager properly enters and exits."""
        with PacedSession(min_delay=0.0) as session:
            self.assertIsInstance(session, PacedSession)
            self.assertIsNotNone(session.session)


class TestPacedSessionTier2(unittest.TestCase):
    """Tier 2: Boundary & Corner Cases for PacedSession."""

    def test_zero_min_delay(self):
        """Verify min_delay=0.0 executes without artificial sleep."""
        adapter = DummyAdapter()
        session = PacedSession(min_delay=0.0)
        session.mount("https://", adapter)

        t0 = time.monotonic()
        for i in range(3):
            session.get(f"test_{i}")
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.5, "Zero delay should not sleep unnecessarily")
        session.close()

    def test_negative_min_delay_clamped(self):
        """Verify negative min_delay is safely clamped to 0.0."""
        session = PacedSession(min_delay=-5.0)
        self.assertEqual(session.min_delay, 0.0)
        session.close()

    def test_retry_on_connection_error_eventual_success(self):
        """Verify retries on ConnectionError succeed when a subsequent attempt passes."""
        session = PacedSession(min_delay=0.0, max_retries=3)
        mock_resp = MagicMock(spec=Response)
        mock_resp.status_code = 200

        attempts = [0]

        def side_effect(*args, **kwargs):
            attempts[0] += 1
            if attempts[0] < 3:
                raise requests.ConnectionError("Simulated network drop")
            return mock_resp

        with patch("ecourts.core.client.time.sleep") as mock_sleep:
            with patch.object(session.session, "post", side_effect=side_effect):
                resp = session.post("casestatus/fillDistrict")
                self.assertEqual(resp, mock_resp)
                self.assertEqual(attempts[0], 3)
                self.assertEqual(mock_sleep.call_count, 2)
        session.close()

    def test_max_retries_exhaustion_raises_runtime_error(self):
        """Verify exhausting retries raises RuntimeError."""
        session = PacedSession(min_delay=0.0, max_retries=2)

        with patch("ecourts.core.client.time.sleep"):
            with patch.object(session.session, "get", side_effect=requests.Timeout("Timed out")):
                with self.assertRaises(RuntimeError) as ctx:
                    session.get("casestatus/fillDistrict")
                self.assertIn("failed after 2 attempts", str(ctx.exception))
        session.close()

    def test_base_url_normalization_trailing_slash(self):
        """Verify base_url without trailing slash is normalized."""
        session = PacedSession(base_url="https://services.ecourts.gov.in/ecourtindia_v6")
        self.assertTrue(session.base_url.endswith("/"))
        resolved = session._resolve_url("casestatus/fillDistrict")
        self.assertEqual(resolved, "https://services.ecourts.gov.in/ecourtindia_v6/?p=casestatus/fillDistrict")
        session.close()

    def test_thread_safe_pacing(self):
        """Verify multiple threads sharing PacedSession respect mutual pacing."""
        adapter = DummyAdapter()
        min_delay = 0.05
        session = PacedSession(min_delay=min_delay)
        session.mount("https://", adapter)

        timestamps: list[float] = []
        lock = threading.Lock()

        session.get("warmup")

        def worker(idx: int):
            session.get(f"endpoint_{idx}")
            with lock:
                timestamps.append(time.monotonic())

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total_time = time.monotonic() - t0

        # Unpaced execution takes < 0.005s; paced execution enforces serialized delays
        self.assertGreaterEqual(total_time, 0.05, f"Thread pacing total time {total_time:.4f}s should be >= 0.05s")
        session.close()

    def test_custom_retries_override_per_call(self):
        """Verify per-call retries override session max_retries."""
        session = PacedSession(min_delay=0.0, max_retries=5)
        attempts = [0]

        def side_effect(*args, **kwargs):
            attempts[0] += 1
            raise requests.ConnectionError("Persistent fail")

        with patch("ecourts.core.client.time.sleep"):
            with patch.object(session.session, "post", side_effect=side_effect):
                with self.assertRaises(RuntimeError):
                    session.post("test", retries=2)
                self.assertEqual(attempts[0], 2)
        session.close()


if __name__ == "__main__":
    unittest.main()
