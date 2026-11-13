"""HTTP layer: transport abstraction + per-worker eCourts session (handshake, token rotation,
retries, session recovery)."""
from __future__ import annotations

import contextlib
import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlencode, urljoin

from . import parsers
from .config import Config, Location
from .ratelimit import BlockedAbort, BlockGuard, Governor, Metrics, RateLimiter

log = logging.getLogger("V2.http")


class EcourtsError(Exception):
    pass


class TransportError(EcourtsError):
    """Connection reset / timeout / DNS etc. (transient)."""


class HttpFailure(EcourtsError):
    def __init__(self, msg: str, status: Optional[int] = None):
        super().__init__(msg)
        self.status = status


class SessionInvalid(EcourtsError):
    """The server no longer accepts this session (WAF page, bad token, ...)."""


class HandshakeError(EcourtsError):
    pass


class BlockSignal(EcourtsError):
    """A block response (403/405 / WAF page) was seen; the shared BlockGuard has been told."""


class HandshakeCapExceeded(EcourtsError):
    """A session re-handshook more often than allowed per run (treated as a block)."""


class BadContent(EcourtsError):
    """A GET returned something other than the expected payload (e.g. HTML instead of a PDF)."""


@dataclass
class Response:
    status: int
    text: str = ""
    content: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


class RequestsTransport:
    """requests.Session wrapper (keep-alive, own cookie jar). ``reset`` starts a fresh cookie jar."""

    def __init__(self, user_agent: str, connect_timeout: float = 10.0, read_timeout: float = 40.0):
        import requests
        self._requests = requests
        self.ua = user_agent
        self.timeout = (connect_timeout, read_timeout)
        self.reset()

    def reset(self) -> None:
        s = self._requests.Session()
        s.headers.update({"User-Agent": self.ua, "Accept-Language": "en-US,en;q=0.9",
                          "Accept-Encoding": "gzip, deflate"})
        a = self._requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=2, max_retries=0)
        s.mount("https://", a)
        s.mount("http://", a)
        old = getattr(self, "session", None)
        self.session = s
        if old is not None:
            old.close()

    def request(self, method: str, url: str, data: Optional[str] = None,
                headers: Optional[Dict[str, str]] = None) -> Response:
        try:
            r = self.session.request(method, url, data=data, headers=headers or {}, timeout=self.timeout,
                                     allow_redirects=True)
        except self._requests.RequestException as e:
            raise TransportError(f"{type(e).__name__}: {e}") from e
        try:
            text = r.text if "pdf" not in r.headers.get("Content-Type", "").lower() else ""
        except Exception:
            text = ""
        return Response(r.status_code, text, r.content, dict(r.headers))


_TRANSIENT_STATUS = (429, 500, 502, 503, 504)


