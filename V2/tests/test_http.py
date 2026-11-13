import threading
import time
import unittest

from V2.config import Config, Location
from V2.http import (BadContent, EcourtsSession, HandshakeError, HttpFailure, SessionInvalid,
                                 TransportError)
from V2.ratelimit import ConcurrencyGate, Governor, RateLimiter
from V2.tests.fakes import FakeEcourts, FakeTransport


def make(server=None, cfg=None, **kw):
    server = server or FakeEcourts()
    sleeps = []
    sess = EcourtsSession(cfg or Config(), Location(), FakeTransport(server), sleep=sleeps.append,
                          rng=lambda: 1.0, **kw)
    return server, sess, sleeps


class TestHandshake(unittest.TestCase):
    def test_dynamic_delimiter_and_header(self):
        for D, H in (("abc123", "Xhdr1"), ("qqq999zz", "Other77")):
            server = FakeEcourts(D=D, H=H)
            _, s, _ = make(server)
            s.handshake()
            self.assertEqual((s.delimeter, s.hdr_name), (D, H))
            self.assertTrue(s.ready)
            j = s.post_ajax("casestatus/getCaptcha", {})
            self.assertIn("div_captcha", j)
        self.assertEqual(server.count("casestatus/set_data"), 1)

    def test_set_data_body(self):
        server, s, _ = make()
        s.handshake()
        body = server.sessions[s.transport.cookie]["loc"]
        self.assertEqual(body["complex_code"], "1030135@3@Y")
        self.assertEqual((body["selected_state_code"], body["selected_dist_code"], body["selected_est_code"]),
                         ("3", "20", "3"))

    def test_token_rotation(self):
        server, s, _ = make()
        s.handshake()
        seen = [s.token]
        for _ in range(4):
            s.post_ajax("casestatus/getCaptcha", {})
            seen.append(s.token)
        self.assertEqual(len(set(seen)), 5)           # a new token after every response
        self.assertEqual(server.sessions[s.transport.cookie]["token"], s.token)

    def test_handshake_failure_after_attempts(self):
        server, s, sleeps = make(cfg=Config(handshake_attempts=2, max_retries=1))
        server.fail_next(1000)
        with self.assertRaises(HandshakeError):
            s.ensure_ready()
        self.assertFalse(s.ready)


class TestRetriesAndRecovery(unittest.TestCase):
    def test_retry_on_remote_disconnected(self):
        server, s, sleeps = make()
        s.handshake()
        server.fail_next(2)
        j = s.post_ajax("casestatus/getCaptcha", {})
        self.assertIn("div_captcha", j)
        self.assertEqual(s.stats["retries"], 2)
        self.assertEqual(len(sleeps), 2)
        self.assertLess(sleeps[0], sleeps[1])          # exponential backoff

    def test_gives_up_after_max_retries(self):
        server, s, _ = make(cfg=Config(max_retries=2))
        s.handshake()
        server.fail_next(50)
        with self.assertRaises(HttpFailure):
            s.post_ajax("casestatus/getCaptcha", {})

    def test_503_then_ok(self):
        server, s, sleeps = make()
        s.handshake()
        server.status_next(2, 503)
        self.assertIn("div_captcha", s.post_ajax("casestatus/getCaptcha", {}))
        self.assertEqual(len(sleeps), 2)

    def test_waf_page_triggers_rehandshake_and_retry(self):
        server, s, _ = make()
        s.handshake()
        first_cookie = s.transport.cookie
        server.waf_next(1)
        j = s.post_ajax("casestatus/getCaptcha", {})
        self.assertIn("div_captcha", j)
        self.assertEqual(s.stats["recoveries"], 1)
        self.assertEqual(s.stats["handshakes"], 2)
        self.assertNotEqual(s.transport.cookie, first_cookie)       # fresh cookie jar
        self.assertEqual(s.transport.resets, 2)

    def test_invalid_request_errormsg_rehandshakes(self):
        # Live 2026-11-13: one worker's session answered "Invalid Request" to every viewHistory,
        # failing 4 cases. It must be treated as a stale session: re-handshake and retry.
        server, s, _ = make()
        s.handshake()
        server.invalid_request_next(1)
        j = s.post_ajax("casestatus/getCaptcha", {})
        self.assertIn("div_captcha", j)
        self.assertEqual(s.stats["recoveries"], 1)
        self.assertEqual(server.count("casestatus/set_data"), 2)

    def test_server_side_session_loss(self):
        server, s, _ = make()
        s.handshake()
        server.kill_sessions()
        j = s.post_ajax("casestatus/getCaptcha", {})
        self.assertIn("div_captcha", j)
        self.assertEqual(s.stats["recoveries"], 1)
        self.assertEqual(server.count("casestatus/set_data"), 2)    # set_data redone after recovery

    def test_persistent_waf_raises_session_invalid(self):
        server, s, _ = make(cfg=Config(max_recover=2))
        s.handshake()
        server.waf_next(200)
        with self.assertRaises((SessionInvalid, HandshakeError)):
            s.post_ajax("casestatus/getCaptcha", {})

    def test_get_validator_raises_badcontent(self):
        server, s, _ = make()
        s.handshake()
        with self.assertRaises(BadContent):
            s.get("reports/doesnotexist.pdf")
        with self.assertRaises(BadContent):
            s.get("vendor/securimage/securimage_show.php?x", validator=lambda r: False)

    def test_get_captcha_image_same_session(self):
        _, s, _ = make()
        s.handshake()
        r = s.get("/ecourtindia_v6/vendor/securimage/securimage_show.php?abc")
        self.assertTrue(r.content.startswith(b"\x89PNG"))


