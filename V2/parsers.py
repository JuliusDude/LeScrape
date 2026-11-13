"""Pure HTML/JS/JSON parsers for the eCourts case-status site (no I/O)."""
from __future__ import annotations

import calendar
import datetime as _dt
import html
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

_WS = re.compile(r"\s+")
_CNR_RE = re.compile(r"\b[A-Z]{4}\d{12}\b")
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}

VB_ARG_NAMES = ["court_code", "dist_code", "nextdate1", "case_number1", "state_code", "disposal_flag",
                "businessDate", "court_no", "national_court_code", "search_by", "srno"]
VIEW_ARG_NAMES = ["case_no", "cino", "court_code", "hideparty", "search_flag", "state_code", "dist_code",
                  "court_complex_code", "search_by"]
PDF_ARG_NAMES = ["normal_v", "case_val", "court_code", "filename", "appFlag"]
IA_ARG_NAMES = ["ia_no", "cinoia", "ia_case_type_name", "ia_filno", "ia_filyear", "national_court_code", "search_by"]


def clean(s: Optional[str]) -> str:
    # The site double-escapes some entities ("&amp;#039;"); BeautifulSoup decodes one level, so a
    # literal "&#039;" / "&rsquo;" can remain. Decode the residue (seen live 2026-11-13).
    s = s or ""
    if "&" in s:
        s = html.unescape(s)
    return _WS.sub(" ", s.replace("\xa0", " ")).strip()


def to_iso(s: Optional[str]) -> Optional[str]:
    """dd-mm-yyyy | dd/mm/yyyy | yyyy-mm-dd | '02nd January 2023' -> 'YYYY-MM-DD' (None if unparsable)."""
    s = clean(s)
    if not s:
        return None
    try:
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
        if m:
            return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
        if m:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})$", s)
        if m and m.group(2).lower() in _MONTHS:
            return _dt.date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    except ValueError:
        return None
    return None


# --------------------------------------------------------------------------- JS call arguments
def split_js_args(s: str) -> List[str]:
    args: List[str] = []
    cur: List[str] = []
    q = None
    for ch in s:
        if q:
            if ch == q:
                q = None
            else:
                cur.append(ch)
        elif ch in "'\"":
            q = ch
        elif ch == ",":
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    args.append("".join(cur).strip())
    return args


def find_calls(text: str, fname: str) -> Iterator[List[str]]:
    """Yield the argument list of every ``fname(...)`` call found in text (quote-aware)."""
    for m in re.finditer(r"\b" + re.escape(fname) + r"\s*\(", text or ""):
        i = m.end()
        q = None
        j = i
        while j < len(text):
            ch = text[j]
            if q:
                if ch == q:
                    q = None
            elif ch in "'\"":
                q = ch
            elif ch == ")":
                break
            j += 1
        else:
            continue
        yield split_js_args(text[i:j])


def first_call(text: str, fname: str) -> Optional[List[str]]:
    for a in find_calls(text, fname):
        return a
    return None


# --------------------------------------------------------------------------- session / handshake
def parse_index(html: str) -> Tuple[str, str]:
    """(app_token, components.js URL as written in the page) from casestatus/index."""
    token = ""
    m = re.search(r"<input[^>]*\bid=[\"']app_token[\"'][^>]*>", html)
    if m:
        v = re.search(r"value=[\"']([^\"']*)[\"']", m.group(0))
        token = v.group(1) if v else ""
    c = re.search(r"src=[\"']([^\"']*components\.js\?v=[^\"']*)[\"']", html)
    return token, (c.group(1) if c else "")


def parse_components(js: str) -> Tuple[Optional[str], Optional[str]]:
    """(delimeter value, extra header name) read from the ajaxCall function."""
    start = js.find("function ajaxCall")
    seg = js[start:] if start >= 0 else js
    d = re.search(r"var\s+delimeter\s*=\s*[\"']([^\"']+)[\"']", seg)
    h = re.search(r"[\"']delimeter[\"']\s*:\s*delimeter\s*,\s*[\"']([^\"']+)[\"']\s*:\s*delimeter", seg)
    return (d.group(1) if d else None, h.group(1) if h else None)


