"""Global rate limiter, circuit breaker and concurrency gate shared by all worker sessions."""
from __future__ import annotations

import collections
import contextlib
import logging
import random
import threading
import time
from typing import Callable, Deque, Dict, List, Optional

log = logging.getLogger("V2.ratelimit")


class BlockedAbort(Exception):
    """Raised in every thread once the BlockGuard gave up (blocked too long). Deliberately NOT an EcourtsError,
    so generic ``except EcourtsError`` retry code cannot swallow it."""


class RateLimiter:
    """Spaces request start times at least 1/max_rps apart across all threads (slot reservation)."""

    def __init__(self, max_rps: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if max_rps <= 0:
            raise ValueError("max_rps must be > 0")
        self.max_rps = float(max_rps)
        self.slow_factor = 1.0
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._next = 0.0
        self.waited = 0.0
        self.tuner: Optional["AutoTune"] = None

    @property
    def interval(self) -> float:
        return self.slow_factor / self.max_rps

    def acquire(self) -> float:
        if self.tuner is not None:
            self.tuner.tick()
        with self._lock:
            now = self._clock()
            start = max(self._next, now)
            self._next = start + self.interval
            wait = start - now
        if wait > 0:
            self._sleep(wait)
            self.waited += wait
        return wait


class ConcurrencyGate:
    """Counting gate whose limit can be lowered (breaker) and slowly restored."""

    def __init__(self, limit: int):
        self.max_limit = self.limit = max(1, limit)
        self._active = 0
        self._cv = threading.Condition()

    def acquire(self, timeout: float = None) -> bool:
        with self._cv:
            ok = self._cv.wait_for(lambda: self._active < self.limit, timeout)
            if ok:
                self._active += 1
            return ok

    def release(self) -> None:
        with self._cv:
            self._active -= 1
            self._cv.notify_all()

    def set_limit(self, n: int) -> None:
        with self._cv:
            self.limit = max(1, min(self.max_limit, n))
            self._cv.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()


class Governor:
    """Circuit breaker: when the recent error rate spikes, pause everyone, halve concurrency and
    slow the limiter; restore gradually after a long run of successes."""

    def __init__(self, limiter: RateLimiter, gate: ConcurrencyGate, window: int = 20, threshold: float = 0.5,
                 cooldown: float = 8.0, restore_after: int = 60, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.limiter, self.gate = limiter, gate
        self.window, self.threshold, self.cooldown, self.restore_after = window, threshold, cooldown, restore_after
        self._clock, self._sleep = clock, sleep
        self._hist: Deque[bool] = collections.deque(maxlen=window)
        self._lock = threading.Lock()
        self._pause_until = 0.0
        self._ok_streak = 0
        self.trips = 0

    def before_request(self) -> None:
        wait = self._pause_until - self._clock()
        if wait > 0:
            self._sleep(wait)

    def record(self, ok: bool) -> None:
        with self._lock:
            self._hist.append(ok)
            self._ok_streak = self._ok_streak + 1 if ok else 0
            n = len(self._hist)
            if n >= max(5, self.window // 2) and (n - sum(self._hist)) / n > self.threshold:
                self.trips += 1
                self._hist.clear()
                self._pause_until = self._clock() + self.cooldown * min(self.trips, 4)
                self.gate.set_limit(self.gate.limit // 2)
                self.limiter.slow_factor = min(self.limiter.slow_factor * 2, 8.0)
            elif ok and self._ok_streak >= self.restore_after and (
                    self.limiter.slow_factor > 1.0 or self.gate.limit < self.gate.max_limit):
                self._ok_streak = 0
                self.limiter.slow_factor = max(1.0, self.limiter.slow_factor / 2)
                self.gate.set_limit(self.gate.limit + 1)


class AutoTune:
    """Ramp the limiter: +step rps every ``interval`` s without a block signal, up to ``max_rps`` (and never above
    ``ceiling``, which a block lowers to 50% of the blocking rate)."""

    def __init__(self, limiter: RateLimiter, max_rps: float, step: float = 0.25, interval: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self.limiter, self.max_rps, self.step, self.interval, self._clock = limiter, max_rps, step, interval, clock
        self.ceiling = max_rps
        self.tolerated: Optional[float] = None
        self.last_change = clock()
        self.paused = False                       # True between a block signal and the unblock
        self.block_rate: Optional[float] = None   # limiter rate when the (first) block hit

    def tick(self) -> None:
        if self.paused:                           # never ramp while blocked (live run 2026-11-13 did)
            return
        now = self._clock()
        if now - self.last_change < self.interval:
            return
        self.last_change = now
        cur = self.limiter.max_rps
        new = min(self.ceiling, cur + self.step)
        if new > cur:
            self.limiter.max_rps = new
            log.info("auto-tune: %.2f -> %.2f rps", cur, new)

    def note_block(self) -> None:
        if not self.paused:
            self.block_rate = self.limiter.max_rps
        self.paused = True
        self.last_change = self._clock()

    def lower_ceiling(self, rps: float) -> None:
        self.ceiling = rps
        self.tolerated = rps
        self.paused = False
        self.last_change = self._clock()


class Metrics:
    """Thread-safe per-request-type counts and latency."""

    def __init__(self):
        self._lock = threading.Lock()
        self._d: Dict[str, List[float]] = {}

    def add(self, label: str, dt: float) -> None:
        with self._lock:
            e = self._d.setdefault(label, [0, 0.0])
            e[0] += 1
            e[1] += dt

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            return {k: {"count": int(v[0]), "avg_s": round(v[1] / v[0], 4)} for k, v in sorted(self._d.items())}


class BlockGuard:
    """Shared by all sessions. A *block signal* (HTTP 403/405, or a 'Security Page' / 'Search Page not Found'
    body) puts the whole run into a blocked state: every thread waits (no requests, no handshakes, no retries) for
    a cooldown (base, doubling per consecutive block up to max, +0-25% jitter). Afterwards exactly ONE thread
    sends a single probe; success unblocks (and bumps ``epoch`` so every session re-handshakes lazily), failure
    starts a longer cooldown. When the total blocked time exceeds ``max_block_s`` the guard aborts."""

    MARKERS = ("Security Page", "Search Page not Found")

    def __init__(self, base: float = 90.0, max_cooldown: float = 900.0, max_block_s: float = 1800.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[], float] = random.random):
        self.base, self.max_cooldown, self.max_block_s = base, max_cooldown, max_block_s
        self._clock, self._sleep, self._rng = clock, sleep, rng
        self._lock = threading.Lock()
        self._hs = threading.Lock()
        self.epoch = 0
        self.blocked = False
        self.aborted = False
        self.blocks = 0
        self.probes = 0
        self.consecutive = 0
        self.cooldowns: List[float] = []
        self._accum = 0.0
        self._since = 0.0
        self._until = 0.0
        self._probing = False
        self._ok_since = 0
        self.on_block: List[Callable[[], None]] = []
        self.on_unblock: List[Callable[[], None]] = []

    @classmethod
    def is_block(cls, status: int, text: str = "") -> bool:
        if status in (403, 405):
            return True
        t = (text or "")[:5000]
        return bool(t) and not t.lstrip().startswith("{") and any(m in t for m in cls.MARKERS)

    def _new_cooldown(self) -> None:
        c = min(self.max_cooldown, self.base * 2 ** (max(self.consecutive, 1) - 1)) * (1 + 0.25 * self._rng())
        self.cooldowns.append(c)
        self._until = self._clock() + c

    def blocked_time(self) -> float:
        with self._lock:
            return self._accum + (self._clock() - self._since if self.blocked else 0.0)

    def signal(self, reason: str = "", epoch: Optional[int] = None) -> bool:
        """``epoch`` = guard epoch when the offending request was sent; a stale response (the guard unblocked in
        the meantime) is ignored so one block cannot be counted twice."""
        with self._lock:
            if self.blocked or self.aborted or (epoch is not None and epoch != self.epoch):
                return False
            self.blocked = True
            self.blocks += 1
            self.consecutive += 1
            self._since = self._clock()
            self._ok_since = 0
            self._new_cooldown()
            cd = self.cooldowns[-1]
        log.warning("BLOCK signal (%s): all requests paused for %.0fs", reason, cd)
        for cb in self.on_block:
            cb()
        return True

    def note_ok(self) -> None:
        with self._lock:
            if not self.blocked:
                self._ok_since += 1
                if self._ok_since >= 20:
                    self.consecutive = 0

    @contextlib.contextmanager
    def handshake_slot(self):
        """Handshakes are serialised across all sessions."""
        with self._hs:
            yield

    def wait_clear(self, probe: Callable[[], bool]) -> None:
        while True:
            do_probe = False
            rem = 0.0
            with self._lock:
                if self.aborted:
                    raise BlockedAbort("aborted: blocked too long")
                if not self.blocked:
                    return
                now = self._clock()
                if self._accum + (now - self._since) > self.max_block_s:
                    self.aborted = True
                    log.error("blocked for more than %.0fs: aborting", self.max_block_s)
                    raise BlockedAbort("aborted: blocked too long")
                rem = self._until - now
                if rem <= 0 and not self._probing:
                    self._probing = True
                    do_probe = True
            if not do_probe:
                self._sleep(min(max(rem, 0.05), 1.0))
                continue
            ok = False
            try:
                ok = bool(probe())
            except BlockedAbort:
                raise
            except Exception:  # noqa: BLE001
                ok = False
            unblocked = False
            with self._lock:
                self._probing = False
                self.probes += 1
                if ok:
                    self._accum += self._clock() - self._since
                    self.blocked = False
                    self.epoch += 1
                    self._ok_since = 0
                    unblocked = True
                else:
                    self.consecutive += 1
                    self._new_cooldown()
            if unblocked:
                log.warning("probe OK: unblocked after %.0fs total blocked; sessions will re-handshake",
                            self._accum)
                for cb in self.on_unblock:
                    cb()
            else:
                log.warning("probe still blocked: next cooldown %.0fs", self.cooldowns[-1])
