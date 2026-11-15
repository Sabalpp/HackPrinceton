"""Runtime configuration. Every value comes from environment variables (or backend/.env).

See backend/.env.example for documentation of each variable.
"""

import os
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Tests (APP_ENV=test set in the process env) never read a developer's .env, so local keys
    # and mock-mode switches can't leak into the suite.
    model_config = SettingsConfigDict(
        env_file=None if os.environ.get("APP_ENV") == "test" else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./farcare.db"
    # Optional, READ-ONLY source of live watch data (Tiger Data / TimescaleDB, the database the
    # collector writes air_health.* into). When blank, watch data is read from DATABASE_URL
    # (single-database deployments). FarCare never writes to this connection.
    tiger_database_url: str = ""
    cors_origins: str = "http://localhost:3000"
    background_tasks_enabled: bool = True
    session_hours: int = Field(default=24, ge=1, le=168)
    registration_enabled: bool = False
    worker_interval_seconds: float = Field(default=2.0, gt=0)
    worker_batch_size: int = Field(default=25, ge=1, le=100)
    worker_max_delivery_attempts: int = Field(default=5, ge=1, le=10)
    worker_retry_base_seconds: float = Field(default=5.0, gt=0)
    worker_retry_max_seconds: float = Field(default=300.0, gt=0)
    worker_heartbeat_stale_seconds: int = Field(default=120, gt=0)
    # Deployed commit (hosts set one of these automatically); shown on /health for deploy checks.
    git_commit: str = Field(
        default="",
        validation_alias=AliasChoices("GIT_COMMIT", "RENDER_GIT_COMMIT", "RAILWAY_GIT_COMMIT_SHA"),
    )

    # --- Auth -------------------------------------------------------------
    # Shared secret for machine-to-machine ingestion (fall events, health sync).
    ingest_api_key: str = ""
    # Demo mode enables /api/demo/* routes (admin token required).
    demo_mode: bool = True
    seed_demo_data: bool = True
    # false = start with no health data, so it all arrives through POST /api/health/observations
    # (from Sabal's sync, or scripts/mock_fitbit_sender.py standing in for it)
    seed_health_data: bool = True
    demo_child_token: str = "dev-child-token"  # noqa: S105 dev-only; refused in production
    demo_parent_token: str = "dev-parent-token"  # noqa: S105 dev-only; refused in production
    demo_admin_token: str = "dev-admin-token"  # noqa: S105 dev-only; refused in production

    # --- Demo identities (real handles live only in env, never in git) -----
    demo_parent_handle: str = "+15550100001"
    demo_child_handle: str = "+15550100002"
    demo_responder_handle: str = "+15550100003"
    demo_responder2_handle: str = "+15550100004"
    demo_emergency_number: str = ""

    # --- Photon 2.0 (Beta) --------------------------------------------------
    photon_api_key: str = ""
    photon_project_id: str = ""
    photon_webhook_secret: str = ""
    # iMessage shared assignments can have different agents. SMS uses one explicit sender.
    photon_platform: Literal["imessage", "sms"] = "imessage"
    photon_agent_user_id: str = ""
    # webhook: Photon POSTs to /webhooks/photon (needs public HTTPS)
    # poll:    backend polls list_messages (local dev without a tunnel)
    # off:     no inbound delivery
    photon_delivery: Literal["webhook", "poll", "off"] = "webhook"
    # auto: real Photon when PHOTON_API_KEY + PHOTON_PROJECT_ID are set, otherwise simulated.
    # console: always simulate (keep your keys in .env but run with mock people).
    messaging_channel: Literal["auto", "photon", "console"] = "auto"
    # Public web address of the app (e.g. https://farcare.example). When set, texts include
    # short links back to the right app screen (/parent, /respond, /). Leave blank to omit.
    public_app_url: str = ""
    photon_poll_interval_seconds: float = 2.0

    # --- Gemini -------------------------------------------------------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_seconds: float = 20.0

    # --- Deterministic safety / coordination policy -------------------------
    safety_check_timeout_seconds: int = 60
    emergency_responder_timeout_seconds: int = 180
    request_reply_timeout_seconds: int = 3600
    approval_timeout_seconds: int = 7200
    fall_dedupe_window_seconds: int = 600
    family_check_timeout_seconds: int = 300  # "Mom isn't answering" safety check window

    # --- Proactive wellness check-ins (at most once per parent per local day) ---
    proactive_checkins_enabled: bool = True
    proactive_checkin_hours: str = "9-19"  # parent's local hours [start, end)
    proactive_quiet_after_chat_hours: int = 6  # don't interrupt an active conversation
    tick_interval_seconds: float = 5.0

    @property
    def photon_enabled(self) -> bool:
        return bool(self.photon_api_key and self.photon_project_id)

    @property
    def use_photon(self) -> bool:
        return self.photon_enabled and self.messaging_channel != "console"

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.use_photon and self.photon_platform == "sms" and not self.photon_agent_user_id:
            raise ValueError("SMS delivery requires PHOTON_AGENT_USER_ID for the approved sender.")
        if self.app_env == "production":
            if self.messaging_channel == "photon" and not self.photon_enabled:
                raise ValueError(
                    "Photon mode requires a project ID and API key; no silent console fallback."
                )
            if self.demo_mode or self.seed_demo_data or self.seed_health_data:
                raise ValueError("Production requires demo mode and demo data seeding disabled.")
            if self.background_tasks_enabled:
                raise ValueError(
                    "Production runs app.worker separately; disable API background tasks."
                )
            if self.database_url.startswith("sqlite"):
                raise ValueError("Production requires PostgreSQL for durable worker coordination.")
            if not self.cors_origin_list or any(
                not origin.startswith("https://") or "*" in origin
                for origin in self.cors_origin_list
            ):
                raise ValueError("Production requires explicit HTTPS origins for browser sessions.")
            if not self.ingest_api_key:
                raise ValueError("Production requires INGEST_API_KEY.")
            if self.photon_delivery == "webhook" and self.photon_enabled:
                if not self.photon_webhook_secret:
                    raise ValueError("Webhook delivery requires PHOTON_WEBHOOK_SECRET.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
