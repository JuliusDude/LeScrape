import os
import tempfile
import unittest

from V2 import pdf as pdfmod
from V2.captcha import (CaptchaFailed, CallableSolver, ManualSolver, preprocess_variants, sane,
                                    solve_with_retries)
from V2.config import Config, Location, SearchSpec
from V2.http import EcourtsSession
from V2.search import SearchError, run_search
from V2.tests.fakes import FakeEcourts, FakeTransport, fx, GOOD_CAPTCHA


class TestCaptchaLoop(unittest.TestCase):
    def test_sane(self):
        self.assertTrue(sane("nhdmpk"))
        for bad in ("", "abc", "NHDMPK", "nhd mpk", "nhdmpk1"):
            self.assertFalse(sane(bad), bad)

    def test_retries_until_accepted(self):
        texts = iter(["aaaaaa", "bbbbbb", "nhdmpk"])
        submitted = []

        def submit(t):
            submitted.append(t)
            return t == "nhdmpk", "RESULT"

        res, n = solve_with_retries(lambda: b"img", CallableSolver(lambda b: next(texts)), submit, attempts=5)
        self.assertEqual((res, n), ("RESULT", 3))
        self.assertEqual(submitted, ["aaaaaa", "bbbbbb", "nhdmpk"])

    def test_insane_ocr_not_submitted(self):
        texts = iter(["", "ab", "abcdef"])
        submitted = []
        res, n = solve_with_retries(lambda: b"x", CallableSolver(lambda b: next(texts)),
                                    lambda t: (submitted.append(t) or True, 1), attempts=5)
        self.assertEqual(submitted, ["abcdef"])
        self.assertEqual(n, 3)

    def test_fetch_error_counts_as_attempt(self):
        calls = [0]

        def fetch():
            calls[0] += 1
            if calls[0] < 3:
                raise RuntimeError("boom")
            return b"img"

        res, n = solve_with_retries(fetch, CallableSolver(lambda b: "abcdef"), lambda t: (True, "ok"), attempts=5)
        self.assertEqual(n, 3)

    def test_gives_up(self):
        with self.assertRaises(CaptchaFailed):
            solve_with_retries(lambda: b"x", CallableSolver(lambda b: "abcdef"), lambda t: (False, None), attempts=4)

    def test_manual_solver(self):
        with tempfile.TemporaryDirectory() as td:
            ms = ManualSolver(os.path.join(td, "c.png"), prompt=lambda msg: " ABCDEF ")
            self.assertEqual(ms.solve(b"png"), "abcdef")
            self.assertTrue(os.path.exists(os.path.join(td, "c.png")))

    def test_preprocess_variants_shapes(self):
        v = preprocess_variants(fx("captcha_sample.png", "rb"))
        self.assertEqual(len(v), 3)
        raw_h = v[1].shape[0]                                # order: otsu 2x, raw, autocontrast 3x
        self.assertEqual(v[0].shape[0], raw_h * 2)
        self.assertEqual(v[2].shape[0], raw_h * 3)

class TestSearchFlow(unittest.TestCase):
    def setUp(self):
        self.server = FakeEcourts(n_rows=8)
        self.cfg = Config(captcha_attempts=6)
        self.s = EcourtsSession(self.cfg, Location(), FakeTransport(self.server), sleep=lambda d: None)

    def test_rejections_then_success(self):
        texts = iter(["wrongg", "bad123", GOOD_CAPTCHA])
        res = run_search(self.s, CallableSolver(lambda b: next(texts)), Location(), SearchSpec(), self.cfg)
        self.assertEqual(res["attempts"], 3)
        self.assertEqual(len(res["rows"]), 8)
        self.assertEqual(res["total"], 2344)
        self.assertEqual(self.server.captcha_submits, ["wrongg", "bad123", GOOD_CAPTCHA])
        # first captcha via getCaptcha; the two re-tries reuse the fresh captcha sent with the rejection
        self.assertEqual(self.server.count("casestatus/getCaptcha"), 1)
        self.assertEqual(res["rows"][0]["view_params"]["cino"], "KABC010342242022")

    def test_captcha_exhausted(self):
        cfg = Config(captcha_attempts=3)
        with self.assertRaises(CaptchaFailed):
            run_search(self.s, CallableSolver(lambda b: "zzzzzz"), Location(), SearchSpec(), cfg)
        self.assertEqual(len(self.server.captcha_submits), 3)

    def test_search_survives_waf_during_captcha(self):
        self.s.handshake()
        self.server.waf_next(1)
        res = run_search(self.s, CallableSolver(lambda b: GOOD_CAPTCHA), Location(), SearchSpec(), self.cfg)
        self.assertEqual(len(res["rows"]), 8)
        self.assertEqual(self.s.stats["recoveries"], 1)

    @unittest.skipUnless(os.environ.get("ALT_SLOW", "1") == "1", "slow OCR test disabled")
    def test_real_ocr_on_sample(self):
        try:
            import easyocr  # noqa: F401
        except ImportError:
            self.skipTest("easyocr unavailable")
        from V2.captcha import EasyOcrSolver
        self.assertEqual(EasyOcrSolver().solve(fx("captcha_sample.png", "rb")), "nhdmpk")


