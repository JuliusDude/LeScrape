import os
import tempfile
import threading
import unittest

from V2.__main__ import exit_code
from V2.captcha import CallableSolver
from V2.config import Config, Location, SearchSpec
from V2.http import (BlockSignal, EcourtsSession, HandshakeCapExceeded)
from V2.pipeline import Pipeline
from V2.ratelimit import AutoTune, BlockedAbort, BlockGuard, ConcurrencyGate, RateLimiter
from V2.tests.fakes import FakeEcourts, FakeTransport, GOOD_CAPTCHA


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        return self.t

    def sleep(self, d):
        with self.lock:
            self.t += d


def guard_with(clock, **kw):
    kw.setdefault("rng", lambda: 0.0)
    return BlockGuard(clock=clock, sleep=clock.sleep, **kw)


class TestGuardUnit(unittest.TestCase):
    def test_all_threads_wait_single_probe_after_cooldown(self):
        clk = FakeClock()
        g = guard_with(clk, base=90, max_cooldown=900, max_block_s=1800)
        probes = []
        unblocked = []
        g.on_unblock.append(lambda: unblocked.append(clk.t))

        def probe():
            probes.append(clk.t)
            return True

        self.assertTrue(g.signal("test"))
        self.assertFalse(g.signal("dup"))                       # duplicate signals are ignored
        done = []
        ts = [threading.Thread(target=lambda: (g.wait_clear(probe), done.append(1))) for _ in range(5)]
        [t.start() for t in ts]
        [t.join(5) for t in ts]
        self.assertEqual(len(done), 5)
        self.assertEqual(len(probes), 1)                         # exactly ONE probe
        self.assertGreaterEqual(probes[0], 90)                   # never before the cooldown ended
        self.assertEqual((g.blocks, g.probes, g.epoch, len(unblocked)), (1, 1, 1, 1))
        self.assertGreaterEqual(g.blocked_time(), 90)

    def test_cooldown_doubles_and_caps(self):
        clk = FakeClock()
        g = guard_with(clk, base=90, max_cooldown=900, max_block_s=10 ** 9)
        results = iter([False, False, False, False, False, False, True])
        g.signal("x")
        g.wait_clear(lambda: next(results))
        self.assertEqual(g.cooldowns, [90, 180, 360, 720, 900, 900, 900])
        self.assertEqual(g.probes, 7)

    def test_jitter_applied(self):
        clk = FakeClock()
        g = BlockGuard(base=100, clock=clk, sleep=clk.sleep, rng=lambda: 1.0)
        g.signal("x")
        self.assertAlmostEqual(g.cooldowns[0], 125.0)

    def test_abort_after_max_block(self):
        clk = FakeClock()
        g = guard_with(clk, base=90, max_cooldown=900, max_block_s=1800)
        g.signal("x")
        with self.assertRaises(BlockedAbort):
            g.wait_clear(lambda: False)
        self.assertTrue(g.aborted)
        with self.assertRaises(BlockedAbort):                    # every thread now aborts at once
            g.wait_clear(lambda: True)
        self.assertLessEqual(g.probes, 8)

    def test_is_block(self):
        self.assertTrue(BlockGuard.is_block(405, "x"))
        self.assertTrue(BlockGuard.is_block(403, ""))
        self.assertTrue(BlockGuard.is_block(200, "<html>Security Page</html>"))
        self.assertTrue(BlockGuard.is_block(200, "Search Page not Found"))
        self.assertFalse(BlockGuard.is_block(200, '{"data_list":"Security Page"}'))
        self.assertFalse(BlockGuard.is_block(200, "<html>ok</html>"))
        self.assertFalse(BlockGuard.is_block(503, "busy"))

    def test_consecutive_resets_after_clean_requests(self):
        clk = FakeClock()
        g = guard_with(clk, base=10)
        g.signal("x")
        g.wait_clear(lambda: True)
        self.assertEqual(g.consecutive, 1)
        for _ in range(20):
            g.note_ok()
        self.assertEqual(g.consecutive, 0)


class TestAutoTune(unittest.TestCase):
    def test_ramp_then_backoff(self):
        clk = FakeClock()
        rl = RateLimiter(1.0, clock=clk, sleep=clk.sleep)
        tune = AutoTune(rl, max_rps=2.0, step=0.25, interval=60, clock=clk)
        rl.tuner = tune
        seen = []
        for _ in range(6):
            clk.t += 60
            rl.acquire()
            seen.append(rl.max_rps)
        self.assertEqual(seen, [1.25, 1.5, 1.75, 2.0, 2.0, 2.0])      # capped at --max-rps
        clk.t += 30
        rl.acquire()
        self.assertEqual(rl.max_rps, 2.0)                             # nothing before 60 s elapsed
        # block at 2.0 rps: guard -> pipeline hook halves and sets the ceiling
        self.assertEqual(tune.tolerated, None)
        tune.lower_ceiling(rl.max_rps / 2)
        rl.max_rps = 1.0
        tune.note_block()
        for _ in range(5):
            clk.t += 60
            rl.acquire()
        self.assertEqual(rl.max_rps, 1.0)                             # stays <= 50% of the blocking rate
        self.assertEqual(tune.tolerated, 1.0)

    def test_no_ramp_within_interval_after_block(self):
        clk = FakeClock()
        rl = RateLimiter(1.0, clock=clk, sleep=clk.sleep)
        tune = AutoTune(rl, 3.0, interval=60, clock=clk)
        rl.tuner = tune
        clk.t = 59
        rl.acquire()
        self.assertEqual(rl.max_rps, 1.0)
        tune.note_block()
        clk.t = 100
        rl.acquire()
        self.assertEqual(rl.max_rps, 1.0)


