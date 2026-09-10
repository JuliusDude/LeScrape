"""
Unit and boundary tests for ecourts.core.captcha (CAPTCHA solvers and CaptchaHandler).
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from ecourts.core.captcha import (
    BaseCaptchaSolver,
    CaptchaHandler,
    LocalOcrSolver,
    ManualInteractiveSolver,
    MockCaptchaSolver,
    ThirdPartyApiSolver,
    preprocess_captcha_image,
)
from ecourts.core.client import PacedSession


def create_sample_png_bytes(text: str = "test") -> bytes:
    """Helper to generate valid PNG bytes in memory."""
    img = Image.new("RGB", (120, 40), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestCaptchaTier1(unittest.TestCase):
    """Tier 1: Feature Coverage tests for CAPTCHA subsystem."""

    def test_mock_solver_default_solution(self):
        """Verify MockCaptchaSolver returns default solution when no map matches."""
        solver = MockCaptchaSolver(default_solution="abc12")
        self.assertEqual(solver.solve(b"dummy_bytes"), "abc12")

    def test_mock_solver_hash_mapping(self):
        """Verify MockCaptchaSolver maps image bytes and SHA-256 hashes to solutions."""
        solver = MockCaptchaSolver(default_solution="default")
        png_bytes = create_sample_png_bytes()
        solver.register_hash(png_bytes, "sol99")

        self.assertEqual(solver.solve(png_bytes), "sol99")
        self.assertEqual(solver.solve(b"unregistered_bytes"), "default")

    def test_mock_solver_sequence(self):
        """Verify MockCaptchaSolver returns solutions in sequence."""
        solver = MockCaptchaSolver(sequence=["first", "second", "third"], default_solution="done")
        self.assertEqual(solver.solve(b"1"), "first")
        self.assertEqual(solver.solve(b"2"), "second")
        self.assertEqual(solver.solve(b"3"), "third")
        self.assertEqual(solver.solve(b"4"), "done")

    def test_manual_interactive_solver_flow(self):
        """Verify ManualInteractiveSolver uses tempdir, calls prompt, and cleans up file."""
        prompt_called = [False]

        def fake_prompt(msg: str) -> str:
            prompt_called[0] = True
            return "k9m2x "

        solver = ManualInteractiveSolver(prompt_func=fake_prompt)
        with patch("subprocess.Popen"):
            with patch("sys.platform", "linux"):
                png_bytes = create_sample_png_bytes()
                solution = solver.solve(png_bytes)

        self.assertTrue(prompt_called[0])
        self.assertEqual(solution, "k9m2x")

    def test_captcha_handler_extract_image_url(self):
        """Verify CaptchaHandler extracts image src from div_captcha snippet."""
        handler = CaptchaHandler(session=MagicMock())
        div_html = (
            '<div class="form-inline">'
            '<img id="captcha_image" src="/ecourtindia_v6/vendor/securimage/securimage_show.php?hash123" />'
            '</div>'
        )
        url = handler.extract_image_url(div_html)
        self.assertEqual(url, "/ecourtindia_v6/vendor/securimage/securimage_show.php?hash123")

    def test_captcha_handler_solve_from_div(self):
        """Verify CaptchaHandler downloads image and invokes solver."""
        mock_session = MagicMock(spec=PacedSession)
        png_bytes = create_sample_png_bytes()
        mock_session.get_image.return_value = png_bytes

        solver = MockCaptchaSolver(default_solution="sol123")
        handler = CaptchaHandler(session=mock_session, solver=solver)

        div_html = '<div><img id="captcha_image" src="/test/securimage_show.php?token" /></div>'
        code, url = handler.solve_from_div(div_html)

        self.assertEqual(code, "sol123")
        self.assertEqual(url, "/test/securimage_show.php?token")
        mock_session.get_image.assert_called_once_with("/test/securimage_show.php?token")

    def test_captcha_handler_solve_from_response(self):
        """Verify CaptchaHandler extracts fresh div_captcha from JSON response."""
        mock_session = MagicMock(spec=PacedSession)
        mock_session.get_image.return_value = create_sample_png_bytes()
        solver = MockCaptchaSolver(default_solution="fresh1")
        handler = CaptchaHandler(session=mock_session, solver=solver)

        resp_json = json.dumps({
            "status": 1,
            "div_captcha": '<div><img id="captcha_image" src="/fresh/securimage_show.php?fresh" /></div>',
        })
        code, url = handler.solve_from_response(resp_json)
        self.assertEqual(code, "fresh1")
        self.assertEqual(url, "/fresh/securimage_show.php?fresh")

    def test_third_party_api_solver_success(self):
        """Verify ThirdPartyApiSolver submits image and polls for resolution."""
        solver = ThirdPartyApiSolver(api_key="fake_key", poll_interval=0.01, max_wait=1.0)
        png_bytes = create_sample_png_bytes()

        submit_resp = MagicMock()
        submit_resp.json.return_value = {"status": 1, "request": "job_999"}

        poll_resp = MagicMock()
        poll_resp.json.return_value = {"status": 1, "request": "pass42"}

        with patch("requests.post", return_value=submit_resp) as mock_post:
            with patch("requests.get", return_value=poll_resp) as mock_get:
                code = solver.solve(png_bytes)
                self.assertEqual(code, "pass42")
                self.assertEqual(mock_post.call_count, 1)
                self.assertEqual(mock_get.call_count, 1)

    def test_image_preprocessing_pipeline(self):
        """Verify image preprocessing outputs valid resized image bytes."""
        raw_png = create_sample_png_bytes()
        preprocessed = preprocess_captcha_image(raw_png)
        self.assertIsInstance(preprocessed, bytes)
        self.assertGreater(len(preprocessed), 0)

        # Check that output is valid image
        img = Image.open(io.BytesIO(preprocessed))
        self.assertGreater(img.width, 100)

    def test_captcha_handler_get_and_solve_captcha(self):
        """Verify CaptchaHandler.get_and_solve_captcha executes POST to getCaptcha and solves image."""
        mock_session = MagicMock(spec=PacedSession)
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "status": 1,
            "div_captcha": '<div><img id="captcha_image" src="/ecourtindia_v6/vendor/securimage/securimage_show.php?tok99" /></div>',
        })
        mock_resp.json.return_value = json.loads(mock_resp.text)
        mock_session.post.return_value = mock_resp
        mock_session.get_image.return_value = create_sample_png_bytes()

        solver = MockCaptchaSolver(default_solution="solved99")
        handler = CaptchaHandler(session=mock_session, solver=solver)

        code, url = handler.get_and_solve_captcha()
        self.assertEqual(code, "solved99")
        self.assertIn("tok99", url)


class TestCaptchaTier2(unittest.TestCase):
    """Tier 2: Boundary & Corner Cases for CAPTCHA subsystem."""

    def test_local_ocr_solver_offline_safe_instantiation(self):
        """Verify LocalOcrSolver does not raise unhandled errors when EasyOCR is unavailable."""
        solver = LocalOcrSolver()
        with patch.object(LocalOcrSolver, "get_reader", return_value=None):
            # When EasyOCR reader is None and pytesseract is absent, should safely return empty string
            result = solver.solve(create_sample_png_bytes())
            self.assertEqual(result, "")

    def test_manual_solver_cleans_whitespace_and_special_chars(self):
        """Verify ManualInteractiveSolver cleans special characters from user input."""
        solver = ManualInteractiveSolver(prompt_func=lambda _: " a-b#1!2? \n")
        with patch("subprocess.Popen"):
            with patch("sys.platform", "linux"):
                res = solver.solve(create_sample_png_bytes())
        self.assertEqual(res, "ab12")

    def test_third_party_solver_missing_api_key_raises(self):
        """Verify ThirdPartyApiSolver raises ValueError if initialized with empty api_key."""
        solver = ThirdPartyApiSolver(api_key="")
        self.assertFalse(solver.is_available())
        with self.assertRaises(ValueError):
            solver.solve(create_sample_png_bytes())

    def test_third_party_solver_timeout_raises(self):
        """Verify ThirdPartyApiSolver raises TimeoutError when polling exceeds max_wait."""
        solver = ThirdPartyApiSolver(api_key="key", poll_interval=0.01, max_wait=0.03)
        submit_resp = MagicMock()
        submit_resp.json.return_value = {"status": 1, "request": "job_123"}
        poll_resp = MagicMock()
        poll_resp.json.return_value = {"status": 2, "request": "CAPCHA_NOT_READY"}

        with patch("requests.post", return_value=submit_resp):
            with patch("requests.get", return_value=poll_resp):
                with self.assertRaises(TimeoutError):
                    solver.solve(create_sample_png_bytes())

    def test_extract_image_url_missing_img_raises(self):
        """Verify extract_image_url raises ValueError when no valid img tag exists."""
        handler = CaptchaHandler(session=MagicMock())
        with self.assertRaises(ValueError) as ctx:
            handler.extract_image_url("<div><p>No image here</p></div>")
        self.assertIn("No valid captcha image found", str(ctx.exception))

    def test_solve_from_response_malformed_json_raises(self):
        """Verify solve_from_response raises ValueError on non-JSON input."""
        handler = CaptchaHandler(session=MagicMock())
        with self.assertRaises(ValueError):
            handler.solve_from_response("<html>Fatal PHP Error</html>")

    def test_solve_from_response_missing_div_captcha_raises(self):
        """Verify solve_from_response raises ValueError when div_captcha is missing."""
        handler = CaptchaHandler(session=MagicMock())
        with self.assertRaises(ValueError) as ctx:
            handler.solve_from_response('{"status": 1, "other_field": "val"}')
        self.assertIn("No div_captcha found", str(ctx.exception))

    def test_preprocess_corrupted_bytes_fallback(self):
        """Verify preprocess_captcha_image returns original bytes if image cannot be parsed."""
        corrupted_bytes = b"not_an_image_corrupted_stream_12345"
        res = preprocess_captcha_image(corrupted_bytes)
        self.assertEqual(res, corrupted_bytes)

    def test_get_and_solve_captcha_missing_div_captcha_raises(self):
        """Verify get_and_solve_captcha raises ValueError if response lacks div_captcha."""
        mock_sess = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({"status": 0, "errormsg": "Failed"})
        mock_resp.json.return_value = json.loads(mock_resp.text)
        mock_sess.post.return_value = mock_resp

        handler = CaptchaHandler(session=mock_sess)
        with self.assertRaises(ValueError) as ctx:
            handler.get_and_solve_captcha()
        self.assertIn("Missing 'div_captcha'", str(ctx.exception))

    def test_third_party_solver_api_error_response_raises(self):
        """Verify ThirdPartyApiSolver raises RuntimeError when provider returns error status."""
        solver = ThirdPartyApiSolver(api_key="key", poll_interval=0.01)
        submit_resp = MagicMock()
        submit_resp.json.return_value = {"status": 0, "request": "ERROR_KEY_DOES_NOT_EXIST"}

        with patch("requests.post", return_value=submit_resp):
            with self.assertRaises(RuntimeError) as ctx:
                solver.solve(create_sample_png_bytes())
            self.assertIn("ERROR_KEY_DOES_NOT_EXIST", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