def parse_captcha_src(div_captcha: str) -> str:
    m = re.search(r"<img[^>]*\bsrc=[\"']([^\"']+)[\"']", (div_captcha or "").replace("\\/", "/"))
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- search results
def parse_search_results(case_data: str) -> Dict[str, Any]:
    soup = BeautifulSoup((case_data or "").replace("</br>", "<br>"), "lxml")
    total = None
    m = re.search(r"Total number of cases\s*:\s*(\d+)", soup.get_text(" "))
    if m:
        total = int(m.group(1))
    rows: List[Dict[str, Any]] = []
    court = ""
    for tr in soup.find_all("tr"):
        th = tr.find("th", attrs={"colspan": True})
        if th is not None and not tr.find("td"):
            court = clean(th.get_text())
            continue
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        link = tr.find("a", onclick=True)
        args = first_call(link["onclick"], "viewHistory") if link else None
        strings = [clean(x) for x in tds[2].stripped_strings]
        pet, resp = "", ""
        idx = next((i for i, x in enumerate(strings) if re.fullmatch(r"(?i)vs\.?", x)), None)
        if idx is None:
            pet = " ".join(strings)
        else:
            pet, resp = " ".join(strings[:idx]), " ".join(strings[idx + 1:])
        case_no = clean(tds[1].get_text())
        parts = case_no.split("/")
        row = {
            "sr_no": clean(tds[0].get_text()), "case_number": case_no,
            "case_type": parts[0] if len(parts) == 3 else "",
            "case_year": parts[2] if len(parts) == 3 else "",
            "petitioner": pet, "respondent": resp, "parties": " vs ".join(x for x in (pet, resp) if x),
            "court_name": court, "view_args": args,
            "cnr": args[1] if args and len(args) > 1 else "",
            "view_params": dict(zip(VIEW_ARG_NAMES, args)) if args and len(args) >= 9 else None,
        }
        rows.append(row)
    return {"total": total, "rows": rows}


# --------------------------------------------------------------------------- case page
_H3 = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def split_sections(html: str) -> Tuple[str, List[Tuple[str, str]]]:
    pos = [(m.start(), m.end(), clean(_TAG.sub(" ", m.group(1)))) for m in _H3.finditer(html)]
    if not pos:
        return html, []
    sections = []
    for i, (_s, e, h) in enumerate(pos):
        end = pos[i + 1][0] if i + 1 < len(pos) else len(html)
        sections.append((h, html[e:end]))
    return html[: pos[0][0]], sections


def _soup(chunk: str) -> BeautifulSoup:
    # a leading stray closing tag makes lxml return an empty document, so wrap the fragment
    return BeautifulSoup("<div>" + chunk, "lxml")


def _label_rows(soup: BeautifulSoup) -> List[Tuple[str, List[Tag]]]:
    out: List[Tuple[str, List[Tag]]] = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        if any(c.name == "th" for c in cells):
            label, vals = None, []
            for c in cells:
                if c.name == "th":
                    if label is not None:
                        out.append((label, vals))
                    label, vals = clean(c.get_text()), []
                elif label is not None:
                    vals.append(c)
            if label is not None:
                out.append((label, vals))
        else:
            out.append((clean(cells[0].get_text()), cells[1:]))
    return [(l, v) for l, v in out if l]


def cell_text(c: Tag) -> str:
    """Cell text keeping <br> boundaries (joined with '; ') so adjacent lines never run together."""
    return "; ".join(_lines(c))


def _parts(cells: List[Tag]) -> List[str]:
    parts: List[str] = []
    for c in cells:
        strongs = c.find_all("strong")
        if strongs:
            parts += [clean(s.get_text()) for s in strongs if clean(s.get_text())]
        elif clean(c.get_text()):
            parts.append(clean(c.get_text()))
    return parts


