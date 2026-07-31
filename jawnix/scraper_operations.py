"""Typed operations for the private Scraper control service."""

from __future__ import annotations

import logging
from typing import Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import Settings
from .scraper_coverage import (
    CoverageStates,
    ScraperStateCoverageDetail,
    StateCoverageCard,
    StateGridCoverage,
    StateKeywords,
)
from .scraper_database import (
    DatabaseExport,
    ExportRegeneration,
    MultiStateExportRequest,
    ScraperDatabaseStateDetail,
    ScraperDatabaseWorkspace,
    StateExportRequest,
)
from .scraper_keywords import (
    KeywordDiff,
    KeywordGenerateRequest,
    KeywordGenerationDraft,
    KeywordRollover,
    KeywordRolloverRequest,
    KeywordSaveRequest,
    KeywordSaveResult,
    KeywordTextRequest,
    KeywordWinner,
    ScraperKeywordWinners,
    ScraperKeywordWorkspace,
)
from .scraper_monitoring import (
    ControlPipelineRequest,
    ControlPipelineResult,
    RegionData,
    RegionKey,
)
from .scraper_runtime import (
    ControlCampaignHistory,
    ControlRuntimePreview,
    ControlRuntimeSaveRequest,
    ControlRuntimeSaveResult,
    ControlRuntimeWorkspace,
    HistorySort,
    RuntimePreviewRequest,
    SortDirection,
)


logger = logging.getLogger(__name__)
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class ScraperOperationsError(Exception):
    """A transport, protocol, or declared control-service failure."""

    def __init__(
        self,
        *,
        status_code: int | None = None,
        detail: str | None = None,
        transport_error: str | None = None,
    ) -> None:
        super().__init__(detail or transport_error or "Scraper Operations failed")
        self.status_code = status_code
        self.detail = detail
        self.transport_error = transport_error


