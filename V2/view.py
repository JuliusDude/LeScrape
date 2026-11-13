"""Render one scraped case as a self-contained HTML page (offline, no network).

Usage:
    python -m V2.view <CNR> --out <run_dir> [--html <file>]
Reads <run_dir>/json/<CNR>.json (written by the exporter) and writes <run_dir>/view/<CNR>.html
unless --html is given. Every section of the stored record is shown, including order PDF text.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence

E = html.escape


def _v(x: Any) -> str:
    if x is None or x == "":
        return '<span class="na">—</span>'
    return E(str(x))


def _kv(rows: Iterable[Sequence[Any]]) -> str:
    body = "".join(f"<tr><th>{E(str(k))}</th><td>{_v(v)}</td></tr>" for k, v in rows)
    return f'<table class="kv">{body}</table>'


def _table(headers: List[str], rows: List[List[Any]], cls: str = "") -> str:
    if not rows:
        return '<p class="na">None recorded on the site.</p>'
    th = "".join(f"<th>{E(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c if isinstance(c, Raw) else _v(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table class="grid {cls}"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


class Raw(str):
    """Pre-rendered HTML cell."""


def _date(raw: Any, iso: Any) -> Any:
    if raw and iso and str(iso) not in str(raw):
        return Raw(f'{E(str(raw))} <span class="iso">{E(str(iso))}</span>')
    return raw


def _ratio(c: Dict[str, Any], a: str, b: str) -> Optional[str]:
    return f"{c[a]}/{c[b]}" if c.get(a) is not None and c.get(b) is not None else None


def _find_pdf(o: Dict[str, Any], run_dir: str) -> str:
    """Locate an order's PDF. PDFs are stored as <run_dir>/pdfs/<sha[:2]>/<sha>.pdf, so the sha256 finds
    them even if the stored pdf_path is stale (e.g. after the folder was renamed alternate_tool -> V2)."""
    sha = o.get("sha256") or ""
    cands = []
    if run_dir and len(sha) > 2:
        cands.append(os.path.join(run_dir, "pdfs", sha[:2], sha + ".pdf"))
    p = o.get("pdf_path") or ""
    if p:
        cands += [p, os.path.abspath(p)]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return ""


def _section(title: str, body: str, count: Optional[int] = None, sid: str = "") -> str:
    c = f' <span class="count">{count}</span>' if count is not None else ""
    return f'<section id="{E(sid)}"><h2>{E(title)}{c}</h2>{body}</section>'


def _parties(ps: List[Dict[str, Any]]) -> str:
    rows = []
    for p in ps:
        advs = p.get("advocates") or ([p["advocate"]] if p.get("advocate") else [])
        rows.append([p.get("seq"), p.get("name"), "; ".join(advs) or None,
                     "; ".join(p.get("extra_lines") or []) or None])
    return _table(["#", "Name", "Advocate(s)", "Other lines"], rows)


def render(case: Dict[str, Any], run_dir: str = "") -> str:
    c = case
    title = f'{c.get("case_number") or c.get("cnr")}'
    pet = "; ".join(p.get("name", "") for p in c.get("petitioners", []))
    res = "; ".join(p.get("name", "") for p in c.get("respondents", []))
    complete = c.get("complete") == 1
    flags = c.get("flags") or {}
    warn = list(c.get("warnings") or []) + [f"missing: {m}" for m in (c.get("missing") or [])]
    if flags:
        warn += [f"{k}: {v}" for k, v in flags.items()]

    hearings = c.get("hearings", [])
    orders = c.get("orders", [])
    nav = [("details", "Case details"), ("parties", "Parties"), ("acts", "Acts & processes"),
           ("main", "Main matters"), ("ia", "IA"), ("hearings", "Hearings"), ("orders", "Orders"),
           ("ack", "Acknowledgement"), ("prov", "Provenance")]

    parts: List[str] = []
    parts.append(_section("Case details", _kv([
        ("Court", c.get("court_name")), ("Case type", c.get("case_type_raw")),
        ("Filing number", c.get("filing_number")), ("Filing date", _date(c.get("filing_date"), c.get("filing_date_iso"))),
        ("Registration number", c.get("registration_number")),
        ("Registration date", _date(c.get("registration_date"), c.get("registration_date_iso"))),
        ("CNR", c.get("cnr")), ("e-Filing number", c.get("efiling_number")), ("e-Filing date", c.get("efiling_date")),
    ]) + _kv([
        ("First hearing date", _date(c.get("first_hearing_date"), c.get("first_hearing_date_iso"))),
        ("Decision date", _date(c.get("decision_date"), c.get("decision_date_iso"))),
        ("Next hearing date", _date(c.get("next_hearing_date"), c.get("next_hearing_date_iso"))),
        ("Case status", c.get("case_status")), ("Sub stage", c.get("case_sub_stage")), ("Case stage", c.get("case_stage")),
        ("Nature of disposal", c.get("nature_of_disposal")), ("Court number and judge", c.get("court_number_and_judge")),
    ] + [(k, v) for k, v in (c.get("details_extra") or {}).items()] + [(k, v) for k, v in (c.get("status_extra") or {}).items()]),
        sid="details"))

    parts.append(_section("Petitioners", _parties(c.get("petitioners", [])), len(c.get("petitioners", [])), "parties")
                 + _section("Respondents", _parties(c.get("respondents", [])), len(c.get("respondents", []))))

    acts = [[a.get("seq"), a.get("act"), a.get("section_raw") if a.get("section_raw") not in (None, "") else a.get("section")]
            for a in c.get("acts", [])]
    procs = [[p.get("seq"), p.get("process_id"), p.get("title"), _date(p.get("date"), p.get("date_iso"))]
             for p in c.get("processes", [])]
    parts.append(_section("Acts", _table(["#", "Act", "Section (as shown)"], acts), len(acts), "acts")
                 + _section("Processes", _table(["#", "Process ID", "Title", "Date"], procs), len(procs)))

    mm = c.get("main_matters", [])
    mm_rows = [[m.get("seq"), m.get("case_no"), m.get("main_cnr"),
                Raw(_v(m.get("filing_no"))
                    + (' <span class="flag">site artefact</span>' if m.get("filing_no_suspect") else ""))]
               for m in mm]
    parts.append(_section("Main matters", _table(["#", "Main case", "Main CNR", "Main filing no."], mm_rows) +
                          ('<p class="note">"Main Filing No." is a session counter on the site (same value for every case '
                           'in a session, rising between sessions), not real case data.</p>' if mm else ""),
                          len(mm), "main"))

    ias = c.get("interim_applications")
    if ias is not None:
        ia_html = []
        for ia in ias:
            biz = ia.get("business") or []
            ia_html.append(_kv([("IA number", ia.get("ia_number")), ("Parties", "; ".join(ia.get("parties") or [])),
                                ("Filed", _date(ia.get("filing_date"), ia.get("filing_date_iso"))),
                                ("Next date", ia.get("next_date")), ("Next purpose", ia.get("next_purpose")),
                                ("Status", ia.get("ia_status"))]) +
                           _table(["#", "Business on date", "Hearing date", "Business"],
                                  [[b.get("seq"), b.get("business_on_date"), b.get("hearing_date"), b.get("business")]
                                   for b in biz]))
        parts.append(_section("Interim applications", "".join(ia_html) or '<p class="na">None.</p>', len(ias), "ia"))
    else:
        extra = c.get("extra_sections") or []
        if extra:
            parts.append(_section("Other sections (generic capture)",
                                  "".join(f"<h3>{E(s.get('heading',''))}</h3><p>{_v(s.get('text'))}</p>" for s in extra),
                                  len(extra), "ia"))

    h_rows = []
    for h in sorted(hearings, key=lambda x: x.get("seq") or 0):
        biz = h.get("business_text")
        h_rows.append([h.get("seq"), _date(h.get("business_on_date"), h.get("business_on_date_iso")),
                       _date(h.get("hearing_date"), h.get("hearing_date_iso")), h.get("purpose"), h.get("judge"),
                       Raw(f'<div class="biz">{_v(biz)}</div>') if biz else (Raw('<span class="na">no link on site</span>')
                                                                            if not h.get("vb_args") else None),
                       h.get("next_purpose"), _date(h.get("next_hearing_date"), h.get("next_hearing_date_iso"))])
    parts.append(_section("Hearings (oldest first)", _table(
        ["#", "Business on date", "Hearing date", "Purpose", "Judge", "Business (daily status)", "Next purpose",
         "Next hearing"], h_rows, "hearings"), len(hearings), "hearings"))

    o_html = []
    for o in orders:
        path = _find_pdf(o, run_dir)
        link = (f'<a href="file:///{E(path.replace(os.sep, "/"))}">Open PDF</a>' if path and os.path.exists(path)
                else '<span class="na">PDF file not found locally</span>')
        o_html.append(
            f'<article class="order"><header><strong>{E(o.get("section") or "Order")}</strong> · Order {_v(o.get("number"))}'
            f' · {_v(o.get("order_date"))}</header>'
            + _kv([("Description", o.get("description")), ("Pages", o.get("pages")), ("Size (bytes)", o.get("size")),
                   ("SHA-256", o.get("sha256")), ("Needs OCR", "yes" if o.get("needs_ocr") else "no")])
            + f'<p>{link}</p><details><summary>Order text ({len(o.get("text") or "")} characters)</summary>'
              f'<pre>{E(o.get("text") or "")}</pre></details></article>')
    parts.append(_section("Orders and judgments", "".join(o_html) or '<p class="na">None.</p>', len(orders), "orders"))

    ack = c.get("ack") or {}
    parts.append(_section("Acknowledgement (cause title)", _kv(sorted((ack.get("fields") or {}).items())) if ack else
                          '<p class="na">Not fetched.</p>', None, "ack"))

    parts.append(_section("Provenance and completeness", _kv([
        ("Complete", "yes" if complete else "NO"),
        ("Business links fetched", _ratio(c, "vb_fetched", "vb_links")),
        ("PDFs saved", _ratio(c, "pdf_saved", "pdf_links")),
        ("Acknowledgement", _ratio(c, "ack_fetched", "ack_expected")),
        ("Requests for this case", c.get("requests")), ("Time for this case (s)", c.get("elapsed_s")),
        ("Scraped at (UTC)", c.get("scraped_at")), ("Run", c.get("run_id")),
        ("Search row", " — ".join(x for x in (c.get("search_case_number"), c.get("search_parties")) if x) or None),
    ]) + ("<ul class=\"warn\">" + "".join(f"<li>{E(str(w))}</li>" for w in warn) + "</ul>" if warn else ""),
        None, "prov"))

    status_badge = (f'<span class="badge ok">complete</span>' if complete else '<span class="badge bad">incomplete</span>')
    navhtml = "".join(f'<a href="#{a}">{E(t)}</a>' for a, t in nav)
    summary = (f'<div class="stats"><div><b>{len(hearings)}</b><span>hearings</span></div>'
               f'<div><b>{len(orders)}</b><span>orders</span></div>'
               f'<div><b>{sum((o.get("pages") or 0) for o in orders)}</b><span>PDF pages</span></div>'
               f'<div><b>{len(c.get("petitioners", [])) + len(c.get("respondents", []))}</b><span>parties</span></div></div>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)}</title>
<style>
:root{{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--accent:#2f5d8a;--ok:#2e7d4f;--bad:#b3261e;--warn:#8a5a00}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151517;--card:#1e1e21;--fg:#ececee;--muted:#a0a0a8;--line:#333338;--accent:#8db7e0;--ok:#6fcf97;--bad:#f28b82;--warn:#f2c46d}}}}
:root[data-theme="dark"]{{--bg:#151517;--card:#1e1e21;--fg:#ececee;--muted:#a0a0a8;--line:#333338;--accent:#8db7e0;--ok:#6fcf97;--bad:#f28b82;--warn:#f2c46d}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px 16px 64px}}
header.top h1{{margin:0;font-size:26px}}header.top p{{margin:4px 0;color:var(--muted)}}
.badge{{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;margin-left:8px;vertical-align:middle;border:1px solid}}
.badge.ok{{color:var(--ok)}}.badge.bad{{color:var(--bad)}}
nav{{position:sticky;top:0;background:var(--bg);padding:10px 0;margin:12px 0;border-bottom:1px solid var(--line);display:flex;gap:14px;flex-wrap:wrap;z-index:2}}
nav a{{color:var(--accent);text-decoration:none;font-size:14px}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}.stats div{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:110px}}
.stats b{{display:block;font-size:22px}}.stats span{{color:var(--muted);font-size:13px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}}
h2{{margin:0 0 12px;font-size:18px}}h3{{font-size:15px;margin:12px 0 6px}}.count{{color:var(--muted);font-weight:400;font-size:14px}}
table{{border-collapse:collapse;width:100%}}.kv{{margin-bottom:10px}}.kv th{{text-align:left;color:var(--muted);font-weight:500;width:230px;padding:5px 10px 5px 0;vertical-align:top}}
.kv td{{padding:5px 0;border-bottom:1px solid var(--line)}}
.scroll{{overflow-x:auto}}.grid th,.grid td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
.grid th{{font-size:13px;color:var(--muted);font-weight:600;white-space:nowrap}}.grid.hearings td:nth-child(6){{min-width:320px}}
.biz{{white-space:pre-wrap}}.na{{color:var(--muted)}}.iso{{color:var(--muted);font-size:12px;margin-left:4px}}
.flag{{color:var(--warn);font-size:12px;border:1px solid var(--warn);border-radius:6px;padding:0 5px;margin-left:6px}}
.note{{color:var(--muted);font-size:13px}}.order{{border:1px solid var(--line);border-radius:10px;padding:12px;margin:10px 0}}
.order header{{margin-bottom:8px}}pre{{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;max-height:520px;overflow:auto;font-size:13px}}
details summary{{cursor:pointer;color:var(--accent)}}.warn{{color:var(--warn)}}a{{color:var(--accent)}}
@media (max-width:640px){{.kv th{{width:42%}}}}
</style></head><body><div class="wrap">
<header class="top"><h1>{E(title)} {status_badge}</h1>
<p>{E(pet)} <em>vs</em> {E(res)}</p><p>CNR {E(c.get("cnr",""))} · {E(c.get("court_name") or "")} · {E(c.get("case_status") or "")}{(" · " + E(c.get("case_sub_stage"))) if c.get("case_sub_stage") else ""}</p></header>
{summary}<nav>{navhtml}</nav>
{"".join(parts)}
</div></body></html>"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m V2.view", description="Render one scraped case as HTML.")
    ap.add_argument("cnr", nargs="?", help="CNR to render (omit with --list)")
    ap.add_argument("--out", required=True, help="run directory (contains json/)")
    ap.add_argument("--html", help="output file (default <out>/view/<CNR>.html)")
    ap.add_argument("--list", action="store_true", help="list available CNRs")
    a = ap.parse_args(argv)
    jdir = os.path.join(a.out, "json")
    if a.list or not a.cnr:
        for f in sorted(os.listdir(jdir)):
            if f.startswith("KA") or (f.endswith(".json") and f != "index.json"):
                print(f[:-5])
        return 0
    src = os.path.join(jdir, a.cnr + ".json")
    if not os.path.exists(src):
        print(f"no such case: {src}", file=sys.stderr)
        return 1
    with open(src, encoding="utf-8") as f:
        case = json.load(f)
    dest = a.html or os.path.join(a.out, "view", a.cnr + ".html")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(render(case, a.out))
    print(os.path.abspath(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
