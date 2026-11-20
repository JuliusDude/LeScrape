"""Build docs/ECOURTS_KARNATAKA_MAP.md plus full CSV lists from raw/state_3/_all.json (offline).

Usage (repo root): python docs/ecourts_map/build_state_report.py [state_code]
"""
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ST = sys.argv[1] if len(sys.argv) > 1 else "3"
SRC = os.path.join(HERE, "raw", f"state_{ST}", "_all.json")
CSV_DIR = os.path.join(HERE, "karnataka")
MD = os.path.join(os.path.dirname(HERE), "ECOURTS_KARNATAKA_MAP.md")
BLR = "20"

A = json.load(open(SRC, encoding="utf-8"))
os.makedirs(CSV_DIR, exist_ok=True)


def write_csv(name, header, rows):
    with open(os.path.join(CSV_DIR, name), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def ct_name(label):
    """'EX - Execution Petition Under Order' -> ('EX', 'Execution Petition Under Order')."""
    if " - " in label:
        a, b = label.split(" - ", 1)
        return a.strip(), b.strip()
    return label.strip(), ""


dist_rows, est_rows, ct_rows = [], [], []
per_complex = {k: [] for k in ("acts", "police_stations", "court_numbers", "cause_list_courts", "subordinate_courts")}
loc_ok = 0
n_cx = n_est = 0
ct_freq = collections.Counter()          # case-type abbreviation -> number of establishments offering it
ct_full = {}                             # abbreviation -> most common full name
ct_full_votes = collections.defaultdict(collections.Counter)
cx_sizes = []
for D in A["districts"]:
    d_est = sum(len(c["establishments"]) for c in D["complexes"])
    dist_rows.append([D["code"], D["name"], len(D["complexes"]), d_est])
    for C in D["complexes"]:
        n_cx += 1
        if C.get("location", {}).get("stat") == 1:
            loc_ok += 1
        cx_sizes.append((D["name"], C["name"], len(C["establishments"])))
        for k in per_complex:
            for v, label in C.get(k, []):
                per_complex[k].append([D["code"], D["name"], C["code"], C["name"], v, label])
        for E in C["establishments"]:
            n_est += 1
            est_rows.append([D["code"], D["name"], C["code"], C["name"], C["value"], E["code"], E["name"], len(E["case_types"])])
            seen = set()
            for v, label in E["case_types"]:
                ab, full = ct_name(label)
                ct_rows.append([D["code"], D["name"], C["code"], C["name"], E["code"], E["name"], v, ab, full])
                if ab not in seen:
                    ct_freq[ab] += 1
                    seen.add(ab)
                ct_full_votes[ab][full] += 1
for ab, votes in ct_full_votes.items():
    ct_full[ab] = votes.most_common(1)[0][0]

files = {
    "districts.csv": write_csv("districts.csv", ["dist_code", "district", "complexes", "establishments"], dist_rows),
    "establishments.csv": write_csv("establishments.csv", ["dist_code", "district", "complex_code", "complex", "complex_value",
                                                           "est_code", "establishment", "n_case_types"], est_rows),
    "case_types.csv": write_csv("case_types.csv", ["dist_code", "district", "complex_code", "complex", "est_code",
                                                   "establishment", "value", "abbrev", "name"], ct_rows),
}
for k, rows in per_complex.items():
    if k == "police_stations":
        continue
    files[f"{k}.csv"] = write_csv(f"{k}.csv", ["dist_code", "district", "complex_code", "complex", "value", "label"], rows)
# Police stations: each complex repeats ~3,000 near-identical entries (a 50 MB file), so store each
# station once with the number of complexes listing it, plus one count row per complex.
ps_count = collections.Counter((v, label) for *_, v, label in per_complex["police_stations"])
files["police_stations.csv"] = write_csv("police_stations.csv", ["value", "label", "complexes_listing"],
                                         [[v, label, n] for (v, label), n in sorted(ps_count.items(), key=lambda x: x[0][1])])
ps_per_cx = collections.Counter((d, dn, c, cn) for d, dn, c, cn, *_ in per_complex["police_stations"])
files["police_stations_per_complex.csv"] = write_csv("police_stations_per_complex.csv",
                                                     ["dist_code", "district", "complex_code", "complex", "n_police_stations"],
                                                     [[*k, n] for k, n in ps_per_cx.items()])
old_ps = os.path.join(CSV_DIR, "police_stations_full.csv")
files["case_type_catalogue.csv"] = write_csv("case_type_catalogue.csv", ["abbrev", "name", "establishments_offering"],
                                             [[ab, ct_full[ab], n] for ab, n in ct_freq.most_common()])

checks = A.get("checks", [])
bad = [c for c in checks if not c["acts_same_first_last"]]

# ---------------------------------------------------------------- markdown
L = []
w = L.append
w("# eCourts Karnataka: every court and every dropdown option\n")
w("Source: the live District Courts portal (`services.ecourts.gov.in/ecourtindia_v6/`), read on 2026-11-13.")
w("- Reads only: the dropdown-filling endpoints were called; no searches, no CAPTCHA.")
w(f"- Generated by `docs/ecourts_map/build_state_report.py` from `raw/state_{ST}/_all.json`.")
w(f"- Full lists are in [`docs/ecourts_map/karnataka/`](ecourts_map/karnataka/) as CSV.")
w("- General site structure: [`ECOURTS_SITE_MAP.md`](ECOURTS_SITE_MAP.md).\n")
complete = len(dist_rows) == A.get("n_districts", len(dist_rows))
w("## 1. Summary\n")
w("| | Count |\n|---|---|")
w(f"| Districts | {len(dist_rows)} |")
w(f"| Court complexes | {n_cx} |")
w(f"| Establishments | {n_est} |")
w(f"| Establishment-level case-type options (each is one Case Type search per year and status) | {len(ct_rows):,} |")
w(f"| Distinct case-type abbreviations | {len(ct_freq)} |")
for k, label in (("acts", "Act entries (summed over complexes)"), ("police_stations", "Police-station entries (summed over complexes)"),
                 ("court_numbers", "Judge-posting entries (summed over complexes)"), ("cause_list_courts", "Cause-list court entries (summed over complexes)"),
                 ("subordinate_courts", "Subordinate-court entries (summed over complexes)")):
    w(f"| {label} | {len(per_complex[k]):,} |")
w(f"| Complexes with map coordinates (Location service) | {loc_ok} of {n_cx} |")
w("")
w("**Rules observed** (all 22 Bengaluru establishments were fetched in full; the rest of the state was checked by sampling):")
w("- Only **case types** vary per establishment.")
w("- The Case Number, Case Type and Caveat case-type lists are identical.")
w("- Acts, police stations, judge postings, cause-list courts and subordinate courts are set **per court complex**.")
w(f"- Check across the state: for {len(checks)} complexes with two or more establishments, the acts list of the last establishment "
  + ("equalled the first in every case." if not bad else f"**differed from the first in {len(bad)}**: {', '.join(c['complex'] for c in bad)}."))
w("- About 1 in 200 dropdown replies came back empty although the list exists. These are retried (2 were found and re-fetched).")
w("- 88 complexes have no subordinate courts; that is normal (only district-level courts hear appeals from subordinate courts).")
w("- Court-complex values have the form `<complex>@<est,est,…>@<flag>`. Case-type values have the form `<type>^<est>`, and the same abbreviation can have a different type code in another establishment.\n")

w("## 2. Districts\n")
w("| Code | District | Complexes | Establishments |\n|---|---|---|---|")
for r in sorted(dist_rows, key=lambda r: r[1]):
    w(f"| {r[0]} | {r[1]}{' (scraped by V1/V2)' if r[0] == BLR else ''} | {r[2]} | {r[3]} |")
w("")

w("## 3. Bengaluru (district 20) in full\n")
blr = next((D for D in A["districts"] if D["code"] == BLR), None)
if blr:
    for C in blr["complexes"]:
        w(f"### {C['name']} (`{C['value']}`)\n")
        w(f"Shared by its establishments:")
        w(f"- {len(C.get('acts', []))} acts")
        w(f"- {len(C.get('police_stations', []))} police stations")
        w(f"- {len(C.get('court_numbers', []))} judge postings")
        w(f"- {len(C.get('cause_list_courts', []))} cause-list courts")
        w(f"- {len(C.get('subordinate_courts', []))} subordinate courts")
        loc = C.get("location", {})
        w(f"- Location: {'coordinates available' if loc.get('stat') == 1 else 'not available (' + str(loc.get('result')) + ')'}\n")
        w("| Est | Establishment | Case types (value → name) |\n|---|---|---|")
        for E in C["establishments"]:
            cts = "<br>".join(f"`{v}` {label}" for v, label in E["case_types"])
            w(f"| {E['code']} | {E['name']} | {cts} |")
        w("")
        cl = C.get("cause_list_courts", [])
        if cl:
            w("<details><summary>Cause-list courts (current court halls and judges)</summary>\n")
            w("\n".join(f"- `{v}` {label}" for v, label in cl if v != "D"))
            w("\n</details>\n")

w("## 4. Case-type catalogue across Karnataka\n")
w(f"The {len(ct_freq)} distinct abbreviations are mostly spelling variants of the same case types. The 100 most widely "
  "offered are below; the full list is in `case_type_catalogue.csv`, and every establishment's own list is in `case_types.csv`.\n")
w("| Abbrev | Name | Establishments |\n|---|---|---|")
for ab, n in ct_freq.most_common(100):
    w(f"| {ab} | {ct_full[ab]} | {n} |")
w("")

w("## 5. Largest court complexes\n")
w("| District | Complex | Establishments |\n|---|---|---|")
for d, c, n in sorted(cx_sizes, key=lambda x: -x[2])[:15]:
    w(f"| {d} | {c} | {n} |")
w("")

w("## 6. Files\n")
w("| File | Rows | Contents |\n|---|---|---|")
desc = {"districts.csv": "district → number of complexes and establishments",
        "establishments.csv": "every establishment with its complex value (as used in `set_data`)",
        "case_types.csv": "every case type of every establishment (`value` is what Case Type search sends as `case_type_1`)",
        "acts.csv": "Act dropdown, per complex", "police_stations.csv": "every distinct FIR police station, with how many complexes list it",
        "police_stations_per_complex.csv": "size of each complex's police-station dropdown (full per-complex lists: raw/state_3/*_police.json)",
        "court_numbers.csv": "Court Orders → Court Number: judge postings with date ranges, per complex",
        "cause_list_courts.csv": "Cause List court dropdown, per complex", "subordinate_courts.csv": "Caveat → Subordinate Court dropdown, per complex",
        "case_type_catalogue.csv": "distinct case types and how many establishments offer them"}
for f, n in files.items():
    w(f"| [`{f}`](ecourts_map/karnataka/{f}) | {n:,} | {desc.get(f, '')} |")
w("")
open(MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
print(f"wrote {MD}: districts={len(dist_rows)} complexes={n_cx} establishments={n_est} case_type_rows={len(ct_rows)}")
