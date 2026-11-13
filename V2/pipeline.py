"""Coordinator + per-case scraping logic."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import parsers, pdf as pdfmod
from .captcha import CaptchaSolver
from .config import Config, Location, SearchSpec
from .http import (BadContent, EcourtsError, EcourtsSession, HandshakeError, HttpFailure, RequestsTransport,
                   SessionInvalid)
from .ratelimit import AutoTune, BlockedAbort, BlockGuard, ConcurrencyGate, Governor, Metrics, RateLimiter
from .search import run_search
from .store import Store, now

log = logging.getLogger("V2.pipeline")


def vb_body(args: List[str], loc: Location) -> Dict[str, str]:
    """Body of home/viewBusiness exactly as the site's viewBusiness() builds it."""
    a = dict(zip(parsers.VB_ARG_NAMES, args))
    body = {"court_code": a["court_code"], "state_code": a["state_code"], "dist_code": a["dist_code"]}
    if a.get("search_by") != "cnr":
        body["court_complex_code"] = loc.complex_code
    body.update({"nextdate1": a["nextdate1"], "case_number1": a["case_number1"], "disposal_flag": a["disposal_flag"],
                 "businessDate": a["businessDate"], "national_court_code": a["national_court_code"],
                 "court_no": a["court_no"], "search_by": a["search_by"], "srno": a["srno"]})
    return body


def pdf_body(args: List[str]) -> Dict[str, str]:
    a = dict(zip(parsers.PDF_ARG_NAMES, args))
    return {"normal_v": a["normal_v"], "case_val": a["case_val"], "court_code": a["court_code"],
            "filename": a["filename"], "appFlag": a["appFlag"]}


def ia_body(args: List[str], loc: Location) -> Dict[str, str]:
    """Body of home/viewIABusiness exactly as the site's viewIABusiness() builds it."""
    a = dict(zip(parsers.IA_ARG_NAMES, args))
    body = {"state_code": loc.state_code, "dist_code": loc.dist_code}
    if a.get("search_by") != "cnr":
        body["court_complex_code"] = loc.complex_code
    for k in parsers.IA_ARG_NAMES:
        body[k] = a[k]
    return body


