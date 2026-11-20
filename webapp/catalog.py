"""Named court selectors from portal dropdowns, with saved responses used when available."""
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup
from flask import abort, jsonify, request
from V2.config import Config, Location
from V2.http import EcourtsSession, RequestsTransport
from V2.ratelimit import RateLimiter

RAW = Path(__file__).resolve().parents[1] / "docs/ecourts_map/raw"
session = None
mutex = threading.Lock()


def options(markup):
    return [[o["value"], o.get_text(" ", strip=True)] for o in BeautifulSoup(markup, "html.parser").select("option[value]") if o["value"] not in ("", "0")]


@lru_cache(maxsize=1024)
def lookup(level, state, district, complex_id, est):
    global session
    if level == "state":
        return json.loads(Path(__file__).with_name("states.json").read_text(encoding="utf-8"))
    spec = {
        "district": ("fillDistrict", "dist_list", "fillDistrict"),
        "complex": ("fillcomplex", "complex_list", f"d{district}_fillcomplex"),
        "establishment": ("fillCourtEstablishment", "establishment_list", f"d{district}_{complex_id}_fillEst"),
        "type": ("fillCaseType", "casetype_list", f"d{district}_{complex_id}_{est}_casetype"),
    }
    endpoint, field, filename = spec[level]
    cached = RAW / f"state_{state}" / (filename + ".json")
    if cached.exists():
        result = options(json.loads(cached.read_text(encoding="utf-8")).get(field) or "")
        if result:
            return result
    data = {"state_code":state}
    if level != "district":
        data["dist_code"] = district
    if level in ("establishment","type"):
        data["court_complex_code"] = complex_id
    if level == "type":
        data.update(est_code=est,search_type="c_type")
    with mutex:
        if session is None:
            cfg = Config()
            session = EcourtsSession(cfg, Location(), RequestsTransport(cfg.user_agent), limiter=RateLimiter(1))
        session.ensure_ready()
        reply = session.post_ajax("casestatus/"+endpoint, data)
    return options(reply[field])


def catalog(level):
    levels = ["state","district","complex","establishment","type"]
    if level not in levels:
        abort(404)
    args = [request.args.get(k,"") for k in levels[:-1]]
    if any(v and not re.fullmatch("[0-9]{1,12}", v) for v in args):
        abort(400)
    try:
        return jsonify(lookup(level,*args))
    except (ValueError, KeyError, OSError, RuntimeError):
        return jsonify(error="Court options could not be loaded from the portal."),503
