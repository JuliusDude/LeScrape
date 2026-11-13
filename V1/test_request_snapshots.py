"""
V1.test_request_snapshots
==================================
Request-construction regression test (PLAN section 3.5 rule).

Patches ``V1.scraper.subprocess.run`` with a scripted fake that records
the exact curl argv for every call, then compares it to a baseline generated from
the UNMODIFIED Phase-0 code (``fixtures/snapshots/request_baseline.json``).

The ONLY approved future additions to argv are these three flag/value pairs
(6 tokens) and they are removed by ``strip_approved_flags`` before comparing:
    --connect-timeout <n>
    --max-time <n>
    -w <fmt>

Regenerate the baseline (only from known-good code!) with:
    DEEPER_REGEN_SNAPSHOTS=1 python -m unittest V1.test_request_snapshots

Fully offline: no network is ever touched (subprocess.run is faked).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from V1 import scraper as scraper_mod
    _IMPORT_ERROR = None
except Exception as exc:  # requests/PIL/cv2 etc. missing
    scraper_mod = None
    _IMPORT_ERROR = exc

HERE = os.path.dirname(os.path.abspath(__file__))
SYN = os.path.join(HERE, "fixtures", "synthetic")
BASELINE = os.path.join(HERE, "fixtures", "snapshots", "request_baseline.json")

APPROVED_PAIRS = ("--connect-timeout", "--max-time", "-w")


def strip_approved_flags(argv):
    """Remove the only approved argv additions: ``--connect-timeout n``,
    ``--max-time n`` and ``-w fmt`` (3 pairs = 6 tokens). Nothing else may differ."""
    out = []
    i = 0
    while i < len(argv):
        if argv[i] in APPROVED_PAIRS and i + 1 < len(argv):
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def _read(name):
    with open(os.path.join(SYN, name), "r", encoding="utf-8") as f:
        return f.read()


class FakeTime:
    """Replaces the scraper's ``time`` module so pacing never sleeps."""

    def __init__(self):
        self._now = 1000.0

    def time(self):
        self._now += 100.0
        return self._now

    def sleep(self, _s):
        pass


class StubSolver:
    def __init__(self):
        self.calls = []

    def solve(self, img_bytes):
        self.calls.append(img_bytes)
        return "abc123"


CAPTCHA_JSON = json.dumps({
    "div_captcha": '<div><img id="captcha_image" src="\\/ecourtindia_v6\\/vendor\\/securimage\\/securimage_show.php?deadbeef" /></div>',
    "app_token": "tokCAP",
    "status": 1,
})
INDEX_HTML = (
    '<html><head><script src="/ecourtindia_v6/js/components.js?v=1"></script></head>'
    '<body><input type="hidden" id="app_token" name="app_token" value="TOKEN1"></body></html>'
)
COMPONENTS_JS = (
    'var delimeter = "dlm123";\n'
    '$.ajax({ headers: {"delimeter": delimeter, "Xcustom": delimeter} });\n'
)
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nFAKEIMAGE\x00\xff"


def route(argv):
    """Scripted portal. Returns stdout bytes for the call."""
    url = argv[-1]
    if url.endswith("securimage_show.php?deadbeef"):
        return IMAGE_BYTES
    if "components.js" in url:
        return COMPONENTS_JS.encode()
    if "?p=casestatus/index" in url and "-X" not in argv:
        return INDEX_HTML.encode()
    if "casestatus/getCaptcha" in url:
        return CAPTCHA_JSON.encode()
    if "casestatus/set_data" in url:
        return json.dumps({"status": 1, "app_token": "tokSD"}).encode()
    if "casestatus/submit_case_type" in url:
        return _read("search_three_rows.json").encode()
    if "home/viewHistory" in url:
        return _read("viewhistory_ok.json").encode()
    return b"<html>home</html>"


class Recorder:
    def __init__(self, cookie_file, db_path):
        self.cookie_file = cookie_file
        self.db_path = db_path
        self.calls = []
        self.phase = ""

    def run(self, cmd, *args, **kwargs):
        argv = list(cmd)
        stdout = route(argv)
        self.calls.append({"phase": self.phase, "argv": argv})
        # Future code may append ``-w "\n%{http_code}"``: mimic curl for it.
        if "-w" in argv:
            fmt = argv[argv.index("-w") + 1]
            stdout = stdout + fmt.replace("\\n", "\n").replace("%{http_code}", "200").encode()
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")

    def normalised(self):
        out = []
        for c in self.calls:
            argv = [
                a.replace(self.cookie_file, "<COOKIE>") for a in c["argv"]
            ]
            argv = strip_approved_flags(argv)
            form, headers = [], []
            for i, tok in enumerate(argv):
                if tok == "-d":
                    form = [list(p) for p in urllib.parse.parse_qsl(argv[i + 1], keep_blank_values=True)]
                elif tok == "-H":
                    headers.append(argv[i + 1])
            out.append({"phase": c["phase"], "argv": argv, "form": form, "headers": headers})
        return out


