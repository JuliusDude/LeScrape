"""For each JS function in the portal's scripts: endpoint(s) called and POST parameter names."""
import re, json, glob, os
FILES = ["js_ecourtindia_v6_js_components.js", "js_ecourtindia_v6_js_searchByCaseStatus.js", "js_ecourtindia_v6_js_searchByCNR.js",
         "js_ecourtindia_v6_js_searchByCauselist.js", "js_ecourtindia_v6_js_searchByCourtOrder.js", "js_ecourtindia_v6_js_serachByCaveat.js",
         "js_ecourtindia_v6_js_processDetails.js", "js_ecourtindia_v6_js_common_header.js"]
def split_funcs(js):
    out = []
    for m in re.finditer(r"function\s+(\w+)\s*\(([^)]*)\)\s*\{", js):
        i = m.end(); depth = 1
        while i < len(js) and depth:
            depth += {"{": 1, "}": -1}.get(js[i], 0); i += 1
        out.append((m.group(1), m.group(2), js[m.start():i]))
    return out
res = {}
for f in FILES:
    p = "raw/" + [x for x in os.listdir("raw") if x.startswith(f.split("?")[0][:-3])][0]
    js = open(p, encoding="utf-8", errors="ignore").read()
    for name, args, body in split_funcs(js):
        b = re.sub(r"//[^\n]*", "", body); b = re.sub(r"/\*.*?\*/", "", b, flags=re.S)
        urls = re.findall(r"""url\s*[:=]\s*["']([^"']+)["']""", b)
        urls += re.findall(r"""["'](?:\?p=)?((?:casestatus|home|cause_list|caveat_search|courtorder|processDetails|cnr_status|location|villageSearch)/[A-Za-z_]+)["']""", b)
        params = re.findall(r"""[&"'](\w+)=["']?\s*\+""", b) + re.findall(r"""["']&?(\w+)=["']""", b)
        params = list(dict.fromkeys(p_ for p_ in params if p_ not in ("p",)))
        if urls:
            res.setdefault(os.path.basename(f), []).append({"fn": name, "args": args.replace(" ", ""), "urls": list(dict.fromkeys(urls)), "params": params})
json.dump(res, open("raw/js_endpoints.json", "w"), indent=1)
for f, fns in res.items():
    print("\n####", f)
    for r in fns:
        print(f"  {r['fn']}({r['args'][:70]}) -> {', '.join(r['urls'])}\n      params: {', '.join(r['params'])[:300]}")
