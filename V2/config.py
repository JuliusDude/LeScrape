"""Configuration dataclasses."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")


@dataclass
class Location:
    """Court complex the session is bound to (set_data) and searched."""
    state_code: str = "3"
    dist_code: str = "20"
    complex_code: str = "1030135"
    est_code: str = "3"
    differ_mast_est: str = "Y"


@dataclass
class SearchSpec:
    case_type: str = "23^3"
    year: str = "2023"
    status: str = "Disposed"          # Disposed | Pending | Both


@dataclass
class Config:
    base_url: str = BASE_URL
    user_agent: str = USER_AGENT
    connect_timeout: float = 10.0
    read_timeout: float = 40.0
    max_retries: int = 5              # transport / 5xx / 429 retries per request
    backoff_base: float = 0.6
    backoff_max: float = 20.0
    max_recover: int = 3              # re-handshakes per call before giving up
    handshake_attempts: int = 4
    max_rps: float = 1.0              # global ceiling across ALL workers (safe default)
    start_rps: float = 1.0            # initial rate when auto_tune is on
    auto_tune: bool = False
    tune_step: float = 0.25
    tune_interval: float = 60.0
    workers: int = 1
    captcha_attempts: int = 10
    captcha_length: int = 6
    case_retries: int = 2             # extra passes over failed cases
    follow_main_matters: bool = False
    fetch_pdfs: bool = True
    store_raw: bool = True
    resume: bool = False
    limit: Optional[int] = None
    breaker_window: int = 20
    breaker_threshold: float = 0.5
    breaker_cooldown: float = 8.0
    block_cooldown_base: float = 90.0   # BlockGuard: first cooldown, doubles per consecutive block
    block_cooldown_max: float = 900.0
    max_block_min: float = 30.0         # abort once total blocked time exceeds this
    max_handshakes: int = 20            # per session per run; more is treated as a block

    def to_dict(self):
        return asdict(self)
