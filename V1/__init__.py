"""
V1
===========
Specialized deeper extraction package for eCourts India, capturing
Processes, Main Matters, Final Orders/Judgments, Disposal specifics,
e-Filing, and relational hearing logs.
"""

from V1.models import (
    ActItem,
    DeeperCaseDetail,
    HearingItem,
    MainMatterItem,
    OrderItem,
    PartyItem,
    ProcessItem,
)
from V1.parser import parse_deeper_case_detail
from V1.db import DeeperDatabase

# exporter / scraper symbols are loaded lazily so that importing the parser,
# db or models does not require openpyxl, requests, PIL, etc.
_LAZY = {
    "export_to_json": "V1.exporter",
    "export_to_csv_flat": "V1.exporter",
    "export_to_multisheet_excel": "V1.exporter",
    "DeeperScraper": "V1.scraper",
}


def __getattr__(name):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value

__all__ = [
    "ActItem",
    "DeeperCaseDetail",
    "HearingItem",
    "MainMatterItem",
    "OrderItem",
    "PartyItem",
    "ProcessItem",
    "parse_deeper_case_detail",
    "DeeperDatabase",
    "export_to_json",
    "export_to_csv_flat",
    "export_to_multisheet_excel",
    "DeeperScraper",
]
