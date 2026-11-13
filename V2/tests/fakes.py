"""Offline fake of the eCourts site, replaying the real fixtures (no network)."""
from __future__ import annotations

import glob
import json
import os
import re
import secrets
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from V2.http import Response, TransportError

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
LIVE = os.path.join(FIX, "live")
BASE = "https://services.ecourts.gov.in/ecourtindia_v6/"
GOOD_CAPTCHA = "nhdmpk"


def fx(name: str, mode: str = "r"):
    with open(os.path.join(FIX, name), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def fxj(name: str):
    return json.loads(fx(name))


def live_html(cnr: str) -> str:
    with open(os.path.join(LIVE, cnr + ".html"), encoding="utf-8") as f:
        return f.read()


def live_cnrs() -> List[str]:
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(LIVE, "*.html")))


# SYNTHETIC (not captured from the live site): a viewIABusiness response that has business rows, shaped like the
# real zero-row response in fixtures/viewIABusiness.txt plus <tr><td> rows for the 4 header columns.
IA_BUSINESS_SYNTHETIC = (
    "<table class='table  table-bordered'>Case No:- EX/24/2023<tr>\n\t<th>IA No</th>\n\t<th>Business on Date</th>\n"
    "\t<th>Hearing Date</th>\n\t<th>Business</th>\n\t</tr>"
    "<tr><td>IA/1/2023</td><td>02-01-2023</td><td>16-01-2023</td><td>ISSUE NOTICE<br>TO RESPONDENT</td></tr>"
    "<tr><td>IA/1/2023</td><td>16-01-2023</td><td></td><td>Disposed</td></tr></table>")