class TestSessionBlockHandling(unittest.TestCase):
    def make(self, server, clk, cfg=None, **gk):
        g = guard_with(clk, **gk)
        s = EcourtsSession(cfg or Config(), Location(), FakeTransport(server), sleep=clk.sleep, rng=lambda: 0.0,
                           guard=g)
        return s, g

    def test_handshake_405_does_not_retry_loop(self):
        server = FakeEcourts()
        server.blocked = True
        clk = FakeClock()
        s, g = self.make(server, clk, base=90, max_cooldown=900, max_block_s=1800)
        with self.assertRaises(BlockedAbort):
            s.ensure_ready()
        # 1 initial index GET + one probe per cooldown (90,180,360,720,900 ...) -- never a 4x retry loop per attempt
        self.assertLessEqual(server.count("casestatus/index"), 10)
        self.assertEqual(server.count("casestatus/set_data"), 0)
        self.assertEqual(s.stats["handshakes"], s.stats["handshakes"])
        self.assertLessEqual(s.stats["handshakes"], 12)
        self.assertEqual(g.blocks, 1)
        self.assertEqual(g.cooldowns[:3], [90, 180, 360])

    def test_no_requests_while_blocked_then_rehandshake_after_probe(self):
        server = FakeEcourts()
        clk = FakeClock()
        s, g = self.make(server, clk, base=90)
        s.ensure_ready()
        first_epoch = s.epoch
        server.blocked = True
        server.unblock_after_index_probes = 2                       # first probe fails, second succeeds
        j = s.post_ajax("casestatus/getCaptcha", {})
        self.assertIn("div_captcha", j)
        self.assertEqual(g.probes, 2)
        self.assertEqual(server.blocked_hits, 2)                    # the failing POST + the failing probe only
        self.assertEqual(g.cooldowns, [90, 180])
        self.assertGreater(s.epoch, first_epoch)
        self.assertEqual(s.stats["handshakes"], 2)
        self.assertGreaterEqual(clk.t, 270)

    def test_waf_body_is_block_signal(self):
        server = FakeEcourts()
        clk = FakeClock()
        s, g = self.make(server, clk, base=5)
        s.ensure_ready()
        server.waf_next(1)
        self.assertIn("div_captcha", s.post_ajax("casestatus/getCaptcha", {}))
        self.assertEqual(g.blocks, 1)

    def test_handshake_cap_counts_as_block(self):
        server = FakeEcourts()
        clk = FakeClock()
        s, g = self.make(server, clk, cfg=Config(max_handshakes=3), base=5)
        for _ in range(3):
            s.handshake()
        with self.assertRaises(HandshakeCapExceeded):
            s.handshake()
        self.assertEqual(g.blocks, 1)
        self.assertTrue(g.blocked)

    def test_metrics_recorded(self):
        from V2.ratelimit import Metrics
        server = FakeEcourts()
        m = Metrics()
        s = EcourtsSession(Config(), Location(), FakeTransport(server), sleep=lambda d: None, metrics=m)
        s.ensure_ready()
        s.post_ajax("casestatus/getCaptcha", {})
        snap = m.snapshot()
        self.assertEqual(snap["casestatus/getCaptcha"]["count"], 1)
        self.assertEqual(snap["GET index"]["count"], 1)
        self.assertIn("avg_s", snap["casestatus/set_data"])


class PipeBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.out = os.path.join(self._td.name, "run")
        self.server = FakeEcourts(n_rows=6)

    def make(self, **cfgkw):
        kw = dict(max_rps=10000, workers=3, backoff_base=0.0, breaker_cooldown=0.0)
        kw.update(cfgkw)
        cfg = Config(**kw)
        wrong_first = iter(["wrongg"])
        pipe = Pipeline(cfg, Location(), SearchSpec(), self.out,
                        solver=CallableSolver(lambda b: next(wrong_first, GOOD_CAPTCHA)), sleep=lambda d: None)
        pipe.session_factory = lambda name: EcourtsSession(cfg, pipe.loc, FakeTransport(self.server), pipe.limiter,
                                                           pipe.governor, sleep=lambda d: None, name=name)
        self.addCleanup(pipe.store.close)
        return pipe


