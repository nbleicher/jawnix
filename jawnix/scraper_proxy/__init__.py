"""Scraper Operations: the native operations API used by the React workspace.

Split out of the former single-module ``scraper_proxy.py`` along its
sections; every public name it exposed is re-exported here so existing
importers (``jawnix.api``, the test suite) keep working unchanged.
"""

from __future__ import annotations

from ..keyword_generation import try_generation_lock
from ..mfa_provider import get_mfa_provider
from .core import (
    HEALTH_CACHE_SECONDS,
    ScraperProxyState,
    native_router,
)
from .grants import clear_scraper_grant

from . import coverage, database, entry, keywords, monitoring, runtime  # noqa: F401,E402