def _lines(li: Tag) -> List[str]:
    lines, cur = [], []
    for node in li.descendants:
        if isinstance(node, Tag) and node.name == "br":
            lines.append("".join(cur))
            cur = []
        elif isinstance(node, NavigableString):
            cur.append(str(node))
    lines.append("".join(cur))
    return [clean(x) for x in lines if clean(x)]


def parse_parties(chunk: str) -> List[Dict[str, Any]]:
    soup = _soup(chunk)
    parties: List[Dict[str, Any]] = []
    orphan_adv: List[str] = []
    for li in soup.find_all("li"):
        cur = None
        first = True
        for line in _lines(li):
            m = re.match(r"^(\d+)\s*\)\s*(.*)$", line)
            a = re.match(r"^Advocate\s*[-:]\s*(.*)$", line, re.I)
            if m:
                cur = {"seq": len(parties) + 1, "number": m.group(1), "name": clean(m.group(2)),
                       "advocates": [], "extra_lines": []}
                parties.append(cur)
            elif a:
                target = cur or (parties[-1] if parties else None)
                if target is None:
                    orphan_adv.append(clean(a.group(1)))
                elif clean(a.group(1)):
                    target["advocates"].append(clean(a.group(1)))
            elif first or cur is None:
                cur = {"seq": len(parties) + 1, "number": "", "name": line, "advocates": [], "extra_lines": []}
                parties.append(cur)
            else:
                cur["extra_lines"].append(line)
            first = False
    for p in parties:
        p["advocate"] = "; ".join(p["advocates"])
    if orphan_adv and parties:
        parties[0]["extra_lines"].extend("Advocate- " + x for x in orphan_adv)
    return parties


def parse_acts(chunk: str) -> List[Dict[str, Any]]:
    out = []
    for tr in _soup(chunk).find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            sec = clean(tds[1].get_text())
            out.append({"seq": len(out) + 1, "act": clean(tds[0].get_text()), "section_raw": sec,
                        "section": sec.strip(" ,;")})
    return out


def parse_processes(chunk: str) -> List[Dict[str, Any]]:
    # the site emits malformed table markup here, so group every <td> in threes
    tds = [clean(t.get_text()) for t in _soup(chunk).find_all("td")]
    out = []
    for i in range(0, len(tds) - 2, 3):
        out.append({"seq": len(out) + 1, "process_id": tds[i], "title": tds[i + 1], "date": tds[i + 2],
                    "date_iso": to_iso(tds[i + 2])})
    return out


