"""Serving for the redesigned React shell.

The shell is compiled by Vite into a directory of content-hashed assets plus a
single ``index.html``. Two caching rules follow from that layout and are the
reason this is a hand-written router rather than a ``StaticFiles`` mount:

* Hashed assets are immutable — their name changes whenever their bytes do, so
  they can be cached for a year.
* ``index.html`` names the current hashes, so it must never be cached; a stale
  copy would point the browser at assets a deploy has already removed.

Everything lives under ``/app``. The current static UI keeps the site root until
the cutover, so this module deliberately registers no route outside that prefix.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from .config import Settings, get_settings

MOUNT_PREFIX = "/app"
ASSETS_SEGMENT = "assets"

# Hashed filenames make an asset's content immutable for the life of that name.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
# The document names the current hashes, so it is always revalidated.
DOCUMENT_CACHE_CONTROL = "no-store, must-revalidate"


def _headers(cache_control: str) -> dict[str, str]:
    return {
        "Cache-Control": cache_control,
        # The shell is non-public until cutover.
        "X-Robots-Tag": "noindex, nofollow",
    }


def register_frontend_shell(app: FastAPI) -> None:
    """Register the shell routes on ``app``."""

    @app.get(f"{MOUNT_PREFIX}/{ASSETS_SEGMENT}/{{asset_path:path}}")
    def serve_asset(
        asset_path: str,
        settings: Settings = Depends(get_settings),
    ) -> Response:
        assets_root = (settings.frontend_dist_dir / ASSETS_SEGMENT).resolve()
        candidate = (assets_root / asset_path).resolve()

        # Resolve first, then confirm containment: this rejects `..` segments,
        # absolute paths, and symlinks that escape the build directory.
        #
        # A missing asset stays a 404 rather than falling back to the document —
        # returning HTML for a stale bundle reference surfaces as an unreadable
        # MIME-type error instead of a clear miss.
        if not candidate.is_relative_to(assets_root) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Not found.")

        return FileResponse(candidate, headers=_headers(IMMUTABLE_CACHE_CONTROL))

    @app.get(MOUNT_PREFIX)
    @app.get(f"{MOUNT_PREFIX}/")
    @app.get(f"{MOUNT_PREFIX}/{{spa_path:path}}")
    def serve_shell_document(settings: Settings = Depends(get_settings)) -> Response:
        """Serve the shell document for every application route.

        The client router owns the path, so a hard refresh or a pasted deep link
        such as ``/app/admin/fulfillment`` must return the same document rather
        than 404.
        """
        index: Path = settings.frontend_dist_dir / "index.html"
        if not index.is_file():
            # The image can start before the build artefact ships. Report that
            # as a dependency being unavailable, not as a bug.
            raise HTTPException(
                status_code=503,
                detail="The Jawnix application is not available. Try again shortly.",
            )

        return FileResponse(
            index,
            media_type="text/html",
            headers=_headers(DOCUMENT_CACHE_CONTROL),
        )
