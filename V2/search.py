"""Case-type search (the only step that needs a CAPTCHA)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import parsers
from .captcha import CaptchaSolver, solve_with_retries
from .config import Config, Location, SearchSpec
from .http import BadContent, EcourtsError, EcourtsSession

log = logging.getLogger("V2.search")


class SearchError(EcourtsError):
    pass


def _looks_like_image(r) -> bool:
    c = r.content or b""
    return len(c) > 100 and (c[:8] == b"\x89PNG\r\n\x1a\n" or c[:3] == b"\xff\xd8\xff" or c[:3] == b"GIF")


def run_search(session: EcourtsSession, solver: CaptchaSolver, loc: Location, spec: SearchSpec,
               cfg: Config, debug_dir: str = "") -> Dict[str, Any]:
    """getCaptcha -> image -> OCR -> submit_case_type, retrying with a fresh captcha until accepted.
    Returns {rows, total, attempts, raw} (raw = JSON of the accepted response)."""
    session.ensure_ready()
    pending_div: List[str] = []   # fresh captcha html handed back by a rejection

    def fetch_image() -> bytes:
        if pending_div:
            div = pending_div.pop()
        else:
            div = session.post_ajax("casestatus/getCaptcha", {}).get("div_captcha", "")
        src = parsers.parse_captcha_src(div)
        if not src:
            raise BadContent("no <img> in div_captcha")
        return session.get(src, validator=_looks_like_image).content

    def submit(text: str):
        payload = {"case_type_1": spec.case_type, "search_year": spec.year, "case_status": spec.status,
                   "ct_captcha_code": text, "state_code": loc.state_code, "dist_code": loc.dist_code,
                   "court_complex_code": loc.complex_code, "est_code": loc.est_code}
        j = session.post_ajax("casestatus/submit_case_type", payload)
        err = str(j.get("errormsg") or "")
        if "captcha" in err.lower():
            if j.get("div_captcha"):
                pending_div.append(j["div_captcha"])
            return False, None
        if err and not j.get("case_data"):
            raise SearchError(err)
        return True, j

    j, attempts = solve_with_retries(fetch_image, solver, submit, cfg.captcha_attempts, cfg.captcha_length,
                                     debug_dir=debug_dir or None)
    parsed = parsers.parse_search_results(j.get("case_data", ""))
    log.info("search ok after %d captcha attempt(s): %d rows (site reports %s)", attempts, len(parsed["rows"]),
             parsed["total"])
    return {"rows": parsed["rows"], "total": parsed["total"], "attempts": attempts, "raw": j}
