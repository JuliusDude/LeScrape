"""Dump tabs, form fields (labels, options) and buttons from each service page."""
import re, sys, json
from bs4 import BeautifulSoup
R = "raw/"
PAGES = {"CNR Number (home)": "ecourtindia_v6_p=home_index.html", "Case Status": "ecourtindia_v6_p=casestatus_index.html",
         "Court Orders": "ecourtindia_v6_p=courtorder_index.html", "Cause List": "ecourtindia_v6_p=cause_list_index.html",
         "Caveat Search": "ecourtindia_v6_p=caveat_search_index.html", "Process Details": "ecourtindia_v6_p=processDetails_processData.html",
         "Location": "ecourtindia_v6_p=location_index.html", "High Courts": "ecourtindia_v6_p=high_court_index.html"}
def lab(soup, el):
    if el.get("id"):
        l = soup.find("label", attrs={"for": el["id"]})
        if l: return " ".join(l.get_text(" ", strip=True).split())
    p = el.find_parent("label")
    return " ".join(p.get_text(" ", strip=True).split()) if p else ""
out = {}
for name, f in PAGES.items():
    soup = BeautifulSoup(open(R + f, encoding="utf-8").read(), "lxml")
    main = soup.find(id="main-content") or soup.find("main") or soup.body
    d = {"tabs": [], "fields": [], "buttons": []}
    for a in main.select("a[onclick], button[onclick], [role=tab], .nav-link, input[type=button][onclick]"):
        t = " ".join(a.get_text(" ", strip=True).split()) or a.get("value", "")
        oc = a.get("onclick", "") or a.get("data-bs-target", "") or a.get("href", "")
        if t and len(t) < 80: d["tabs" if (a.get("role") == "tab" or "nav-link" in (a.get("class") or [])) else "buttons"].append([t, oc[:120], a.get("id")])
    for el in main.select("input, select, textarea"):
        typ = el.get("type", el.name)
        if typ in ("hidden",) and not el.get("id", "").startswith(("sess", "court", "app")): continue
        rec = {"tag": el.name, "type": typ, "id": el.get("id"), "name": el.get("name"), "label": lab(soup, el),
               "placeholder": el.get("placeholder"), "value": el.get("value"), "onchange": (el.get("onchange") or el.get("onclick") or "")[:100]}
        if el.name == "select":
            opts = [(o.get("value"), " ".join(o.get_text(" ", strip=True).split())) for o in el.find_all("option")]
            rec["n_options"] = len(opts); rec["options"] = opts[:40]
        d["fields"].append(rec)
    out[name] = d
json.dump(out, open("raw/pages_analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for n, d in out.items():
    print(f"\n######## {n}")
    for t in d["tabs"]: print("  TAB", t)
    for b in d["buttons"][:60]: print("  BTN", b)
    for x in d["fields"]:
        print("  FLD", x["type"], x["id"], x["name"], repr(x["label"])[:50], x.get("placeholder") or "", x["value"] if x["type"] in ("radio", "checkbox") else "", x["onchange"][:60], f"opts={x.get('n_options')}" if x["tag"] == "select" else "")
