from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./jawnix-dev.db"
    session_secret: str = Field(default="development-only-change-me", alias="JAWNIX_SESSION_SECRET")
    session_ttl_seconds: int = Field(default=86400, alias="JAWNIX_SESSION_TTL_SECONDS")
    cookie_secure: bool = Field(default=True, alias="JAWNIX_COOKIE_SECURE")
    billing_enabled: bool = Field(default=False, alias="JAWNIX_ENABLE_BILLING")
    frontend_dist_dir: Path = Field(default=Path("./frontend/dist"), alias="JAWNIX_FRONTEND_DIST_DIR")
    public_base_url: str = Field(default="http://localhost:8080", alias="JAWNIX_PUBLIC_BASE_URL")

    supabase_url: str = Field(default="", alias="JAWNIX_SUPABASE_URL")
    supabase_anon_key: str = Field(default="", alias="JAWNIX_SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    admin_mfa_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        alias="JAWNIX_ADMIN_MFA_MAX_ATTEMPTS",
    )
    admin_mfa_attempt_window_seconds: int = Field(
        default=900,
        ge=60,
        le=86400,
        alias="JAWNIX_ADMIN_MFA_ATTEMPT_WINDOW_SECONDS",
    )
    admin_mfa_lock_seconds: int = Field(
        default=900,
        ge=60,
        le=86400,
        alias="JAWNIX_ADMIN_MFA_LOCK_SECONDS",
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    telegram_approver_user_ids: str = Field(default="", alias="TELEGRAM_APPROVER_USER_IDS")
    telegram_webhook_secret: str = Field(default="", alias="TELEGRAM_WEBHOOK_SECRET")

    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    resend_webhook_secret: str = Field(default="", alias="RESEND_WEBHOOK_SECRET")
    batch_from_email: str = Field(default="Jawnix <hai@jawnix.com>", alias="JAWNIX_BATCH_FROM_EMAIL")
    # The address a Customer is pointed at when a Batch Request needs a human.
    support_email: str = Field(default="hai@jawnix.com", alias="JAWNIX_SUPPORT_EMAIL")
    batch_dir: Path = Field(default=Path("./batches"), alias="JAWNIX_BATCH_DIR")
    batch_retention_days: int = Field(default=30, alias="JAWNIX_BATCH_RETENTION_DAYS")
    global_cooldown_days: int = Field(default=7, alias="JAWNIX_GLOBAL_COOLDOWN_DAYS")
    worker_poll_seconds: float = Field(default=2.0, alias="JAWNIX_WORKER_POLL_SECONDS")
    worker_id: str = Field(default="worker-1", alias="JAWNIX_WORKER_ID")
    job_lock_timeout_seconds: int = Field(default=900, alias="JAWNIX_JOB_LOCK_TIMEOUT_SECONDS")

    scraper_db_path: Path = Field(default=Path("/data/health_leads/data/leads.db"), alias="JAWNIX_SCRAPER_DB_PATH")
    scraper_command: str = Field(default="", alias="JAWNIX_SCRAPER_COMMAND")
    scraper_hour_utc: int = Field(default=3, alias="JAWNIX_SCRAPER_HOUR_UTC")
    scraper_ops_url: str = Field(default="", alias="JAWNIX_SCRAPER_OPS_URL")
    scraper_ops_origin: str = Field(
        default="",
        alias="JAWNIX_SCRAPER_OPS_ORIGIN",
    )
    scraper_ops_user: str = Field(
        default="admin",
        alias="JAWNIX_SCRAPER_OPS_USER",
    )
    scraper_ops_password: str = Field(
        default="",
        alias="JAWNIX_SCRAPER_OPS_PASSWORD",
    )
    scraper_control_token: str = Field(
        default="",
        alias="JAWNIX_SCRAPER_CONTROL_TOKEN",
    )
    #: The Scraper database page runs several live aggregates over ~772k rows and
    #: measured 11.3s against production on 2026-07-29 — count(DISTINCT phone)
    #: alone is 1.4s. A 10s ceiling sat *below* a known-good response, so the
    #: screen reported "the private record store did not respond" for a request
    #: that would have succeeded a second later. 30s keeps the guard against a
    #: genuinely hung upstream without failing a slow-but-working one.
    scraper_ops_timeout_seconds: float = Field(
        default=30,
        alias="JAWNIX_SCRAPER_OPS_TIMEOUT_SECONDS",
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        alias="OPENROUTER_API_KEY",
    )
    openrouter_model: str = Field(
        default="deepseek/deepseek-v4-flash",
        alias="OPENROUTER_MODEL",
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )
    #: One wall-clock budget covers every adaptive model attempt. It is not a
    #: per-request transport timeout, so three attempts cannot each consume the
    #: full allowance.
    keyword_generation_deadline_seconds: float = Field(
        default=180,
        gt=0,
        alias="JAWNIX_KEYWORD_GENERATION_DEADLINE_SECONDS",
    )
    scraper_publication_hour_utc: int = Field(
        default=8,
        alias="JAWNIX_SCRAPER_PUBLICATION_HOUR_UTC",
    )
    nightly_review_hour_utc: int = Field(
        default=9,
        alias="JAWNIX_NIGHTLY_REVIEW_HOUR_UTC",
    )
    nightly_review_retry_minutes: int = Field(
        default=15,
        alias="JAWNIX_NIGHTLY_REVIEW_RETRY_MINUTES",
    )
    recommendation_shadow_mode: bool = Field(
        default=False,
        alias="JAWNIX_RECOMMENDATION_SHADOW_MODE",
    )
    recommendation_apply_enabled: bool = Field(
        default=True,
        alias="JAWNIX_RECOMMENDATION_APPLY_ENABLED",
    )

    @property
    def telegram_approvers(self) -> set[str]:
        return {value.strip() for value in self.telegram_approver_user_ids.split(",") if value.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