@unittest.skipIf(scraper_mod is None, "V1.scraper deps unavailable: %s" % _IMPORT_ERROR)
class TestRequestSnapshots(unittest.TestCase):
    def test_request_construction_matches_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie = os.path.join(tmp, "cookies.txt")
            db_path = os.path.join(tmp, "t.db")
            rec = Recorder(cookie, db_path)
            returns = {}

            with mock.patch.object(scraper_mod.subprocess, "run", rec.run), \
                    mock.patch.object(scraper_mod, "time", FakeTime()):
                sc = scraper_mod.DeeperScraper(db_path=db_path, cookie_file=cookie)
                sc.solver = StubSolver()

                rec.phase = "init_session"
                sc.init_session()
                self.assertEqual(sc.app_token, "TOKEN1")
                self.assertEqual(sc.delimeter, "dlm123")
                self.assertEqual(sc.custom_header, "Xcustom")
                returns["init_session"] = {
                    "app_token": sc.app_token, "delimeter": sc.delimeter, "custom_header": sc.custom_header,
                }

                rec.phase = "set_establishment"
                sc.set_establishment("1030135", "3", "20", "3")
                self.assertEqual(sc.app_token, "tokSD")  # token rotated from response

                rec.phase = "solve_captcha"
                sol = sc.solve_captcha()
                self.assertEqual(sol, "abc123")
                self.assertEqual(sc.app_token, "tokCAP")
                self.assertEqual(sc.solver.calls[-1], IMAGE_BYTES)
                returns["solve_captcha"] = sol

                rec.phase = "search_cases"
                rows = sc.search_cases("12^3", "2024", case_status="Pending")
                self.assertEqual(len(rows), 3)
                self.assertEqual(sc.app_token, "tok_search")
                self.assertEqual(rows[0]["view_params"]["cino"], "KABC010000012024")
                returns["search_cases"] = {
                    "count": len(rows), "cnrs": [r["cnr_number"] for r in rows],
                    "court_names": [r["court_name"] for r in rows],
                }

                rec.phase = "fetch_view_history"
                vp = rows[0]["view_params"]
                html = sc.fetch_view_history(vp)
                self.assertEqual(sc.app_token, "tok2")
                with open(os.path.join(HERE, "fixtures", "target_case_view.html"), "r", encoding="utf-8") as f:
                    expected_html = f.read()
                self.assertIsInstance(html, str)
                self.assertEqual(html, expected_html)
                returns["fetch_view_history"] = {"len": len(html), "type": "str"}

                rec.phase = "_curl_binary"
                b = sc._curl("https://services.ecourts.gov.in/ecourtindia_v6/vendor/securimage/securimage_show.php?deadbeef",
                             headers={"Referer": "https://example.invalid/"}, is_binary=True)
                self.assertEqual(b, IMAGE_BYTES)
                self.assertIsInstance(b, bytes)

                rec.phase = "_curl_text_post"
                t = sc._curl("https://services.ecourts.gov.in/ecourtindia_v6/?p=casestatus/set_data",
                             data={"a": "1", "b": "x y"})
                self.assertIsInstance(t, str)

            current = {"calls": rec.normalised(), "returns": returns}

            if os.environ.get("DEEPER_REGEN_SNAPSHOTS") == "1":
                os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
                with open(BASELINE, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(current, f, indent=2, ensure_ascii=False)
                    f.write("\n")

            with open(BASELINE, "r", encoding="utf-8") as f:
                baseline = json.load(f)

            self.assertEqual([c["phase"] for c in current["calls"]], [c["phase"] for c in baseline["calls"]])
            for cur, base in zip(current["calls"], baseline["calls"]):
                self.assertEqual(cur["argv"], base["argv"], "argv changed in phase %s" % base["phase"])
                self.assertEqual(cur["form"], base["form"], "form body changed in phase %s" % base["phase"])
                self.assertEqual(cur["headers"], base["headers"], "headers changed in phase %s" % base["phase"])
            self.assertEqual(current["returns"], baseline["returns"])

    def test_strip_approved_flags_only_removes_three_pairs(self):
        argv = ["curl.exe", "-s", "--connect-timeout", "15", "--max-time", "60", "-w", "\n%{http_code}", "-b", "c", "u"]
        self.assertEqual(strip_approved_flags(argv), ["curl.exe", "-s", "-b", "c", "u"])
        self.assertEqual(strip_approved_flags(["curl.exe", "-s", "u"]), ["curl.exe", "-s", "u"])


if __name__ == "__main__":
    unittest.main()
