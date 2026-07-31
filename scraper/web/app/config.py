from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/gmaps_pro"
    database_url_ro: Optional[str] = None
    scraper_control_token: SecretStr = Field(
        validation_alias="JAWNIX_SCRAPER_CONTROL_TOKEN",
        min_length=32,
    )
    control_dir: Path = Path("/app/control")
    keywords_path: Path = Path("/app/keywords.txt")
    active_states_path: Path = Path("/app/control/active_states.yaml")
    source_segments_path: Path = Path(
        "/app/control/runtime/source_segments.yaml"
    )
    exports_dir: Path = Path("/app/exports/by_state")
    worker_stale_secs: int = 180
    expected_workers: int = 8
    telemetry_stale_secs: int = 180
    queue_max_depth: int = 500
    queue_max_age_mins: float = 30
    max_retryable: int = 50
    max_empty_rate: float = 0.9
    max_spool_files: int = 200
    max_spool_age_mins: float = 15
    disk_warn_percent: float = 85
    memory_warn_percent: float = 90
    enqueue_trigger_mode: str = "sentinel"
    enqueue_trigger_path: Path = Path("/app/control/triggers/enqueue.request")
    pipeline_pause_path: Path = Path("/app/control/runtime/pipeline.paused")
    upload_max_bytes: int = 1_000_000
    db_command_timeout: int = 15
    openrouter_api_key: Optional[SecretStr] = None
    openrouter_model: str = "deepseek/deepseek-v4-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_secs: float = 45
    keyword_draft_ttl_hours: int = 24

    @property
    def keyword_ai_enabled(self) -> bool:
        return bool(self.openrouter_api_key and self.openrouter_api_key.get_secret_value().strip())

    @property
    def read_dsn(self) -> str:
        return self.database_url_ro or self.database_url