class TestPdf(unittest.TestCase):
    def test_order_0_text_layer(self):
        info = pdfmod.extract_text(fx("order_0.pdf", "rb"))
        self.assertEqual(info["pages"], 8)
        self.assertEqual(info["needs_ocr"], 0)
        self.assertGreater(len(info["text"].strip()), 500)
        self.assertEqual(info["text"].count("\f"), 7)

    def test_order_1(self):
        info = pdfmod.extract_text(fx("order_1.pdf", "rb"))
        self.assertGreaterEqual(info["pages"], 1)
        self.assertEqual(info["needs_ocr"], 0)

    def test_no_text_layer_flagged(self):
        import pymupdf
        doc = pymupdf.open()
        doc.new_page()
        doc.new_page()
        blank = doc.tobytes()
        info = pdfmod.extract_text(blank)
        self.assertEqual((info["pages"], info["needs_ocr"]), (2, 1))

    def test_corrupt_pdf(self):
        info = pdfmod.extract_text(b"%PDF-1.4 garbage")
        self.assertEqual(info["needs_ocr"], 1)
        self.assertTrue(info["error"] or info["pages"] == 0)
        self.assertFalse(pdfmod.is_pdf(b"<html>Security Page</html>"))
        self.assertTrue(pdfmod.is_pdf(fx("order_0.pdf", "rb")))

    def test_save_by_sha_idempotent(self):
        data = fx("order_0.pdf", "rb")
        with tempfile.TemporaryDirectory() as td:
            a = pdfmod.save_pdf(data, td)
            b = pdfmod.save_pdf(data, td)
            self.assertEqual(a["path"], b["path"])
            self.assertEqual(a["size"], len(data))
            self.assertEqual(a["pages"], 8)
            with open(a["path"], "rb") as f:
                self.assertEqual(f.read(), data)
            self.assertEqual(len(a["sha256"]), 64)
            self.assertIn(a["sha256"], os.path.basename(a["path"]))


if __name__ == "__main__":
    unittest.main()


class TestPdfConcurrency(unittest.TestCase):
    def test_same_pdf_saved_by_many_threads(self):
        import threading
        data = fx("order_0.pdf", "rb")
        errs = []
        with tempfile.TemporaryDirectory() as td:
            def work():
                try:
                    for _ in range(20):
                        pdfmod.save_pdf(data, td)
                except Exception as e:  # noqa: BLE001
                    errs.append(e)
            ts = [threading.Thread(target=work) for _ in range(6)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(errs, [])
            leftovers = [f for d, _, fs in os.walk(td) for f in fs if f.endswith(".tmp")]
            self.assertEqual(leftovers, [])


class TestCli(unittest.TestCase):
    def test_parser_defaults(self):
        from V2.__main__ import build_parser
        a = build_parser().parse_args(["run", "--limit", "10", "--workers", "4", "--out", "x"])
        self.assertEqual((a.max_rps, a.workers, a.limit, a.case_type, a.year, a.status),
                         (1.0, 4, 10, "23^3", "2023", "Disposed"))
        d = build_parser().parse_args(["run"])
        self.assertEqual((d.workers, d.max_rps, d.auto_tune, d.max_block_min), (1, 1.0, False, 30.0))


class TestCaptchaBenchmark(unittest.TestCase):
    """Real live captchas hand-labelled by the lead (tests/fixtures/captcha_bench/labels.json)."""

    def test_solver_accuracy_on_labelled_live_captchas(self):
        try:
            import easyocr  # noqa: F401
        except ImportError:
            self.skipTest("easyocr not installed")
        import json as _json
        from V2.captcha import EasyOcrSolver
        d = os.path.join(os.path.dirname(__file__), "fixtures", "captcha_bench")
        labels = _json.load(open(os.path.join(d, "labels.json")))
        solver = EasyOcrSolver()
        ok = sum(solver.solve(open(os.path.join(d, k), "rb").read()) == v for k, v in labels.items())
        self.assertGreaterEqual(ok, 12, f"only {ok}/{len(labels)} captchas solved")

    def test_rejected_captcha_image_saved_for_diagnosis(self):
        import tempfile
        from V2.captcha import CallableSolver, solve_with_retries
        with tempfile.TemporaryDirectory() as td:
            res = iter([(False, None), (True, "ok")])
            out, n = solve_with_retries(lambda: b"IMG", CallableSolver(lambda b: "abc123"),
                                        lambda t: next(res), attempts=3, debug_dir=td)
            self.assertEqual((out, n), ("ok", 2))
            self.assertEqual(os.listdir(td), ["rejected_01_abc123.png"])