class FakeEcourts:
    """Thread-safe server. ``D``/``H`` are the dynamic delimiter value / header name."""

    def __init__(self, D: str = "zz9delim", H: str = "Hdr77x", n_rows: int = 6):
        self.D, self.H = D, H
        self.lock = threading.Lock()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.log: List[Dict[str, Any]] = []
        self.faults: List[Any] = []            # consumed one per request: ("raise", exc) | ("status", code) | ("waf",)
        self.captcha_submits: List[str] = []
        self.n_rows = n_rows
        self.business_calls = 0
        self.ia_calls = 0
        self.ia_bodies: List[Dict[str, str]] = []
        self.fail_ia_for: set = set()          # cinoia values whose viewIABusiness always fails
        self.ia_with_rows: set = set()         # cinoia values served the SYNTHETIC with-rows IA response
        self.pdf_gets = 0
        self.blocked = False                   # WAF mode: every request -> HTTP 405 (228 bytes)
        self.block_after_business: Optional[int] = None   # start blocking at the Nth viewBusiness request
        self.unblock_after_index_probes: Optional[int] = None  # unblock when the Nth GET index arrives while blocked
        self.blocked_hits = 0
        self.index_gets_blocked = 0
        self._business_seen = 0
        self.fail_business_for: set = set()    # srno values that always return an application error
        self._index = fx("casestatus_index.html")
        self._js = fx("components.js").replace("fd794rfh", D).replace("Ajfeh753tr", H)
        self._ok = fxj("submit_case_type_ok.json")
        self._rej = fxj("submit_case_type_rejected.json")
        self._pdfs = [fx("order_0.pdf", "rb"), fx("order_1.pdf", "rb")]
        self._live = {c: live_html(c) for c in live_cnrs()}

    # ---- fault injection helpers
    def fail_next(self, n: int, exc: Optional[Exception] = None) -> None:
        self.faults += [("raise", exc or TransportError("RemoteDisconnected('Remote end closed connection')"))] * n

    def status_next(self, n: int, code: int = 503) -> None:
        self.faults += [("status", code)] * n

    def waf_next(self, n: int) -> None:
        self.faults += [("waf",)] * n

    def invalid_request_next(self, n: int) -> None:
        """Live 2026-11-13: a stale session gets JSON {errormsg: 'Oops ... Invalid Request...', app_token}."""
        self.faults += [("invalid",)] * n

    def kill_sessions(self) -> None:
        with self.lock:
            self.sessions.clear()

    def count(self, endpoint: str) -> int:
        return sum(1 for r in self.log if r["endpoint"] == endpoint)

    # ---- helpers
    def _search_html(self) -> str:
        html = self._ok["case_data"]
        rows = re.findall(r"<tr><td>\d+</td>.*?</td></tr>", html, re.S)[: self.n_rows]
        head = html[: html.index("<tr><td>1</td>")]
        return head + "".join(rows) + "</tbody></table>"

    def _json(self, sess, payload: Dict[str, Any]) -> Response:
        sess["token"] = secrets.token_hex(16)
        payload = dict(payload)
        payload["app_token"] = sess["token"]
        return Response(200, json.dumps(payload), headers={"Content-Type": "application/json"})

    @staticmethod
    def _html(text: str, status: int = 200) -> Response:
        return Response(status, text, text.encode(), {"Content-Type": "text/html"})

    # ---- core
    def handle(self, cookie: Optional[str], method: str, url: str, data: Optional[str], headers: Dict[str, str]):
        with self.lock:
            u = urlparse(url)
            q = parse_qs(u.query, keep_blank_values=True)
            p = (q.get("p") or [""])[0]
            # the real site keeps the cino etc. inside ?p=...&cino=; parse them into the endpoint's params
            endpoint = p
            self.log.append({"method": method, "endpoint": endpoint or u.path, "thread": threading.get_ident(),
                             "cookie": cookie})
            if (self.block_after_business is not None and endpoint == "home/viewBusiness"
                    and not self.blocked):
                self._business_seen += 1
                if self._business_seen >= self.block_after_business:
                    self.block_after_business = None
                    self.blocked = True
            if self.blocked:
                if method == "GET" and endpoint == "casestatus/index":
                    self.index_gets_blocked += 1
                    if (self.unblock_after_index_probes is not None
                            and self.index_gets_blocked >= self.unblock_after_index_probes):
                        self.blocked = False
                if self.blocked:
                    self.blocked_hits += 1
                    return None, Response(405, "x" * 228, b"x" * 228)
            if self.faults:
                f = self.faults.pop(0)
                if f[0] == "raise":
                    raise f[1]
                if f[0] == "status":
                    return None, Response(f[1], "busy")
                if f[0] == "waf":
                    return None, self._html("<html><title>Security Page</title>blocked</html>", 200)
                if f[0] == "invalid":
                    body = json.dumps({"errormsg": "<strong>Oops!</strong>There is something wrong.....!!!, "
                                       "Invalid Request...!Try once again", "app_token": "50c783ad51a9c87416"})
                    return None, Response(200, body, body.encode())
            if method == "GET" and endpoint == "casestatus/index":
                ck = secrets.token_hex(8)
                tok = secrets.token_hex(16)
                self.sessions[ck] = {"token": tok, "set_data": False, "pdfs": {}, "n_pdf": 0}
                page = re.sub(r"(id='app_token' value=\")[^\"]*", r"\g<1>" + tok, self._index)
                return ck, self._html(page)
            sess = self.sessions.get(cookie or "")
            if method == "GET" and u.path.endswith("components.js"):
                return cookie, Response(200, self._js, self._js.encode(), {"Content-Type": "application/javascript"})
            if sess is None:
                return cookie, self._html("<html>Search Page not Found</html>", 200)
            if method == "GET":
                if "securimage_show" in u.path:
                    img = fx("captcha_sample.png", "rb")
                    return cookie, Response(200, "", img, {"Content-Type": "image/png"})
                m = re.search(r"reports/[0-9a-f]+\.pdf", u.path)
                if m and m.group(0) in sess["pdfs"]:
                    self.pdf_gets += 1
                    c = self._pdfs[sess["pdfs"][m.group(0)] % 2]
                    return cookie, Response(200, "", c, {"Content-Type": "application/pdf"})
                return cookie, self._html("<html>Not found</html>", 404)
            # ---- ajax POST
            body = {k: v[0] for k, v in parse_qs(data or "", keep_blank_values=True).items()}
            hdr_ok = headers.get("delimeter") == self.D and headers.get(self.H) == self.D
            if not hdr_ok or body.get("app_token") != sess["token"] or headers.get("X-Requested-With") != "XMLHttpRequest":
                return cookie, self._html("<html><title>Security Page</title></html>", 200)
            base_ep = endpoint
            if base_ep == "casestatus/set_data":
                sess["set_data"] = True
                sess["loc"] = body
                return cookie, self._json(sess, fxj("set_data.txt") | {})
            if not sess["set_data"]:
                return cookie, self._html("<html>Search Page not Found</html>")
            if base_ep == "casestatus/getCaptcha":
                return cookie, self._json(sess, {"div_captcha": self._rej["div_captcha"], "status": 1})
            if base_ep == "casestatus/submit_case_type":
                self.captcha_submits.append(body.get("ct_captcha_code", ""))
                if body.get("ct_captcha_code") != GOOD_CAPTCHA:
                    return cookie, self._json(sess, {"errormsg": "Invalid Captcha... ", "case_data": "", "status": 0,
                                                     "div_captcha": self._rej["div_captcha"]})
                return cookie, self._json(sess, {"status": 1, "case_data": self._search_html(),
                                                 "div_captcha": self._rej["div_captcha"]})
            if base_ep == "home/viewHistory":
                cino = body.get("cino", "")
                page = self._live.get(cino)
                if page is None:
                    return cookie, self._json(sess, {"status": 0, "errormsg": "no such case"})
                return cookie, self._json(sess, {"data_list": page, "status": 1, "search_by": body.get("search_by")})
            if base_ep == "home/viewBusiness":
                self.business_calls += 1
                if body.get("srno") in self.fail_business_for:
                    return cookie, self._json(sess, {"status": 0})
                d = fxj("viewBusiness.txt")["data_list"].replace("20-08-2025", body.get("businessDate", ""))
                return cookie, self._json(sess, {"data_list": d, "status": 1})
            if base_ep == "home/viewIABusiness":
                self.ia_calls += 1
                self.ia_bodies.append(body)
                if body.get("cinoia") in self.fail_ia_for:
                    return cookie, self._json(sess, {"status": 0})
                if body.get("cinoia") in self.ia_with_rows:
                    return cookie, self._json(sess, {"data_list": IA_BUSINESS_SYNTHETIC, "status": 1})
                return cookie, self._json(sess, fxj("viewIABusiness.txt") | {})
            if base_ep == "home/display_pdf":
                path = f"reports/{secrets.token_hex(16)}.pdf"
                sess["pdfs"][path] = sess["n_pdf"]
                sess["n_pdf"] += 1
                return cookie, self._json(sess, {"order": path, "status": 1})
            if base_ep.startswith("home/case_acknowlegement"):
                return cookie, self._json(sess, {"ack_details": fxj("ack.txt")["ack_details"], "status": 1})
            if base_ep.startswith("cnr_status/viewCNRHistory"):
                return cookie, self._json(sess, {"casetype_list": fxj("viewCNRHistory.txt")["casetype_list"].replace(
                                                     "KABC010342962022", body.get("cino", "")),
                                                 "status": 1})
            return cookie, self._json(sess, {"status": 0, "errormsg": "unknown endpoint " + base_ep})


class FakeTransport:
    """Per-session transport against a shared FakeEcourts. Mimics requests-style cookie handling."""

    def __init__(self, server: FakeEcourts):
        self.server = server
        self.cookie: Optional[str] = None
        self.resets = 0

    def reset(self) -> None:
        self.cookie = None
        self.resets += 1

    def request(self, method, url, data=None, headers=None):
        ck, resp = self.server.handle(self.cookie, method, url, data, headers or {})
        if ck is not None:
            self.cookie = ck
        return resp
