"""Every dropdown option for every establishment in Karnataka -> Bengaluru (district 20).
Read-only fill* endpoints via the V2 session (~0.8 req/s); no searches, no CAPTCHA.
Writes raw/bengaluru/<complex>_<est>_<list>.json and raw/bengaluru/_all.json."""
import json, os, re, logging
from bs4 import BeautifulSoup
from V2.config import Config, Location
from V2.http import EcourtsSession, RequestsTransport
from V2.ratelimit import RateLimiter
logging.basicConfig(level=logging.WARNING)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw", "bengaluru"); os.makedirs(OUT, exist_ok=True)
cfg = Config()
s = EcourtsSession(cfg, Location(), RequestsTransport(cfg.user_agent), limiter=RateLimiter(0.8))
s.ensure_ready()
N = [0]
def call(tag, ep, data):
    path = os.path.join(OUT, re.sub(r"[^A-Za-z0-9_.-]", "_", tag) + ".json")
    if os.path.exists(path):                       # resumable
        return json.load(open(path, encoding="utf-8"))
    N[0] += 1
    j = s.post_ajax(ep, data)
    json.dump(j, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return j
def opts(html):
    return [[o.get("value", ""), " ".join(o.get_text(" ", strip=True).split())]
            for o in BeautifulSoup(html or "", "html.parser").find_all("option") if o.get("value", "") not in ("", "0")]
ST, DI = "3", "20"
ALL = {"state": ["3", "Karnataka"], "district": ["20", "BENGALURU"], "complexes": []}
j = call("fillcomplex", "casestatus/fillcomplex", {"state_code": ST, "dist_code": DI})
for val, cname in opts(j.get("complex_list")):
    cc, ests_csv, flag = (val.split("@") + ["", ""])[:3]
    C = {"value": val, "code": cc, "name": cname, "flag": flag, "establishments": []}
    loc = call(f"{cc}_location", "location/location_details", {"state_code": ST, "dist_code": DI, "court_complex_code": cc})
    C["location"] = {"stat": loc.get("stat"), "result": loc.get("result")}
    e = call(f"{cc}_fillEst", "casestatus/fillCourtEstablishment", {"state_code": ST, "dist_code": DI, "court_complex_code": cc})
    for est, ename in opts(e.get("establishment_list")):
        X = {"state_code": ST, "dist_code": DI, "court_complex_code": cc, "est_code": est}
        E = {"code": est, "name": ename}
        E["case_types_c_type"] = opts(call(f"{cc}_{est}_casetype_c_type", "casestatus/fillCaseType", {**X, "search_type": "c_type"}).get("casetype_list"))
        E["case_types_c_no"] = opts(call(f"{cc}_{est}_casetype_c_no", "casestatus/fillCaseType", {**X, "search_type": "c_no"}).get("casetype_list"))
        cv = call(f"{cc}_{est}_casetype_caveat", "casestatus/fillCaseType", {**X, "search_type": "caveat_search"})
        E["caveat_case_types"] = opts(cv.get("casetype_list")); E["subordinate_courts"] = opts(cv.get("subordinate_court_list"))
        E["acts"] = opts(call(f"{cc}_{est}_acts", "casestatus/fillActType", {**X, "search_act": ""}).get("act_list"))
        E["police_stations"] = opts(call(f"{cc}_{est}_police", "casestatus/fillPoliceStation", X).get("police_station_list"))
        E["court_numbers"] = opts(call(f"{cc}_{est}_courtnumber", "courtorder/fillCourtNumber", X).get("courtnumber_list"))
        E["cause_list_courts"] = opts(call(f"{cc}_{est}_causelist", "cause_list/fillCauseList", {**X, "search_act": ""}).get("cause_list"))
        C["establishments"].append(E)
        print(f"{cc} {est:>3} {ename[:45]:45} ct={len(E['case_types_c_type'])} cno={len(E['case_types_c_no'])} acts={len(E['acts'])} ps={len(E['police_stations'])} judges={len(E['court_numbers'])} cl={len(E['cause_list_courts'])} sub={len(E['subordinate_courts'])}", flush=True)
    ALL["complexes"].append(C)
ALL["requests_this_run"] = N[0]
json.dump(ALL, open(os.path.join(OUT, "_all.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("requests this run:", N[0])
