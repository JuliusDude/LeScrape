"""
ecourts.parser.hierarchy
========================
Parser routines for eCourts India dropdown cascades:
  State -> District -> Court Complex -> Establishment -> Case Type
Handles compound tokens:
  Complex token: {complex_code}@{est_codes}@{differ_flag}
  Case type token: {case_type_code}^{est_code}
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from bs4 import BeautifulSoup

log = logging.getLogger("ecourts.parser.hierarchy")


def unwrap_json_or_dict(data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Ensure response is returned as a dict.

    If input is JSON string, parse it; if HTML or non-JSON, return empty dict.
    """
    if isinstance(data, dict):
        return data
    if not isinstance(data, str):
        return {}
    text = data.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return {}


def split_complex_token(complex_token: str) -> Tuple[str, List[str], str]:
    """Splits a court complex compound token into its constituent components:

    Format: "{court_complex_code}@{establishment_codes}@{differ_mast_est_flag}"
    Example: "1030136@15,16,17,18,19,20,21,23@Y" -> ("1030136", ["15", "16", ...], "Y")
    """
    parts = complex_token.split("@")
    complex_code = parts[0].strip() if len(parts) > 0 else ""
    est_codes: List[str] = []
    if len(parts) > 1 and parts[1].strip():
        est_codes = [c.strip() for c in parts[1].split(",") if c.strip()]
    differ_flag = parts[2].strip() if len(parts) > 2 else ""
    return complex_code, est_codes, differ_flag


def complex_code_numeric(full_complex_code: str) -> str:
    """Extract the numeric court complex code prefix from compound token

    (e.g., '1030135@3@Y' -> '1030135').
    """
    return full_complex_code.split("@")[0].strip()


def split_case_type_token(case_type_token: str) -> Tuple[str, str]:
    """Splits a case type compound token into case type code and establishment code:

    Format: "{case_type_code}^{est_code}"
    Example: "12^3" -> ("12", "3")
    """
    parts = case_type_token.split("^")
    code = parts[0].strip() if len(parts) > 0 else ""
    est = parts[1].strip() if len(parts) > 1 else ""
    return code, est


def parse_dropdown_options(
    response_data: Union[str, Dict[str, Any]],
    list_key: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Parse an eCourts dropdown HTML fragment into a list of dictionaries:

    [{'code': '...', 'name': '...'}, ...]

    Handles:
      1. JSON containing list_key (e.g. 'dist_list', 'complex_list')
      2. Raw HTML string containing <option> tags
    Filters out empty values and placeholder options like 'Select District'.
    """
    html_fragment = ""

    if isinstance(response_data, dict):
        if list_key and list_key in response_data:
            html_fragment = str(response_data[list_key])
        else:
            # Check common keys
            for k in ("dist_list", "complex_list", "establishment_list", "casetype_list"):
                if k in response_data:
                    html_fragment = str(response_data[k])
                    break
    elif isinstance(response_data, str):
        parsed_json = unwrap_json_or_dict(response_data)
        if parsed_json:
            if list_key and list_key in parsed_json:
                html_fragment = str(parsed_json[list_key])
            else:
                for k in ("dist_list", "complex_list", "establishment_list", "casetype_list"):
                    if k in parsed_json:
                        html_fragment = str(parsed_json[k])
                        break
        else:
            html_fragment = response_data

    if not html_fragment:
        return []

    soup = BeautifulSoup(html_fragment, "html.parser")
    options = soup.find_all("option")

    results: List[Dict[str, str]] = []
    for opt in options:
        val = opt.get("value", "").strip()
        name = opt.get_text(strip=True)
        # Skip empty value or placeholder options like 'Select ...'
        if not val:
            continue
        if name.lower().startswith("select"):
            continue
        results.append({"code": val, "name": name})

    return results


def parse_districts(response_data: Union[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    """Parse district options from fillDistrict response."""
    return parse_dropdown_options(response_data, "dist_list")


def parse_complexes(response_data: Union[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse court complex options from fillcomplex response.

    Augments each item with numeric_code, est_codes, and differ_flag.
    """
    raw_list = parse_dropdown_options(response_data, "complex_list")
    enriched: List[Dict[str, Any]] = []
    for item in raw_list:
        numeric, ests, flag = split_complex_token(item["code"])
        enriched.append({
            "code": item["code"],
            "name": item["name"],
            "numeric_code": numeric,
            "est_codes": ests,
            "differ_flag": flag,
        })
    return enriched


def parse_establishments(response_data: Union[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    """Parse court establishments from fillCourtEstablishment response."""
    return parse_dropdown_options(response_data, "establishment_list")


def parse_case_types(response_data: Union[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse case types from fillCaseType response.

    Augments each item with type_code and est_code.
    """
    raw_list = parse_dropdown_options(response_data, "casetype_list")
    enriched: List[Dict[str, Any]] = []
    for item in raw_list:
        type_code, est_code = split_case_type_token(item["code"])
        enriched.append({
            "code": item["code"],
            "name": item["name"],
            "type_code": type_code,
            "est_code": est_code,
        })
    return enriched