class ScraperWorkspaceSummary(BaseModel):
    """Availability probe and private Scraper workspace summary."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    active_states: list[str]
    keyword_count: int = Field(ge=0)
    business_count: int = Field(ge=0)
    pipeline_state: Literal["pausing", "paused", "running", "stopped"]


class ScraperOperations(Protocol):
    async def workspace_summary(self) -> ScraperWorkspaceSummary: ...

    async def list_keywords(self) -> ScraperKeywordWorkspace: ...

    async def keyword_winners(self) -> list[KeywordWinner]: ...

    async def preview_keywords(self, payload: KeywordTextRequest) -> KeywordDiff: ...

    async def save_keywords(self, payload: KeywordSaveRequest) -> KeywordSaveResult: ...

    async def generate_keywords(
        self,
        payload: KeywordGenerateRequest,
    ) -> KeywordGenerationDraft: ...

    async def set_keyword_rollover(
        self,
        payload: KeywordRolloverRequest,
    ) -> KeywordRollover: ...

    async def database_workspace(
        self,
        *,
        search: str,
        state: str,
        page: int,
    ) -> ScraperDatabaseWorkspace: ...

    async def database_state(self, state: str) -> ScraperDatabaseStateDetail: ...

    async def export_database_state(
        self,
        state: str,
        payload: StateExportRequest,
    ) -> DatabaseExport: ...

    async def export_database_states(
        self,
        payload: MultiStateExportRequest,
    ) -> DatabaseExport: ...

    async def stored_database_export(self, filename: str) -> DatabaseExport: ...

    async def regenerate_database_exports(
        self,
        state: str,
    ) -> ExportRegeneration: ...

    async def coverage_states(self) -> list[StateCoverageCard]: ...

    async def coverage_state(self, state: str) -> ScraperStateCoverageDetail: ...

    async def coverage_state_keywords(self, state: str) -> StateKeywords: ...

    async def coverage_state_cells(self, state: str) -> StateGridCoverage: ...

    async def monitoring_dashboard(self) -> RegionData: ...

    async def monitoring_region(self, region: RegionKey) -> RegionData: ...

    async def control_pipeline(
        self,
        payload: ControlPipelineRequest,
    ) -> ControlPipelineResult: ...

    async def runtime_workspace(self) -> ControlRuntimeWorkspace: ...

    async def preview_runtime(
        self,
        payload: RuntimePreviewRequest,
    ) -> ControlRuntimePreview: ...

    async def save_runtime(
        self,
        payload: ControlRuntimeSaveRequest,
    ) -> ControlRuntimeSaveResult: ...

    async def campaign_history(
        self,
        *,
        search: str,
        state: str,
        sort: HistorySort,
        direction: SortDirection,
    ) -> ControlCampaignHistory: ...


class HTTPScraperOperations:
    """HTTP adapter for the Scraper service's bearer-authenticated JSON API."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = settings.scraper_ops_url.rstrip("/")
        self._token = settings.scraper_control_token
        self._timeout = settings.scraper_ops_timeout_seconds
        self._generation_timeout = (
            settings.scraper_ops_generation_timeout_seconds
        )
        self._transport = transport

    async def workspace_summary(self) -> ScraperWorkspaceSummary:
        return await self._request(
            "GET",
            "/api/workspace",
            ScraperWorkspaceSummary,
        )

    async def _request(
        self,
        method: str,
        path: str,
        response_model: type[ResponseModel],
        *,
        payload: dict | None = None,
        params: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> ResponseModel:
        if not self._base_url or not self._token:
            raise ScraperOperationsError()
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=timeout if timeout is not None else self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    json=payload,
                    params=params,
                )
        except httpx.RequestError as error:
            transport_error = type(error).__name__
            logger.warning(
                "Scraper Operations request failed method=%s path=%s "
                "transport_error=%s",
                method,
                path,
                transport_error,
            )
            raise ScraperOperationsError(
                transport_error=transport_error,
            ) from error

        if not 200 <= response.status_code < 300:
            detail: str | None = None
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("detail"), str):
                    detail = body["detail"]
            except ValueError:
                pass
            raise ScraperOperationsError(
                status_code=response.status_code,
                detail=detail,
            )
        try:
            return response_model.model_validate_json(response.content)
        except (ValidationError, ValueError) as error:
            logger.warning(
                "Scraper Operations returned an invalid contract "
                "method=%s path=%s",
                method,
                path,
            )
            raise ScraperOperationsError(status_code=response.status_code) from error

    async def list_keywords(self) -> ScraperKeywordWorkspace:
        return await self._request(
            "GET",
            "/api/keywords",
            ScraperKeywordWorkspace,
        )

    async def keyword_winners(self) -> list[KeywordWinner]:
        result = await self._request(
            "GET",
            "/api/keywords/winners",
            ScraperKeywordWinners,
        )
        return result.winners

    async def preview_keywords(self, payload: KeywordTextRequest) -> KeywordDiff:
        return await self._request(
            "POST",
            "/api/keywords/preview",
            KeywordDiff,
            payload=payload.model_dump(),
        )

    async def save_keywords(self, payload: KeywordSaveRequest) -> KeywordSaveResult:
        return await self._request(
            "POST",
            "/api/keywords/save",
            KeywordSaveResult,
            payload=payload.model_dump(exclude={"review_token"}),
        )

    async def generate_keywords(
        self,
        payload: KeywordGenerateRequest,
    ) -> KeywordGenerationDraft:
        return await self._request(
            "POST",
            "/api/keywords/generate",
            KeywordGenerationDraft,
            payload=payload.model_dump(),
            timeout=self._generation_timeout,
        )

    async def set_keyword_rollover(
        self,
        payload: KeywordRolloverRequest,
    ) -> KeywordRollover:
        return await self._request(
            "POST",
            "/api/keywords/rollover",
            KeywordRollover,
            payload=payload.model_dump(),
        )

    async def database_workspace(
        self,
        *,
        search: str,
        state: str,
        page: int,
    ) -> ScraperDatabaseWorkspace:
        return await self._request(
            "GET",
            "/api/database",
            ScraperDatabaseWorkspace,
            params={"search": search, "state": state, "page": page},
        )

    async def database_state(self, state: str) -> ScraperDatabaseStateDetail:
        return await self._request(
            "GET",
            f"/api/database/states/{state}",
            ScraperDatabaseStateDetail,
        )

    async def export_database_state(
        self,
        state: str,
        payload: StateExportRequest,
    ) -> DatabaseExport:
        return await self._request(
            "POST",
            f"/api/database/exports/state/{state}",
            DatabaseExport,
            payload=payload.model_dump(),
        )

    async def export_database_states(
        self,
        payload: MultiStateExportRequest,
    ) -> DatabaseExport:
        return await self._request(
            "POST",
            "/api/database/exports/states",
            DatabaseExport,
            payload=payload.model_dump(),
        )

    async def stored_database_export(self, filename: str) -> DatabaseExport:
        return await self._request(
            "GET",
            f"/api/database/exports/stored/{filename}",
            DatabaseExport,
        )

    async def regenerate_database_exports(
        self,
        state: str,
    ) -> ExportRegeneration:
        return await self._request(
            "POST",
            f"/api/database/exports/{state}/regenerate",
            ExportRegeneration,
        )

    async def coverage_states(self) -> list[StateCoverageCard]:
        result = await self._request(
            "GET",
            "/api/coverage",
            CoverageStates,
        )
        return result.states

    async def coverage_state(
        self,
        state: str,
    ) -> ScraperStateCoverageDetail:
        return await self._request(
            "GET",
            f"/api/coverage/{state}",
            ScraperStateCoverageDetail,
        )

    async def coverage_state_keywords(self, state: str) -> StateKeywords:
        return await self._request(
            "GET",
            f"/api/coverage/{state}/keywords",
            StateKeywords,
        )

    async def coverage_state_cells(self, state: str) -> StateGridCoverage:
        return await self._request(
            "GET",
            f"/api/coverage/{state}/cells",
            StateGridCoverage,
        )

    async def monitoring_dashboard(self) -> RegionData:
        return await self._request(
            "GET",
            "/api/dashboard",
            RegionData,
        )

    async def monitoring_region(self, region: RegionKey) -> RegionData:
        return await self._request(
            "GET",
            f"/api/dashboard/{region}",
            RegionData,
        )

    async def control_pipeline(
        self,
        payload: ControlPipelineRequest,
    ) -> ControlPipelineResult:
        return await self._request(
            "POST",
            "/api/pipeline",
            ControlPipelineResult,
            payload=payload.model_dump(),
        )

    async def runtime_workspace(self) -> ControlRuntimeWorkspace:
        return await self._request(
            "GET",
            "/api/runtime",
            ControlRuntimeWorkspace,
        )

    async def preview_runtime(
        self,
        payload: RuntimePreviewRequest,
    ) -> ControlRuntimePreview:
        return await self._request(
            "POST",
            "/api/runtime/preview",
            ControlRuntimePreview,
            payload=payload.model_dump(mode="json"),
        )

    async def save_runtime(
        self,
        payload: ControlRuntimeSaveRequest,
    ) -> ControlRuntimeSaveResult:
        return await self._request(
            "POST",
            "/api/runtime/save",
            ControlRuntimeSaveResult,
            payload=payload.model_dump(mode="json"),
        )

    async def campaign_history(
        self,
        *,
        search: str,
        state: str,
        sort: HistorySort,
        direction: SortDirection,
    ) -> ControlCampaignHistory:
        return await self._request(
            "GET",
            "/api/history",
            ControlCampaignHistory,
            params={
                "search": search,
                "state": state,
                "sort": sort,
                "direction": direction,
            },
        )