class CaseScraper:
    """Fetches everything about ONE case using ONE session (sequential: the token rotates)."""

    MAX_CONSEC_FAIL = 3

    def __init__(self, session: EcourtsSession, cfg: Config, loc: Location, pdf_dir: Optional[str],
                 linked_seen: Optional[set] = None, linked_lock: Optional[threading.Lock] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.s, self.cfg, self.loc, self.pdf_dir = session, cfg, loc, pdf_dir
        self.linked_seen = linked_seen if linked_seen is not None else set()
        self.linked_lock = linked_lock or threading.Lock()
        self._sleep = sleep

    # -- helpers
    def _call(self, endpoint: str, params: Dict[str, Any], raw: List[Dict[str, Any]], cnr: str,
              key: str) -> Dict[str, Any]:
        j = self.s.post_ajax(endpoint, params)
        if self.cfg.store_raw:
            body = json.dumps({k: v for k, v in j.items() if k != "app_token"}, ensure_ascii=False)
            raw.append({"endpoint": endpoint.split("&")[0], "params": params, "status": j.get("status"),
                        "body": body, "ts": now()})
        if key not in j or j.get(key) in (None, ""):
            raise EcourtsError(f"{endpoint}: response has no {key!r}: {str(j)[:200]}")
        return j

    def _retry(self, fn: Callable[[], Any], tries: int = 3) -> Any:
        last: Optional[Exception] = None
        for n in range(1, tries + 1):
            try:
                return fn()
            except EcourtsError as e:     # (BlockedAbort is not an EcourtsError and propagates)
                last = e
                log.debug("retry %d/%d after %s", n, tries, e)
                self._sleep(0.3 * n)
        raise last  # type: ignore[misc]

    # -- pieces
    def fetch_business(self, h: Dict[str, Any], cnr: str, raw: List[Dict[str, Any]]) -> None:
        body = vb_body(h["vb_args"], self.loc)
        j = self._retry(lambda: self._call("home/viewBusiness", body, raw, cnr, "data_list"))
        h["business"] = parsers.parse_business(j["data_list"])
        h["fetched"] = True
        h["error"] = None

    def fetch_ia(self, ia: Dict[str, Any], cnr: str, raw: List[Dict[str, Any]]) -> None:
        body = ia_body(ia["args"], self.loc)
        j = self._retry(lambda: self._call("home/viewIABusiness", body, raw, cnr, "data_list"))
        ia["business"] = parsers.parse_ia_business(j["data_list"])
        ia["fetched"] = True
        ia["error"] = None

    def fetch_pdf(self, o: Dict[str, Any], cnr: str, raw: List[Dict[str, Any]]) -> None:
        body = pdf_body(o["pdf_args"])

        def once():
            j = self._call("home/display_pdf", body, raw, cnr, "order")
            resp = self.s.get(j["order"], validator=lambda r: pdfmod.is_pdf(r.content))
            return j["order"], pdfmod.save_pdf(resp.content, self.pdf_dir)

        url, info = self._retry(once)
        o["pdf_url"], o["pdf"], o["fetched"], o["error"] = url, info, True, None

    def fetch_ack(self, case: Dict[str, Any], cnr: str, raw: List[Dict[str, Any]]) -> Dict[str, Any]:
        j = self._retry(lambda: self._call(case["ack_url"], {}, raw, cnr, "ack_details"), 2)
        ack = parsers.parse_ack(j["ack_details"])
        ack["fetched"] = True
        return ack

    # -- main entry
    def scrape(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Returns [main bundle, *linked bundles]. A bundle is saved even when incomplete."""
        t0, req0 = time.monotonic(), self.s.stats["requests"]
        raw: List[Dict[str, Any]] = []
        cnr0 = row.get("cnr", "")
        j = self._retry(lambda: self._call("home/viewHistory", dict(row["view_params"]), raw, cnr0, "data_list"))
        case = parsers.parse_case_page(j["data_list"])
        if not case["cnr"]:
            case["cnr"] = cnr0
        cnr = case["cnr"]
        if cnr0 and cnr != cnr0:
            case["warnings"].append(f"CNR on page ({cnr}) differs from search row ({cnr0})")
        missing: List[str] = []

        fails = 0
        for h in case["hearings"]:
            if not h["vb_args"]:
                continue
            if fails >= self.MAX_CONSEC_FAIL:
                h["fetched"], h["error"] = False, "skipped after repeated failures"
                continue
            try:
                self.fetch_business(h, cnr, raw)
                fails = 0
            except EcourtsError as e:
                fails += 1
                h["fetched"], h["error"] = False, str(e)[:300]
            if not h.get("fetched"):
                missing.append(f"business seq={h['seq']}")

        for ia in case["interim_applications"]:
            if not ia["args"] or len(ia["args"]) < len(parsers.IA_ARG_NAMES):
                continue
            try:
                self.fetch_ia(ia, cnr, raw)
            except EcourtsError as e:
                ia["fetched"], ia["error"] = False, str(e)[:300]
                missing.append(f"ia seq={ia['seq']}")

        if self.cfg.fetch_pdfs:
            for o in case["orders"]:
                if not o["pdf_args"]:
                    continue
                try:
                    self.fetch_pdf(o, cnr, raw)
                except EcourtsError as e:
                    o["fetched"], o["error"] = False, str(e)[:300]
                    missing.append(f"pdf seq={o['seq']}")
        else:
            missing += [f"pdf seq={o['seq']}" for o in case["orders"] if o["pdf_args"]]

        ack = None
        if case["ack_url"]:
            try:
                ack = self.fetch_ack(case, cnr, raw)
            except EcourtsError as e:
                ack = {"fetched": False, "error": str(e)[:300], "fields": {}}
                missing.append("ack")

        # parser sanity: the page showed history rows but we found no business links is suspicious only
        # for non-CNR pages; keep as a warning rather than an error
        meta = {"kind": "searched", "elapsed_s": round(time.monotonic() - t0, 3),
                "requests": self.s.stats["requests"] - req0, "missing": missing, "complete": not missing,
                "search_case_number": row.get("case_number"), "search_parties": row.get("parties"), "flags": {}}
        bundles = [{"case": case, "ack": ack, "raw": raw, "meta": meta}]

        if self.cfg.follow_main_matters:
            for m in case["main_matters"]:
                mc = m.get("cnr")
                if not mc:
                    continue
                with self.linked_lock:
                    if mc in self.linked_seen:
                        continue
                    self.linked_seen.add(mc)
                try:
                    lraw: List[Dict[str, Any]] = []
                    lj = self._retry(lambda: self._call("cnr_status/viewCNRHistory/", {"cino": mc}, lraw, mc,
                                                        "casetype_list"), 2)
                    lcase = parsers.parse_case_page(lj["casetype_list"])
                    if not lcase["cnr"]:
                        lcase["cnr"] = mc
                    bundles.append({"case": lcase, "ack": None, "raw": lraw, "meta": {
                        "kind": "linked", "parent_cnr": cnr, "complete": True, "missing": [], "flags": {},
                        "elapsed_s": None, "requests": 1}})
                except EcourtsError as e:
                    case["warnings"].append(f"main matter {mc} not fetched: {e}")
                    with self.linked_lock:
                        self.linked_seen.discard(mc)
        return bundles


class Pipeline:
    def __init__(self, cfg: Config, loc: Location, spec: SearchSpec, out_dir: str,
                 solver: Optional[CaptchaSolver] = None, session_factory: Optional[Callable[..., EcourtsSession]] = None,
                 limiter: Optional[RateLimiter] = None, store: Optional[Store] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg, self.loc, self.spec, self.out_dir, self.solver = cfg, loc, spec, out_dir, solver
        os.makedirs(out_dir, exist_ok=True)
        self.pdf_dir = os.path.join(out_dir, "pdfs")
        self.store = store or Store(os.path.join(out_dir, "ecourts.db"))
        start = min(cfg.start_rps, cfg.max_rps) if cfg.auto_tune else cfg.max_rps
        self.limiter = limiter or RateLimiter(start)
        self.tuner: Optional[AutoTune] = None
        if cfg.auto_tune:
            self.tuner = AutoTune(self.limiter, cfg.max_rps, cfg.tune_step, cfg.tune_interval)
            self.limiter.tuner = self.tuner
        self.metrics = Metrics()
        self.guard = BlockGuard(cfg.block_cooldown_base, cfg.block_cooldown_max, cfg.max_block_min * 60.0)
        self.guard.on_unblock.append(self._on_unblock)
        if self.tuner:
            self.guard.on_block.append(self.tuner.note_block)
        self.gate = ConcurrencyGate(cfg.workers)
        self.governor = Governor(self.limiter, self.gate, cfg.breaker_window, cfg.breaker_threshold,
                                 cfg.breaker_cooldown)
        self.session_factory = session_factory or self._default_factory
        self._sleep = sleep
        self.run_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self.sessions: List[EcourtsSession] = []
        self.linked_seen: set = set()
        self.linked_lock = threading.Lock()
        self.counters = {"skipped": 0, "linked": 0}
        self.outcome: Dict[str, str] = {}      # cnr -> ok | incomplete | failed | pending (latest attempt)
        self.elapsed_per_case: List[float] = []

    def _default_factory(self, name: str) -> EcourtsSession:
        t = RequestsTransport(self.cfg.user_agent, self.cfg.connect_timeout, self.cfg.read_timeout)
        return EcourtsSession(self.cfg, self.loc, t, self.limiter, self.governor, name=name)

    def _on_unblock(self) -> None:
        """After a block: halve concurrency and rate (floor 1 worker / 0.5 rps) for the rest of the run."""
        old_rps, old_w = self.limiter.max_rps, self.gate.limit
        if self.tuner and self.tuner.block_rate:
            old_rps = self.tuner.block_rate          # halve the rate that actually triggered the block
        new_rps = max(0.5, old_rps / 2)
        self.limiter.max_rps = new_rps
        new_w = max(1, old_w // 2)
        self.gate.max_limit = new_w
        self.gate.set_limit(new_w)
        if self.tuner:
            self.tuner.lower_ceiling(new_rps)
        log.warning("after block: rate %.2f -> %.2f rps, concurrency %d -> %d", old_rps, new_rps, old_w, new_w)

    def _new_session(self, name: str) -> EcourtsSession:
        s = self.session_factory(name)
        if getattr(s, "guard", None) is None:
            s.guard = self.guard
        if getattr(s, "metrics", None) is None:
            s.metrics = self.metrics
        with self._lock:
            self.sessions.append(s)
        return s

    # ------------------------------------------------------------------ steps
    def search(self, session: EcourtsSession) -> Dict[str, Any]:
        if self.solver is None:
            raise EcourtsError("no CAPTCHA solver configured")
        res = run_search(session, self.solver, self.loc, self.spec, self.cfg,
                             debug_dir=os.path.join(self.out_dir, "captcha_rejected"))
        from dataclasses import asdict
        try:  # keep the raw accepted search response for audit/re-parsing
            import gzip
            with gzip.open(os.path.join(self.out_dir, f"search_{self.run_id}.json.gz"), "wt", encoding="utf-8") as f:
                json.dump(res.get("raw") or {}, f, ensure_ascii=False)
        except OSError as e:
            log.warning("could not save raw search response: %s", e)
        self.store.add_search(self.run_id, asdict(self.loc), asdict(self.spec), res["attempts"], res["total"],
                              res["rows"])
        return res

    def _save(self, bundles: List[Dict[str, Any]]) -> bool:
        for b in bundles:
            self.store.save_case(self.run_id, b)
        main = bundles[0]
        with self._lock:
            self.counters["linked"] += len(bundles) - 1
            self.elapsed_per_case.append(main["meta"]["elapsed_s"] or 0)
        return bool(main["meta"]["complete"])

    def _worker(self, idx: int, q: "queue.Queue", failed: List[Dict[str, Any]], session=None) -> None:
        name = f"w{idx}"
        try:
            sess = session or self._new_session(name)
            sess.ensure_ready()
        except BlockedAbort:
            self.stop.set()
            return
        except EcourtsError as e:
            log.error("%s could not start: %s", name, e)
            self.store.record_error(self.run_id, None, "handshake", type(e).__name__, str(e))
            return
        scraper = CaseScraper(sess, self.cfg, self.loc, self.pdf_dir, self.linked_seen, self.linked_lock,
                              self._sleep)
        while not self.stop.is_set():
            try:
                row = q.get_nowait()
            except queue.Empty:
                return
            self.gate.acquire()
            cnr = row.get("cnr")
            try:
                bundles = scraper.scrape(row)
                complete = self._save(bundles)
                with self._lock:
                    self.outcome[cnr] = "ok" if complete else "incomplete"
                if not complete:
                    row["_attempts"] = row.get("_attempts", 0) + 1
                    with self._lock:
                        failed.append(row)
                    log.warning("%s incomplete: %s", cnr, bundles[0]["meta"]["missing"][:5])
            except BlockedAbort:
                self.stop.set()
                with self._lock:
                    failed.append(row)       # stays 'pending' in the summary; --resume continues it
                return
            except KeyboardInterrupt:
                self.stop.set()
                with self._lock:
                    failed.append(row)
                raise
            except Exception as e:  # noqa: BLE001 - one bad case must not kill the worker
                log.warning("%s failed: %s: %s", cnr, type(e).__name__, e)
                self.store.record_error(self.run_id, cnr, "case", type(e).__name__, str(e),
                                        getattr(e, "status", None))
                row["_attempts"] = row.get("_attempts", 0) + 1
                with self._lock:
                    self.outcome[cnr] = "failed"
                    failed.append(row)
                if isinstance(e, (SessionInvalid, HttpFailure, BadContent)):
                    try:
                        sess.recover()
                    except EcourtsError as e2:
                        log.error("%s cannot recover session: %s", name, e2)
                        return
            finally:
                self.gate.release()

    def _run_pool(self, rows: List[Dict[str, Any]], reuse: Optional[EcourtsSession] = None) -> List[Dict[str, Any]]:
        q: "queue.Queue" = queue.Queue()
        for r in rows:
            q.put(r)
        failed: List[Dict[str, Any]] = []
        n = max(1, min(self.cfg.workers, len(rows)))
        threads = []
        for i in range(n):
            t = threading.Thread(target=self._worker, args=(i, q, failed, reuse if i == 0 else None),
                                 name=f"worker-{i}", daemon=True)
            t.start()
            threads.append(t)
        try:
            for t in threads:
                while t.is_alive():
                    t.join(0.5)
        except KeyboardInterrupt:
            self.stop.set()
            raise
        leftovers = []
        while True:
            try:
                leftovers.append(q.get_nowait())
            except queue.Empty:
                break
        return failed + leftovers

    # ------------------------------------------------------------------ run
    def run(self, rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Full run. ``rows`` (search-result dicts) may be supplied to skip the search step."""
        from dataclasses import asdict
        self.store.start_run(self.run_id, {"cfg": self.cfg.to_dict(), "loc": asdict(self.loc),
                                           "spec": asdict(self.spec)})
        t_start = time.monotonic()
        coord = self._new_session("search")
        search_info: Dict[str, Any] = {}
        if rows is None:
            try:
                res = self.search(coord)
                rows, search_info = res["rows"], {"attempts": res["attempts"], "total": res["total"]}
            except BlockedAbort:
                rows = []
                self.stop.set()
        total_rows = len(rows)
        seen, todo = set(), []
        done = self.store.completed_cnrs() if self.cfg.resume else set()
        for r in rows:
            cnr = r.get("cnr")
            if not cnr or cnr in seen or not r.get("view_params"):
                continue
            seen.add(cnr)
            if cnr in done:
                self.counters["skipped"] += 1
                continue
            todo.append(r)
        if self.cfg.limit:
            todo = todo[: self.cfg.limit]
        self.linked_seen |= seen
        self.outcome.update({r["cnr"]: "pending" for r in todo})
        log.info("run %s: %d search rows, %d to scrape, %d skipped (resume)", self.run_id, total_rows, len(todo),
                 self.counters["skipped"])

        t_pool = time.monotonic()
        pending = todo
        reuse = coord
        failed: List[Dict[str, Any]] = []
        try:
            for rnd in range(self.cfg.case_retries + 1):
                if not pending or self.guard.aborted:
                    break
                if rnd:
                    log.info("retry round %d: %d case(s)", rnd, len(pending))
                    self._sleep(min(5.0, 1.0 * rnd))
                failed = self._run_pool(pending, reuse)
                reuse = None
                pending = failed
        except KeyboardInterrupt:
            log.warning("interrupted; summarising what was saved")
        pool_wall = time.monotonic() - t_pool
        blocked_s = self.guard.blocked_time()
        suspect = self.store.flag_suspect_main_filing()
        c = self.counters
        ok_n = sum(1 for v in self.outcome.values() if v == "ok")
        failed_cnrs = sorted(k for k, v in self.outcome.items() if v != "ok")
        reqs = sum(s.stats["requests"] for s in self.sessions)
        summary = {
            "run_id": self.run_id, "search_rows": total_rows, "scheduled": len(todo), "ok": ok_n,
            "incomplete_final": sum(1 for v in self.outcome.values() if v == "incomplete"),
            "failed_final": len(failed_cnrs),
            "failed_cnrs": failed_cnrs, "skipped_resume": c["skipped"],
            "linked_main_matters": c["linked"], "workers": self.cfg.workers, "max_rps": self.cfg.max_rps,
            "effective_concurrency": self.gate.limit, "breaker_trips": self.governor.trips,
            "handshakes": sum(s.stats["handshakes"] for s in self.sessions),
            "recoveries": sum(s.stats["recoveries"] for s in self.sessions),
            "retries": sum(s.stats["retries"] for s in self.sessions), "requests": reqs,
            "search": search_info, "wall_s_total": round(time.monotonic() - t_start, 3),
            "wall_s_scrape": round(pool_wall, 3),
            "s_per_case": round(pool_wall / ok_n, 3) if ok_n else None,
            "cases_per_min": round(ok_n * 60 / pool_wall, 2) if ok_n and pool_wall else None,
            "requests_per_s": round(reqs / pool_wall, 2) if pool_wall else None,
            "avg_case_elapsed_s": round(sum(self.elapsed_per_case) / len(self.elapsed_per_case), 3)
            if self.elapsed_per_case else None,
            "aborted": "blocked" if self.guard.aborted else None, "blocks": self.guard.blocks,
            "total_blocked_s": round(blocked_s, 3), "probes": self.guard.probes,
            "cooldowns_s": [round(c, 1) for c in self.guard.cooldowns],
            "final_rps": self.limiter.max_rps, "final_workers": self.gate.limit,
            "tolerated_rps": self.tuner.tolerated if self.tuner else None,
            "request_stats": self.metrics.snapshot(),
            "s_per_case_excl_blocked": round(max(pool_wall - blocked_s, 0) / ok_n, 3) if ok_n else None,
            "suspect_main_filing_numbers": suspect, "db_counts": self.store.counts(),
        }
        self.store.finish_run(self.run_id, summary)
        with open(os.path.join(self.out_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return summary
