"""Enumerate the dropdown hierarchy for Karnataka -> Bengaluru via the portal's fill* endpoints (read-only,
no searches, no CAPTCHA). Saves every raw JSON reply to raw/enum/."""
import json, os, re, time, logging
from bs4 import BeautifulSoup
from V2.config import Config, Location
from V2.http import EcourtsSession, RequestsTransport
from V2.ratelimit import RateLimiter
logging.basicConfig(level=logging.WARNING)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw", "enum"); os.makedirs(OUT, exist_ok=True)
cfg = Config()
s = EcourtsSession(cfg, Location(), RequestsTransport(cfg.user_agent), limiter=RateLimiter(0.8))
s.ensure_ready()
n = [0]
def call(tag, ep, data):
    n[0] += 1
    j = s.post_ajax(ep, data)
    json.dump(j, open(os.path.join(OUT, re.sub(r"[^A-Za-z0-9_.-]", "_", tag) + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return j
def opts(html):
    return [(o.get("value", ""), " ".join(o.get_text(" ", strip=True).split())) for o in BeautifulSoup(html or "", "html.parser").find_all("option")]
R = {}
idx = BeautifulSoup(open("docs/ecourts_map/raw/ecourtindia_v6_p=casestatus_index.html", encoding="utf-8").read(), "lxml")
R["states"] = [(o.get("value"), o.get_text(strip=True)) for o in idx.select("#sess_state_code option")]
j = call("fillDistrict_3", "casestatus/fillDistrict", {"state_code": "3"}); R["districts_KA"] = opts(j.get("dist_list"))
j = call("fillcomplex_3_20", "casestatus/fillcomplex", {"state_code": "3", "dist_code": "20"}); R["complexes_BLR"] = opts(j.get("complex_list"))
R["complex_detail"] = {}
for val, name in R["complexes_BLR"]:
    if not val or val == "0": continue
    cc, est, flag = (val.split("@") + ["", ""])[:3]
    d = {"name": name, "value": val, "flag": flag, "establishments": []}
    if flag == "Y":
        j = call(f"fillEst_{cc}", "casestatus/fillCourtEstablishment", {"state_code": "3", "dist_code": "20", "court_complex_code": cc})
        d["establishments"] = [x for x in opts(j.get("establishment_list")) if x[0] not in ("", "0")]
    ests = [e for e, _ in d["establishments"]] if flag == "Y" else [""]
    d["case_types"] = {}
    for e in ests:
        j = call(f"fillCaseType_{cc}_{e or 'none'}", "casestatus/fillCaseType",
                 {"state_code": "3", "dist_code": "20", "court_complex_code": cc, "est_code": e, "search_type": "c_type"})
        d["case_types"][e or "(complex)"] = [x for x in opts(j.get("casetype_list")) if x[0] not in ("", "0")]
    R["complex_detail"][cc] = d
# per-establishment dropdowns for the scraped court: City Civil Court Complex (1030135) est 3
X = {"state_code": "3", "dist_code": "20", "court_complex_code": "1030135", "est_code": "3"}
R["sample_est"] = "1030135 / est 3 (PRL. CITY CIVIL AND SESSIONS JUDGE)"
for tag, ep, extra, key in (("fillPoliceStation", "casestatus/fillPoliceStation", {}, "police_station_list"),
                            ("fillActType_all", "casestatus/fillActType", {"search_act": ""}, "act_list"),
                            ("fillCourtNumber", "courtorder/fillCourtNumber", {}, None),
                            ("fillCauseList", "cause_list/fillCauseList", {"search_act": ""}, "cause_list"),
                            ("fillCaseType_cno", "casestatus/fillCaseType", {"search_type": "c_no"}, "casetype_list"),
                            ("fillCaseType_caveat", "casestatus/fillCaseType", {"search_type": "caveat_search"}, None),
                            ("fillSubordinateCourt", "caveat_search/fillSubordinateCourt", {}, None)):
    j = call(tag, ep, {**X, **extra})
    keys = [k for k in j if k not in ("app_token",)]
    R[tag] = {"keys": keys, "options": {k: opts(j[k]) for k in keys if isinstance(j.get(k), str) and "<option" in j[k]}}
j = call("location_details", "location/location_details", {"state_code": "3", "dist_code": "20", "court_complex_code": "1030135"})
R["location_details"] = j
R["requests"] = n[0]
json.dump(R, open(os.path.join(OUT, "_summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("requests:", n[0], "states:", len(R["states"]), "districts:", len(R["districts_KA"]), "complexes:", len(R["complexes_BLR"]))
for cc, d in R["complex_detail"].items():
    print(" ", cc, d["flag"], d["name"][:60], "ests:", len(d["establishments"]), "case types:", {k: len(v) for k, v in d["case_types"].items()})
for t in ("fillPoliceStation", "fillActType_all", "fillCourtNumber", "fillCauseList", "fillCaseType_cno", "fillCaseType_caveat", "fillSubordinateCourt"):
    print(" ", t, R[t]["keys"], {k: len(v) for k, v in R[t]["options"].items()})
print(" location:", str(R["location_details"])[:200])
