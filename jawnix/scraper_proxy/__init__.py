"""Scraper Operations proxy: native operations API plus the mounted UI proxy.

Split out of the former single-module ``scraper_proxy.py`` along its
sections; every public name it exposed is re-exported here so existing
importers (``jawnix.api``, the test suite) keep working unchanged.
"""

from __future__ import annotations

from ..keyword_generation import try_generation_lock
from ..mfa_provider import get_mfa_provider
from .core import (
    HEALTH_CACHE_SECONDS,
    MOUNT_PREFIX,
    SCRAPER_SESSION_COOKIE,
    ScraperProxyState,
    _scraper_serializer,
    native_router,
)
from .grants import clear_scraper_grant

from . import coverage, database, entry, keywords, monitoring, runtime  # noqa: F401,E402
from .forwarding import forward_scraper_request  # noqa: E402
from .handoff import (  # noqa: E402
    accept_scraper_handoff,
    clear_scraper_session,
    request_is_scraper_origin,
    scraper_handoff_response,
    scraper_principal_from_request,
)
