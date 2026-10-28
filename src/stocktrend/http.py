"""HTTP session shared by all ingesters.

We use ``requests`` rather than ``httpx``: Wikipedia's bot filter rejects httpx
even with a descriptive User-Agent.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from stocktrend.config import USER_AGENT

DEFAULT_TIMEOUT = 30


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    # Retry transient server and connection errors. 429s are left to callers,
    # since each source has its own rate-limit etiquette.
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s