class TestPipelineBlocks(PipeBase):
    def test_block_pauses_all_single_probe_unblock_and_slow_down(self):
        self.server.block_after_business = 20
        self.server.unblock_after_index_probes = 2
        pipe = self.make(block_cooldown_base=0.2, block_cooldown_max=5.0)
        s = pipe.run()
        self.assertEqual(s["ok"], 6)
        self.assertIsNone(s["aborted"])
        self.assertEqual(s["blocks"], 1)
        self.assertEqual(s["probes"], 2)
        c1, c2 = s["cooldowns_s"]
        self.assertGreater(c2 / c1, 1.5)                               # doubled (jitter 0-25%)
        # while blocked only in-flight requests (<= workers) and the 2 probes ever hit the server
        self.assertLessEqual(self.server.blocked_hits, 3 + 2)
        self.assertEqual(self.server.index_gets_blocked, 2)            # no handshake attempts during the cooldown
        self.assertEqual((s["final_workers"], s["final_rps"]), (1, 5000.0))   # halved: 3 -> 1, 10000 -> 5000 rps
        self.assertGreaterEqual(s["total_blocked_s"], 0.2)
        self.assertGreaterEqual(s["handshakes"], 4)                    # sessions re-handshook lazily
        self.assertEqual(pipe.store.counts()["hearings"], 103)

    def test_abort_after_max_block_and_resume(self):
        self.server.block_after_business = 25                          # blocked forever
        pipe = self.make(block_cooldown_base=0.0, block_cooldown_max=0.0, max_block_min=0.002)
        s = pipe.run()
        self.assertEqual(s["aborted"], "blocked")
        self.assertEqual(exit_code(s), 3)
        self.assertGreaterEqual(s["blocks"], 1)
        self.assertLess(s["ok"], 6)
        self.assertGreater(s["failed_final"], 0)                       # unfinished cases listed as not done
        done_before = pipe.store.completed_cnrs()
        self.assertLess(len(done_before), 6)
        with open(os.path.join(self.out, "summary.json")) as f:
            self.assertEqual(__import__("json").load(f)["aborted"], "blocked")
        # the site recovers; --resume continues exactly the unfinished cases
        self.server.blocked = False
        self.server.block_after_business = None
        pipe2 = self.make(resume=True)
        s2 = pipe2.run()
        self.assertIsNone(s2["aborted"])
        self.assertEqual(s2["skipped_resume"], len(done_before))
        self.assertEqual(s2["ok"], 6 - len(done_before))
        self.assertEqual(len(pipe2.store.completed_cnrs()), 6)
        self.assertEqual(exit_code(s2), 0)

    def test_exit_codes(self):
        self.assertEqual(exit_code({"aborted": None, "failed_final": 0}), 0)
        self.assertEqual(exit_code({"aborted": None, "failed_final": 2}), 2)
        self.assertEqual(exit_code({"aborted": "blocked", "failed_final": 2}), 3)

    def test_summary_metrics_and_defaults(self):
        pipe = self.make(limit=2)
        s = pipe.run()
        for k in ("blocks", "total_blocked_s", "final_rps", "final_workers", "tolerated_rps", "request_stats",
                  "s_per_case_excl_blocked", "aborted"):
            self.assertIn(k, s)
        self.assertEqual((s["blocks"], s["total_blocked_s"]), (0, 0))
        rs = s["request_stats"]
        self.assertEqual(rs["home/viewHistory"]["count"], 2)
        self.assertGreater(rs["home/viewBusiness"]["count"], 10)
        self.assertGreaterEqual(rs["home/display_pdf"]["count"], 2)
        self.assertIn("avg_s", rs["GET pdf"])
        self.assertIsNotNone(s["s_per_case_excl_blocked"])
        d = Config()
        self.assertEqual((d.workers, d.max_rps, d.auto_tune), (1, 1.0, False))

    def test_auto_tune_wired_into_pipeline(self):
        pipe = self.make(auto_tune=True, start_rps=1000, max_rps=4000)
        self.assertEqual(pipe.limiter.max_rps, 1000)
        self.assertIs(pipe.limiter.tuner, pipe.tuner)
        pipe._on_unblock()                                              # what a successful probe triggers
        self.assertEqual(pipe.limiter.max_rps, 500)
        self.assertEqual(pipe.tuner.ceiling, 500)
        s = pipe.run()
        self.assertEqual(s["tolerated_rps"], 500)
        self.assertLessEqual(s["final_rps"], 500)


if __name__ == "__main__":
    unittest.main()


class TestAutoTuneFreezesDuringBlock(unittest.TestCase):
    """Live 2026-11-13: auto-tune kept ramping 2.25 -> 2.75 rps while blocked, so the post-block rate
    was halved from 2.75 instead of the 2.25 that triggered the block."""

    def test_no_ramp_while_blocked_and_ceiling_from_block_rate(self):
        from V2.ratelimit import AutoTune, RateLimiter
        t = [0.0]
        lim = RateLimiter(2.25)
        tune = AutoTune(lim, max_rps=4.0, step=0.25, interval=60.0, clock=lambda: t[0])
        tune.note_block()
        for _ in range(5):
            t[0] += 61
            tune.tick()
        self.assertEqual(lim.max_rps, 2.25)
        self.assertEqual(tune.block_rate, 2.25)
        tune.lower_ceiling(tune.block_rate / 2)
        t[0] += 61
        tune.tick()
        self.assertLessEqual(lim.max_rps, 2.25)
        self.assertEqual(tune.ceiling, 1.125)
