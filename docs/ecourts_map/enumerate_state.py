"""Every dropdown option for a whole state (default Karnataka = 3), read-only, ~0.8 req/s, resumable.
Per establishment: case types (c_type; Bengaluru showed c_no and caveat lists are identical to it).
Per complex (Bengaluru showed these are identical for every establishment in a complex): acts, police
stations, judge postings (court numbers), cause-list courts, subordinate courts, location.
For complexes with >=2 establishments the acts list is also fetched for the LAST establishment as a
check of that assumption. Output: raw/state_<code>/ (one JSON per reply) and raw/state_<code>/_all.json."""
import json, os, re, sys, logging
from bs4 import BeautifulSoup
from V2.config import Config, Location
from V2.http import EcourtsSession, RequestsTransport
from V2.ratelimit import RateLimiter
logging.basicConfig(level=logging.WARNING)
ST = sys.argv[1] if len(sys.argv) > 1 else "3"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw", f"state_{ST}"); os.makedirs(OUT, exist_ok=True)
cfg = Config()
s = EcourtsSession(cfg, Location(), RequestsTransport(cfg.user_agent), limiter=RateLimiter(0.8))
N = [0]
def call(tag, ep, data):
    path = os.path.join(OUT, re.sub(r"[^A-Za-z0-9_.-]", "_", tag) + ".json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    s.ensure_ready(); N[0] += 1
    j = s.post_ajax(ep, data)
    # The site occasionally answers status=1 with an EMPTY list (seen live for 2 of ~370 complexes);
    # retry once so a transient empty reply is not recorded as "no options".
    if not any("<option" in str(v) and "value" in str(v) and str(v).count("<option") > 1 for v in j.values()):
        N[0] += 1
        j2 = s.post_ajax(ep, data)
        if any(str(v).count("<option") > 1 for v in j2.values()):
            j = j2
    json.dump(j, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return j
def opts(html):
    return [[o.get("value", ""), " ".join(o.get_text(" ", strip=True).split())]
            for o in BeautifulSoup(html or "", "html.parser").find_all("option") if o.get("value", "") not in ("", "0")]
ALL = {"state": ST, "districts": [], "checks": []}
for dv, dname in opts(call("fillDistrict", "casestatus/fillDistrict", {"state_code": ST}).get("dist_list")):
    D = {"code": dv, "name": dname, "complexes": []}
    for val, cname in opts(call(f"d{dv}_fillcomplex", "casestatus/fillcomplex", {"state_code": ST, "dist_code": dv}).get("complex_list")):
        cc, _, flag = (val.split("@") + ["", ""])[:3]
        base = {"state_code": ST, "dist_code": dv, "court_complex_code": cc}
        C = {"value": val, "code": cc, "name": cname, "flag": flag, "establishments": []}
        ests = opts(call(f"d{dv}_{cc}_fillEst", "casestatus/fillCourtEstablishment", base).get("establishment_list"))
        for est, ename in ests:
            ct = opts(call(f"d{dv}_{cc}_{est}_casetype", "casestatus/fillCaseType", {**base, "est_code": est, "search_type": "c_type"}).get("casetype_list"))
            C["establishments"].append({"code": est, "name": ename, "case_types": ct})
        if ests:
            X = {**base, "est_code": ests[0][0]}
            C["acts"] = opts(call(f"d{dv}_{cc}_acts", "casestatus/fillActType", {**X, "search_act": ""}).get("act_list"))
            C["police_stations"] = opts(call(f"d{dv}_{cc}_police", "casestatus/fillPoliceStation", X).get("police_station_list"))
            C["court_numbers"] = opts(call(f"d{dv}_{cc}_courtnumber", "courtorder/fillCourtNumber", X).get("courtnumber_list"))
            C["cause_list_courts"] = opts(call(f"d{dv}_{cc}_causelist", "cause_list/fillCauseList", {**X, "search_act": ""}).get("cause_list"))
            C["subordinate_courts"] = opts(call(f"d{dv}_{cc}_caveat", "casestatus/fillCaseType", {**X, "search_type": "caveat_search"}).get("subordinate_court_list"))
            if len(ests) >= 2:
                last = opts(call(f"d{dv}_{cc}_acts_last", "casestatus/fillActType", {**base, "est_code": ests[-1][0], "search_act": ""}).get("act_list"))
                ALL["checks"].append({"complex": cc, "acts_same_first_last": last == C["acts"]})
        loc = call(f"d{dv}_{cc}_location", "location/location_details", base)
        C["location"] = {"stat": loc.get("stat"), "result": loc.get("result")}
        D["complexes"].append(C)
    ALL["districts"].append(D)
    n_e = sum(len(c["establishments"]) for c in D["complexes"])
    print(f"{dv:>3} {dname[:22]:22} complexes={len(D['complexes']):3} establishments={n_e:3} requests_so_far={N[0]}", flush=True)
    json.dump(ALL, open(os.path.join(OUT, "_all.json"), "w", encoding="utf-8"), ensure_ascii=False)
ALL["requests_this_run"] = N[0]
json.dump(ALL, open(os.path.join(OUT, "_all.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("DONE requests this run:", N[0], "checks failed:", [c for c in ALL["checks"] if not c["acts_same_first_last"]])