class EcourtsSession:
    """One logical browser session. Not safe for concurrent use of the *same* instance by many
    threads (the app token rotates on every response) -- a lock serialises calls; use one session
    per worker thread for parallelism.

    With a shared ``guard`` (BlockGuard) every block signal (403/405, WAF page) pauses ALL sessions; this class
    never retries or re-handshakes on its own while the guard is blocked."""

    def __init__(self, cfg: Config, loc: Location, transport, limiter: Optional[RateLimiter] = None,
                 governor: Optional[Governor] = None, sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[], float] = random.random, name: str = "s0",
                 guard: Optional[BlockGuard] = None, metrics: Optional[Metrics] = None):
        self.cfg, self.loc, self.transport, self.name = cfg, loc, transport, name
        self.limiter, self.governor, self._sleep, self._rng = limiter, governor, sleep, rng
        self.guard, self.metrics = guard, metrics
        self._lock = threading.RLock()
        self.token = ""
        self.delimeter: Optional[str] = None
        self.hdr_name: Optional[str] = None
        self.components_url = ""
        self.ready = False
        self.epoch = 0
        self._handshaking = False
        self.stats = {"requests": 0, "retries": 0, "recoveries": 0, "handshakes": 0, "errors": 0}

    # ------------------------------------------------------------------ plumbing
    @property
    def index_url(self) -> str:
        return self.cfg.base_url + "?p=casestatus/index"

    def _backoff(self, attempt: int, retry_after: Optional[float] = None) -> None:
        d = min(self.cfg.backoff_max, self.cfg.backoff_base * (2 ** (attempt - 1)))
        d = d * (0.5 + 0.5 * self._rng())
        if retry_after:
            d = max(d, min(retry_after, self.cfg.backoff_max))
        self.stats["retries"] += 1
        self._sleep(d)

    def _probe(self) -> bool:
        """Single GET of the index page used by the BlockGuard after a cooldown."""
        if self.limiter:
            self.limiter.acquire()
        try:
            r = self.transport.request("GET", self.index_url, headers={"Accept": "text/html,*/*"})
        except (TransportError, OSError):
            return False
        self.stats["requests"] += 1
        return r.status == 200 and not BlockGuard.is_block(r.status, r.text)

    def _send(self, method: str, url: str, data: Optional[str] = None,
              headers: Optional[Dict[str, str]] = None, label: str = "other") -> Response:
        """One logical request with retries on connection errors, timeouts, 5xx and 429.
        Raises BlockSignal (after telling the guard) on a block response; waits while the guard is blocked."""
        attempt = 0
        while True:
            attempt += 1
            if self.guard:
                self.guard.wait_clear(self._probe)
                if not self._handshaking and self.epoch != self.guard.epoch:
                    # the guard unblocked while we waited (the probe may even have touched our cookie jar):
                    # this session must re-handshake before sending anything
                    raise BlockSignal("session predates the last unblock")
            if self.governor:
                self.governor.before_request()
            if self.limiter:
                self.limiter.acquire()
            t0 = time.monotonic()
            ep = self.guard.epoch if self.guard else None
            try:
                resp = self.transport.request(method, url, data=data, headers=headers)
            except (TransportError, OSError) as e:
                self.stats["errors"] += 1
                if self.governor:
                    self.governor.record(False)
                if attempt > self.cfg.max_retries:
                    raise HttpFailure(f"{method} {url}: {e} (after {attempt} attempts)") from e
                log.debug("%s transport error %s (attempt %d)", self.name, e, attempt)
                self._backoff(attempt)
                continue
            self.stats["requests"] += 1
            if self.metrics:
                self.metrics.add(label, time.monotonic() - t0)
            if self.guard and BlockGuard.is_block(resp.status, resp.text):
                self.guard.signal(f"{self.name} {label}: HTTP {resp.status}", ep)
                raise BlockSignal(f"{label}: HTTP {resp.status}")
            if resp.status in _TRANSIENT_STATUS:
                self.stats["errors"] += 1
                if self.governor:
                    self.governor.record(False)
                if attempt > self.cfg.max_retries:
                    raise HttpFailure(f"{method} {url}: HTTP {resp.status}", resp.status)
                ra = None
                try:
                    ra = float(resp.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    pass
                self._backoff(attempt, ra)
                continue
            if self.governor:
                self.governor.record(True)
            if self.guard and resp.status < 400:
                self.guard.note_ok()
            return resp

    def _ajax_headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.cfg.base_url.split("/ecourtindia_v6")[0],
            "Referer": self.index_url,
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.delimeter:
            h["delimeter"] = self.delimeter
            if self.hdr_name:
                h[self.hdr_name] = self.delimeter
        return h

    @staticmethod
    def _classify(resp: Response):
        """-> (problem|None, json|None)"""
        if resp.status in (401, 403, 405) or resp.status >= 400:
            return f"HTTP {resp.status}", None
        text = (resp.text or "").strip()
        try:
            j = json.loads(text)
        except ValueError:
            why = "Security Page" if "Security Page" in text else (
                "Search Page not Found" if "Search Page not Found" in text else "non-JSON response")
            return why, None
        if not isinstance(j, dict):
            return "unexpected JSON type", None
        if not j.get("app_token"):
            return "no app_token in response", j
        # Live 2026-11-13: a stale session answers every call with this errormsg (and a frozen
        # token) until it is re-handshaken; treat it as an invalid session, not a case failure.
        if "invalid request" in str(j.get("errormsg") or "").lower():
            return "Invalid Request (stale session)", j
        return None, j

    # ------------------------------------------------------------------ handshake
    def handshake(self) -> None:
        """index -> components.js -> set_data, on a fresh cookie jar. Raises HandshakeError / BlockSignal.
        Serialised across sessions when a guard is shared."""
        with self._lock:
            self.ready = False
            if self.stats["handshakes"] >= self.cfg.max_handshakes:
                if self.guard:
                    self.guard.signal(f"{self.name} exceeded {self.cfg.max_handshakes} handshakes")
                raise HandshakeCapExceeded(f"{self.name}: more than {self.cfg.max_handshakes} handshakes")
            with (self.guard.handshake_slot() if self.guard else contextlib.nullcontext()):
                self._handshaking = True
                try:
                    self._do_handshake()
                finally:
                    self._handshaking = False

    def _do_handshake(self) -> None:
        self.transport.reset()
        self.stats["handshakes"] += 1
        r = self._send("GET", self.index_url, headers={"Accept": "text/html,*/*"}, label="GET index")
        epoch = self.guard.epoch if self.guard else 0     # after any guard wait: this handshake is fresh
        token, comp = parsers.parse_index(r.text)
        if r.status != 200 or not token or not comp:
            raise HandshakeError(
                f"index page unusable (HTTP {r.status}, token={bool(token)}, comp={bool(comp)})")
        self.token = token
        self.components_url = urljoin(self.index_url, comp)
        r = self._send("GET", self.components_url, headers={"Referer": self.index_url, "Accept": "*/*"},
                       label="GET components")
        d, h = parsers.parse_components(r.text)
        if r.status != 200 or not d:
            raise HandshakeError("could not read delimeter from components.js")
        self.delimeter, self.hdr_name = d, h
        j = self.post_ajax("casestatus/set_data", {
            "complex_code": f"{self.loc.complex_code}@{self.loc.est_code}@{self.loc.differ_mast_est}",
            "selected_state_code": self.loc.state_code,
            "selected_dist_code": self.loc.dist_code,
            "selected_est_code": self.loc.est_code,
        }, recover=False)
        if str(j.get("status")) not in ("1", "True", "true"):
            raise HandshakeError(f"set_data rejected: {str(j)[:200]}")
        self.epoch = epoch
        self.ready = True

    def _is_ready(self) -> bool:
        return self.ready and (self.guard is None or self.epoch == self.guard.epoch)

    def ensure_ready(self) -> None:
        with self._lock:
            while not self._is_ready():
                try:
                    self._handshake_with_retries()
                except BlockSignal:
                    if not self.guard:
                        raise
                    # the guard is blocked now: the next handshake waits there (cooldown + single probe)
                    self.ready = False
                    self.stats["recoveries"] += 1

    def _handshake_with_retries(self) -> None:
        last: Optional[Exception] = None
        for attempt in range(1, self.cfg.handshake_attempts + 1):
            try:
                self.handshake()
                return
            except (HandshakeError, SessionInvalid, HttpFailure) as e:
                last = e
                log.warning("%s handshake attempt %d failed: %s", self.name, attempt, e)
                if self.guard and self.guard.blocked:
                    return          # a block signal arrived meanwhile: let the guard decide, no retry loop
                self._backoff(attempt)
        raise HandshakeError(f"handshake failed after {self.cfg.handshake_attempts} attempts: {last}")

    def recover(self) -> None:
        self.stats["recoveries"] += 1
        self.ready = False
        self.ensure_ready()

    # ------------------------------------------------------------------ requests
    def post_ajax(self, endpoint: str, data: Optional[Mapping[str, Any]] = None, recover: bool = True,
                  raw: bool = False) -> Dict[str, Any]:
        """POST ``?p=<endpoint>`` like the site's ajaxCall; returns parsed JSON and rotates the token.
        On invalid-session responses the session is re-handshaken and the call retried; on block signals
        everything waits in the shared guard first."""
        with self._lock:
            rec = 0
            problem = ""
            while True:
                if recover:
                    self.ensure_ready()
                items = list((data or {}).items()) + [("ajax_req", "true"), ("app_token", self.token)]
                try:
                    resp = self._send("POST", self.cfg.base_url + "?p=" + endpoint, data=urlencode(items),
                                      headers=self._ajax_headers(), label=endpoint.split("&")[0])
                except BlockSignal:
                    if not recover or not self.guard:
                        raise
                    self.ready = False
                    self.stats["recoveries"] += 1
                    continue
                problem, j = self._classify(resp)
                if problem is None:
                    self.token = j["app_token"]
                    return j
                log.warning("%s %s -> %s", self.name, endpoint, problem)
                if not recover or rec >= self.cfg.max_recover:
                    break
                rec += 1
                self.recover()
            raise SessionInvalid(f"{endpoint}: {problem}")

    def get(self, url: str, validator: Optional[Callable[[Response], bool]] = None,
            referer: Optional[str] = None) -> Response:
        """GET a resource of the same session (captcha image, PDF). Relative URLs resolve against the site."""
        with self._lock:
            self.ensure_ready()
            full = urljoin(self.cfg.base_url, url) if not url.startswith("http") else url
            label = "GET pdf" if "reports/" in full else ("GET captcha" if "securimage" in full else "GET other")
            try:
                resp = self._send("GET", full, headers={"Referer": referer or self.index_url, "Accept": "*/*"},
                                  label=label)
            except BlockSignal as e:
                self.ready = False
                raise BadContent(f"GET {full}: blocked ({e})")
            if resp.status in (401, 403, 405) or (validator is not None and not validator(resp)) \
                    or resp.status >= 400:
                bad = f"HTTP {resp.status}, {len(resp.content)} bytes"
                if resp.status in (401, 403, 405) or "Security Page" in (resp.text or "")[:2000]:
                    self.recover()
                raise BadContent(f"GET {full}: unexpected content ({bad})")
            return resp
