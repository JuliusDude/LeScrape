"""
ecourts - Production-ready eCourts India scraper and persistent queue system.
"""

__version__ = "0.1.0"

from ecourts.core.client import PacedSession
from ecourts.core.captcha import (
    BaseCaptchaSolver,
    LocalOcrSolver,
    ManualInteractiveSolver,
    ThirdPartyApiSolver,
    MockCaptchaSolver,
    CaptchaHandler,
)
from ecourts.parser.case_details import CaseDetail, parse_case_detail, extract_cnr
from ecourts.parser.hierarchy import (
    parse_districts,
    parse_complexes,
    parse_establishments,
    parse_case_types,
    complex_code_numeric,
    split_complex_token,
    split_case_type_token,
)
from ecourts.parser.search import (
    parse_case_results,
    parse_view_params,
    build_search_payload,
    WrongCaptchaError,
)
from ecourts.storage.db import Database
from ecourts.queue.worker import QueueWorker
from ecourts.export.exporter import Exporter

__all__ = [
    "__version__",
    "PacedSession",
    "BaseCaptchaSolver",
    "LocalOcrSolver",
    "ManualInteractiveSolver",
    "ThirdPartyApiSolver",
    "MockCaptchaSolver",
    "CaptchaHandler",
    "CaseDetail",
    "parse_case_detail",
    "extract_cnr",
    "parse_districts",
    "parse_complexes",
    "parse_establishments",
    "parse_case_types",
    "complex_code_numeric",
    "split_complex_token",
    "split_case_type_token",
    "parse_case_results",
    "parse_view_params",
    "build_search_payload",
    "WrongCaptchaError",
    "Database",
    "QueueWorker",
    "Exporter",
]