def parse_main_matters(chunk: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for label, vals in _label_rows(_soup(chunk)):
        low = label.lower().rstrip(": ").strip()
        val = vals[0] if vals else None
        text = cell_text(val) if val is not None else ""
        if "main case" in low:
            cnr = ""
            m = first_call(str(val), "funView_CinoHistory") if val is not None else None
            if m:
                cnr = m[0]
            else:
                f = _CNR_RE.search(text)
                cnr = f.group(0) if f else ""
            cur = {"seq": len(out) + 1, "case_no_raw": text, "case_no": clean(re.sub(r"\(.*$", "", text)),
                   "cnr": cnr, "filing_no": "", "extra": {}}
            out.append(cur)
        elif "main filing" in low:
            if cur is None or cur["filing_no"]:
                cur = {"seq": len(out) + 1, "case_no_raw": "", "case_no": "", "cnr": "", "filing_no": "", "extra": {}}
                out.append(cur)
            cur["filing_no"] = text
        else:
            if cur is None:
                cur = {"seq": 1, "case_no_raw": "", "case_no": "", "cnr": "", "filing_no": "", "extra": {}}
                out.append(cur)
            cur["extra"][label] = text
    return out


def parse_ia_status(chunk: str) -> List[Dict[str, Any]]:
    """Rows of the 'IA Status' table (class IAheading). Cells: IA number (link viewIABusiness(...)), parties
    (<br>-separated), filing date, 'next date <br>(purpose)', status."""
    soup = _soup(chunk)
    table = soup.find("table", class_="IAheading") or soup.find("table")
    out: List[Dict[str, Any]] = []
    if table is None:
        return out
    for tr in table.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 5:
            continue
        a = tds[0].find("a")
        args = first_call((a.get("onclick", "") or str(a)) if a is not None else "", "viewIABusiness")
        if args is None and a is not None:
            args = first_call(str(a), "viewIABusiness")
        nlines = _lines(tds[3])
        nd = nlines[0] if nlines and nlines[0].strip("- ") else ""
        pm = re.search(r"\((.*)\)", " ".join(nlines[1:]) if len(nlines) > 1 else "")
        fd = clean(tds[2].get_text())
        out.append({
            "seq": len(out) + 1, "ia_number": clean(tds[0].get_text()), "parties": _lines(tds[1]),
            "filing_date": fd, "filing_date_iso": to_iso(fd),
            "next_date": nd, "next_date_iso": to_iso(nd), "next_purpose": clean(pm.group(1)) if pm else "",
            "ia_status": clean(tds[4].get_text()), "args": args,
            "params": dict(zip(IA_ARG_NAMES, args)) if args and len(args) >= len(IA_ARG_NAMES) else None,
        })
    return out


def parse_ia_business(html: str) -> Dict[str, Any]:
    """viewIABusiness data_list: 'Case No:- X' + table(IA No | Business on Date | Hearing Date | Business); 0+ rows."""
    soup = BeautifulSoup("<div>" + (html or ""), "lxml")
    m = re.search(r"Case No\s*:-?\s*([^\s|]+)", soup.get_text(" "))
    rows: List[Dict[str, Any]] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 4:
            continue
        bod, hd = clean(tds[1].get_text()), clean(tds[2].get_text())
        rows.append({"seq": len(rows) + 1, "ia_no": clean(tds[0].get_text()),
                     "business_on_date": bod, "business_on_date_iso": to_iso(bod),
                     "hearing_date": hd, "hearing_date_iso": to_iso(hd), "business": cell_text(tds[3])})
    return {"case_no": m.group(1) if m else "", "rows": rows}


def parse_history(chunk: str) -> List[Dict[str, Any]]:
    soup = _soup(chunk)
    table = soup.find("table", class_="history_table") or soup.find("table")
    rows = []
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        a = tds[1].find("a")
        args = None
        if a is not None:
            args = first_call(a.get("onclick", "") or str(a), "viewBusiness")
        bod = clean(tds[1].get_text())
        hd = clean(tds[2].get_text())
        rows.append({
            "judge": clean(tds[0].get_text()), "business_on_date": bod, "business_on_date_iso": to_iso(bod),
            "hearing_date": hd, "hearing_date_iso": to_iso(hd),
            "purpose": clean(tds[3].get_text()) if len(tds) > 3 else "",
            "vb_args": args,
        })
    n = len(rows)
    for i, r in enumerate(rows):          # table is newest-first -> oldest gets seq 1
        r["seq"] = n - i
    return rows


def parse_orders(chunk: str, heading: str) -> List[Dict[str, Any]]:
    out = []
    for tr in _soup(chunk).find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        a = tds[2].find("a")
        label = clean(a.get("aria-label", "")) if a is not None else ""
        args = first_call((a.get("onclick", "") or str(a)) if a is not None else "", "displayPdf")
        if args is None and a is not None:
            args = first_call(str(a), "displayPdf")
        out.append({
            "section": heading, "category": clean(label.split("|")[0]) if "|" in label else "",
            "number": clean(tds[0].get_text()), "order_date": clean(tds[1].get_text()),
            "order_date_iso": to_iso(tds[1].get_text()), "description": label or clean(tds[2].get_text()),
            "link_text": clean(tds[2].get_text()), "pdf_args": args,
        })
    return out


_DETAIL_KEYS = {"case type": "case_type_raw", "filing number": "filing_number", "filing date": "filing_date",
                "registration number": "registration_number", "registration date": "registration_date",
                "e-filing number": "efiling_number", "e-filing date": "efiling_date"}
_STATUS_KEYS = {"first hearing date": "first_hearing_date", "decision date": "decision_date",
                "next hearing date": "next_hearing_date", "case stage": "case_stage",
                "nature of disposal": "nature_of_disposal", "court number and judge": "court_number_and_judge"}


def parse_case_page(html: str) -> Dict[str, Any]:
    """Parse a viewHistory / viewCNRHistory data_list fragment into a plain dict.
    Nothing is dropped silently: unknown sections go to ``extra_sections``; unknown labels to *_extra."""
    head, sections = split_sections(html or "")
    case: Dict[str, Any] = {
        "court_name": "", "case_type_raw": "", "case_type": "", "filing_number": "", "filing_date": "",
        "registration_number": "", "registration_date": "", "cnr": "", "efiling_number": "", "efiling_date": "",
        "first_hearing_date": "", "decision_date": "", "next_hearing_date": "", "case_stage": "",
        "case_status": "", "case_status_raw": "", "case_sub_stage": None, "nature_of_disposal": "",
        "court_number_and_judge": "", "ack_url": "", "national_court_code": "",
        "details_extra": {}, "status_extra": {}, "petitioners": [], "respondents": [], "acts": [],
        "processes": [], "interim_applications": [], "main_matters": [], "hearings": [], "orders": [], "extra_sections": [], "warnings": [],
    }
    h2 = _soup(head).find("h2")
    case["court_name"] = clean(h2.get_text()) if h2 else ""
    for heading, chunk in sections:
        low = heading.lower()
        if "case details" in low:
            for label, vals in _label_rows(_soup(chunk)):
                key = label.lower()
                txt = cell_text(vals[0]) if vals else ""
                if key == "cnr number":
                    f = _CNR_RE.search(" ".join(clean(v.get_text()) for v in vals))
                    case["cnr"] = f.group(0) if f else ""
                elif key in _DETAIL_KEYS:
                    case[_DETAIL_KEYS[key]] = txt
                else:
                    case["details_extra"][label] = txt
            ack = first_call(chunk, "display_case_acknowlegement")
            if ack:
                case["ack_url"] = ack[0]
                m = re.search(r"national_court_code=([A-Za-z0-9]+)", ack[0])
                if m:
                    case["national_court_code"] = m.group(1)
        elif "case status" in low:
            for label, vals in _label_rows(_soup(chunk)):
                key = label.lower()
                if key == "case status":
                    parts = _parts(vals)
                    case["case_status_raw"] = " ".join(parts)
                    case["case_status"] = parts[0] if parts else ""
                    for i, p in enumerate(parts):
                        if re.fullmatch(r"(?i)sub\s*stage\s*:?", p):
                            case["case_sub_stage"] = parts[i + 1] if i + 1 < len(parts) else ""
                    continue
                txt = " ".join(_parts(vals))
                if key in _STATUS_KEYS:
                    case[_STATUS_KEYS[key]] = txt
                else:
                    case["status_extra"][label] = txt
        elif low == "ia status" or low.startswith("ia status"):
            case["interim_applications"] = parse_ia_status(chunk)
        elif "petitioner" in low:
            case["petitioners"] = parse_parties(chunk)
        elif "respondent" in low:
            case["respondents"] = parse_parties(chunk)
        elif low == "acts" or low.startswith("act"):
            case["acts"] = parse_acts(chunk)
        elif "process" in low:
            case["processes"] = parse_processes(chunk)
        elif "main matter" in low:
            case["main_matters"] = parse_main_matters(chunk)
        elif "case history" in low or low == "history":
            case["hearings"] = parse_history(chunk)
        elif "order" in low or "judgement" in low or "judgment" in low:
            case["orders"].extend(parse_orders(chunk, heading))
        else:
            soup = _soup(chunk)
            case["extra_sections"].append({
                "heading": heading, "text": clean(soup.get_text(" ")),
                "tables": [[[cell_text(c) for c in tr.find_all(["th", "td"])] for tr in t.find_all("tr")]
                           for t in soup.find_all("table")],
            })
    for i, o in enumerate(case["orders"], 1):
        o["seq"] = i
    reg = case["registration_number"]
    ctype = case["case_type_raw"].split(" - ")[0].strip() if case["case_type_raw"] else ""
    case["case_type"] = ctype
    case["case_number"] = f"{ctype}/{reg}" if ctype and reg else reg
    if not case["national_court_code"]:
        for h in case["hearings"]:
            if h["vb_args"] and len(h["vb_args"]) > 8 and h["vb_args"][8]:
                case["national_court_code"] = h["vb_args"][8]
                break
    for k in ("filing_date", "registration_date", "efiling_date", "first_hearing_date", "decision_date",
              "next_hearing_date"):
        case[k + "_iso"] = to_iso(case[k])
    if not case["cnr"]:
        case["warnings"].append("cnr not found")
    if not case["hearings"]:
        case["warnings"].append("no hearings table found")
    return case


# --------------------------------------------------------------------------- viewBusiness / ack / pdf
def parse_business(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html or "", "lxml")
    out: Dict[str, Any] = {"business_text": "", "next_purpose": "", "next_hearing_date": "",
                           "next_hearing_date_iso": None, "judge": "", "header": {}, "extra": {}}
    texts: List[str] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) == 3 and clean(tds[1].get_text()) == ":":
            label = clean(tds[0].get_text()).lower()
            val = clean(tds[2].get_text())
            if label == "business":
                texts.append(val)
            elif label == "next purpose":
                out["next_purpose"] = val
            elif label == "next hearing date":
                out["next_hearing_date"] = val
                out["next_hearing_date_iso"] = to_iso(val)
            else:
                out["extra"][clean(tds[0].get_text())] = val
        elif len(tds) == 1 and tds[0].get("align") == "right" and tds[0].get("colspan"):
            out["judge"] = clean(tds[0].get_text())
    out["business_text"] = "\n".join(texts)
    for center in soup.find_all("center"):
        t = clean(center.get_text())
        for key, pat in (("in_court_of", r"^In the court of\s*:\s*(.*)$"), ("cnr", r"^CNR Number\s*:\s*(.*)$"),
                         ("case_number", r"^Case Number\s*:\s*(.*)$"), ("date", r"^Date\s*:\s*(.*)$")):
            m = re.match(pat, t)
            if m and not center.find("center") and key not in out["header"]:
                out["header"][key] = m.group(1).strip()
    return out


def parse_ack(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html or "", "lxml")
    fields: Dict[str, str] = {}
    for td in soup.find_all("td", attrs={"headers": True}):
        hid = td["headers"]
        hid = hid[0] if isinstance(hid, list) else hid
        lab = soup.find(id=hid)
        if lab is None:
            continue
        key = clean(lab.get_text().replace("&nbsp", " ")).rstrip(":").strip()
        if key in fields:
            n = 2
            while f"{key}#{n}" in fields:
                n += 1
            key = f"{key}#{n}"
        fields[key] = clean(td.get_text())
    court = soup.find(class_="court-name")
    return {
        "fields": fields, "court_name": clean(court.get_text()) if court else "",
        "decree_holder": fields.get("Decree Holder", ""), "judgement_debtor": fields.get("Judgement Debtor", ""),
        "cnr": fields.get("CNR Number", ""), "filing_number": fields.get("Filing Number", ""),
    }