class TestRateLimiter(unittest.TestCase):
    def test_spacing_with_fake_clock(self):
        t = [0.0]
        waits = []

        def sleep(d):
            waits.append(d)
            t[0] += d

        rl = RateLimiter(5, clock=lambda: t[0], sleep=sleep)
        for _ in range(11):
            rl.acquire()
        self.assertAlmostEqual(t[0], 10 / 5, places=6)   # 11 requests at 5 rps need >= 2.0 s

    def test_never_exceeds_max_rps_across_threads(self):
        rl = RateLimiter(100)
        stamps = []
        lock = threading.Lock()

        def work():
            for _ in range(10):
                rl.acquire()
                with lock:
                    stamps.append(time.monotonic())

        ts = [threading.Thread(target=work) for _ in range(4)]
        t0 = time.monotonic()
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(stamps), 40)
        self.assertGreaterEqual(time.monotonic() - t0, 39 / 100 - 0.02)

    def test_session_requests_go_through_limiter(self):
        server = FakeEcourts()
        t = [0.0]

        def sleep(d):
            t[0] += d

        rl = RateLimiter(2, clock=lambda: t[0], sleep=sleep)
        s = EcourtsSession(Config(), Location(), FakeTransport(server), limiter=rl, sleep=sleep)
        s.handshake()                                    # index, components, set_data = 3 requests
        self.assertGreaterEqual(t[0], 2 / 2 - 1e-9)

    def test_invalid_rps(self):
        with self.assertRaises(ValueError):
            RateLimiter(0)


class TestGovernor(unittest.TestCase):
    def test_breaker_halves_concurrency_and_slows(self):
        t = [0.0]
        slept = []
        rl = RateLimiter(10, clock=lambda: t[0], sleep=lambda d: None)
        gate = ConcurrencyGate(8)
        g = Governor(rl, gate, window=10, threshold=0.5, cooldown=5, restore_after=5, clock=lambda: t[0],
                     sleep=slept.append)
        for _ in range(3):
            g.record(True)
        for _ in range(4):
            g.record(False)
        self.assertEqual(g.trips, 1)
        self.assertEqual(gate.limit, 4)
        self.assertEqual(rl.slow_factor, 2.0)
        g.before_request()
        self.assertEqual(slept, [5])                      # everyone pauses during the cooldown
        t[0] = 100
        for _ in range(5):
            g.record(True)
        self.assertEqual(gate.limit, 5)                   # gradual restore
        self.assertEqual(rl.slow_factor, 1.0)

    def test_gate_limits_active(self):
        gate = ConcurrencyGate(2)
        self.assertTrue(gate.acquire(0.01))
        self.assertTrue(gate.acquire(0.01))
        self.assertFalse(gate.acquire(0.01))
        gate.release()
        self.assertTrue(gate.acquire(0.01))
        gate.set_limit(1)
        self.assertEqual(gate.limit, 1)


if __name__ == "__main__":
    unittest.main()
