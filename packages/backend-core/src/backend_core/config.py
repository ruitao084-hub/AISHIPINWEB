"""Application settings (taskbook §7, P2-T01).

One validated object, parsed once at process start, shared by the API, the
worker and the render worker. Configuration is never read from ``os.environ``
at a call site: a typo there fails at request time in production, whereas a
missing or malformed value here fails at boot.

Secrets are typed ``SecretStr`` so they cannot escape through an accidental
``repr()`` or ``model_dump()`` — §63 forbids logging credentials, and the
easiest way to honour that is to make leaking them structurally hard.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "staging", "production"]
# QualityMode used to be declared here as a Literal. It moved to
# `backend_core.domain.enums` in PHASE 7, where it is persisted on the
# project row — two spellings of the same closed set is how they drift.
MockVideoMode = Literal["success", "fail", "timeout", "slow"]

#: Failure injection for the mock vision provider (§172). Each mode reaches a
#: different branch in the caller: `malformed` is a 200 whose body fails schema
#: validation, which must not be mistaken for a transport failure, and `empty`
#: is a provider that worked and found nothing.
MockVisionMode = Literal[
    "success",
    "unavailable",
    "rate_limited",
    "rejected",
    "malformed",
    "empty",
]

#: How much reasoning the vision model spends per call. Product analysis is
#: description, not deduction, so the cheap end of the ladder is the honest
#: default; the setting exists because a customer with difficult imagery can
#: raise it without a deploy (§122).
VisionEffort = Literal["low", "medium", "high", "xhigh", "max"]

#: Failure injection for the mock LLM provider (§172). Same taxonomy as the
#: vision mock, minus `empty` — an empty creative plan set fails schema
#: validation rather than being a distinct outcome, so it is `malformed`.
MockLLMMode = Literal[
    "success",
    "unavailable",
    "rate_limited",
    "rejected",
    "malformed",
]


class Settings(BaseSettings):
    """Environment-backed configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ------------------------------------------------------
    app_env: AppEnv = "development"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    debug: bool = False

    # --- CORS (§61) -------------------------------------------------------
    # Defaults cover local development only. Production must set this
    # explicitly; a wildcard with credentials is rejected below.
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Whether `X-Forwarded-For` may be believed (§60, §61). Off by default:
    # the header is attacker-controlled unless a proxy is actually in front,
    # and trusting it without one puts forged addresses into the audit trail
    # and lets a per-IP rate limit be bypassed by setting a header.
    trust_proxy_headers: bool = False

    # --- Database (§4.4) --------------------------------------------------
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aipvs"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    # Recycle below typical proxy/database idle timeouts so a pooled connection
    # is never handed out after the server has already dropped it.
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)
    database_echo: bool = False

    # --- Redis and Celery (§4.5) ------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = Field(default=50, ge=1)
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Auth (§39) -------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl: int = Field(default=900, ge=60)
    jwt_refresh_token_ttl: int = Field(default=2_592_000, ge=3600)

    # --- Object storage (§4.6, §11) ---------------------------------------
    s3_endpoint: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "aipvs-dev"
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_public_base_url: str | None = None
    # MinIO and most self-hosted S3 implementations need path-style addressing.
    s3_force_path_style: bool = True
    s3_signed_url_ttl_seconds: int = Field(default=900, ge=60, le=604_800)

    # --- AI providers (§20, §138) -----------------------------------------
    openai_api_key: SecretStr = SecretStr("")
    google_ai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    runway_api_key: SecretStr = SecretStr("")
    tts_provider: str = "mock"
    tts_api_key: SecretStr = SecretStr("")
    default_video_provider: str = "mock"
    default_llm_provider: str = "mock"
    default_vision_provider: str = "mock"
    default_image_provider: str = "mock"

    # --- Vision analysis (§14, §20) ---------------------------------------
    #: Pinned rather than floating: §15 records the prompt version of every
    #: call so results stay explainable, and a model that silently changed
    #: underneath would make that record a half-truth.
    anthropic_vision_model: str = "claude-opus-5"
    anthropic_vision_effort: VisionEffort = "medium"

    # --- Creative and script generation (§16, §17) ------------------------
    anthropic_llm_model: str = "claude-opus-5"
    #: Higher than the vision default: writing three genuinely distinct
    #: concepts and a script that fits a character budget is a reasoning task,
    #: not a description task, and the cheap tier shows.
    anthropic_llm_effort: VisionEffort = "high"
    llm_timeout_seconds: float = Field(default=300.0, gt=0)
    #: §107 asks for a retry when structured output fails to parse. Two
    #: attempts, because a third rarely helps and every one is billed.
    llm_parse_retries: int = Field(default=1, ge=0, le=3)

    # --- Video generation (§21, §22) --------------------------------------
    runway_video_model: str = "gen4_turbo"
    #: Runway truncates long prompts server-side; truncating here keeps what
    #: was actually sent recorded on the provider_jobs row.
    runway_prompt_max_chars: int = Field(default=1000, ge=100)
    video_request_timeout_seconds: float = Field(default=60.0, gt=0)
    #: How often a worker asks a provider how a submitted job is doing.
    #: §26 suggests 2-5s for the *frontend*; a worker polling a paid API that
    #: fast is just spending rate limit on an answer that has not changed.
    video_poll_interval_seconds: float = Field(default=10.0, gt=0)
    #: Wall-clock ceiling for one generation before the job is TIMEOUT (§161).
    video_job_timeout_seconds: int = Field(default=1800, ge=60)

    #: Cap on images per analysis call. Vision pricing is per image, and a
    #: product with forty photographs produces an expensive request whose extra
    #: frames add nothing — the first few cover it from every useful angle.
    vision_max_images: int = Field(default=6, ge=1, le=20)
    #: Wall-clock budget for one vision call, generous because a thinking model
    #: on six images is not fast, but bounded so a hung request cannot pin a
    #: worker thread indefinitely (§24).
    vision_timeout_seconds: float = Field(default=180.0, gt=0)

    # --- Media toolchain (§4.7, §35) --------------------------------------
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffmpeg_timeout_seconds: int = Field(default=1800, ge=30)

    # --- Observability (§63) ----------------------------------------------
    sentry_dsn: SecretStr = SecretStr("")
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "aipvs-api"

    # --- Upload limits (§12) ----------------------------------------------
    max_upload_image_mb: int = Field(default=20, ge=1)
    max_upload_video_mb: int = Field(default=500, ge=1)
    max_project_duration_seconds: int = Field(default=120, ge=1)

    #: Decompression-bomb ceiling. A 200 KB PNG can declare 60000x60000 and
    #: expand to tens of gigabytes when decoded, so the byte limit above does
    #: not bound memory on its own (§12 "图片像素", §151).
    max_upload_image_megapixels: int = Field(default=50, ge=1)
    #: Single-dimension cap. Beyond this, encoders and browsers start failing
    #: regardless of total pixel count.
    max_upload_image_dimension: int = Field(default=16384, ge=1)
    #: Source footage longer than this is rejected at upload rather than
    #: discovered during a render (§12 "视频时长").
    max_upload_video_duration_seconds: int = Field(default=900, ge=1)
    #: Wall-clock budget for probing an uploaded file. ffprobe reads container
    #: headers over ranged HTTP, so this bounds a pathological file (moov atom
    #: at the end, deeply fragmented) rather than a normal one.
    media_probe_timeout_seconds: int = Field(default=30, ge=1)

    # --- Worker concurrency (§25) -----------------------------------------
    video_generation_concurrency: int = Field(default=4, ge=1)
    tts_concurrency: int = Field(default=4, ge=1)
    render_concurrency: int = Field(default=2, ge=1)
    qc_concurrency: int = Field(default=2, ge=1)

    # --- Job behaviour (§24, §161) ----------------------------------------
    job_max_retries: int = Field(default=3, ge=0)
    job_retry_base_delay_seconds: int = Field(default=5, ge=1)
    job_stuck_threshold_seconds: int = Field(default=3600, ge=60)

    # --- Feature flags (§122, §170) ---------------------------------------
    use_mock_providers: bool = True
    enable_real_video_provider: bool = False
    enable_real_vision_provider: bool = False
    enable_real_llm_provider: bool = False
    enable_qc: bool = False
    enable_credits: bool = False
    enable_multi_provider: bool = False
    enable_batch: bool = False

    # --- Mock failure injection (§172) ------------------------------------
    mock_video_mode: MockVideoMode = "success"
    mock_vision_mode: MockVisionMode = "success"
    mock_llm_mode: MockLLMMode = "success"

    # -- computed ----------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_image_bytes(self) -> int:
        return self.max_upload_image_mb * 1024 * 1024

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_video_bytes(self) -> int:
        return self.max_upload_video_mb * 1024 * 1024

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_image_pixels(self) -> int:
        return self.max_upload_image_megapixels * 1_000_000

    # -- validation --------------------------------------------------------

    @model_validator(mode="after")
    def _validate_production_requirements(self) -> Self:
        """Fail fast on configurations that are only safe outside production.

        Every one of these is a real incident if it reaches production, and all
        are invisible until exploited — so they are checked at boot, where the
        deploy fails loudly, rather than trusted to review.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if not self.jwt_secret.get_secret_value():
            problems.append("JWT_SECRET must be set")
        elif len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("JWT_SECRET must be at least 32 characters")

        if self.debug:
            problems.append("DEBUG must be false in production")

        if "*" in self.cors_allow_origins:
            problems.append("CORS_ALLOW_ORIGINS must not be '*' in production")

        if any(origin.startswith("http://") for origin in self.cors_allow_origins):
            problems.append("CORS_ALLOW_ORIGINS must use https in production")

        if self.use_mock_providers:
            problems.append("USE_MOCK_PROVIDERS must be false in production")

        if not self.s3_bucket:
            problems.append("S3_BUCKET must be set")

        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    @model_validator(mode="after")
    def _validate_provider_selection(self) -> Self:
        """A real provider must not be selected without a key to call it with."""
        if self.use_mock_providers:
            return self

        if self.enable_real_video_provider and self.default_video_provider == "mock":
            raise ValueError(
                "ENABLE_REAL_VIDEO_PROVIDER is true but DEFAULT_VIDEO_PROVIDER is 'mock'"
            )

        # Checked at boot rather than at the first analysis: a missing key
        # discovered mid-request costs a user their upload flow, whereas one
        # discovered here costs a deploy that was going to fail anyway.
        if self.enable_real_llm_provider:
            if self.default_llm_provider == "mock":
                raise ValueError(
                    "ENABLE_REAL_LLM_PROVIDER is true but DEFAULT_LLM_PROVIDER is 'mock'"
                )
            if (
                self.default_llm_provider == "anthropic"
                and not self.anthropic_api_key.get_secret_value()
            ):
                raise ValueError(
                    "DEFAULT_LLM_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is not set"
                )

        if self.enable_real_vision_provider:
            if self.default_vision_provider == "mock":
                raise ValueError(
                    "ENABLE_REAL_VISION_PROVIDER is true but DEFAULT_VISION_PROVIDER is 'mock'"
                )
            if (
                self.default_vision_provider == "anthropic"
                and not self.anthropic_api_key.get_secret_value()
            ):
                raise ValueError(
                    "DEFAULT_VISION_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is not set"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once.

    Cached because validation is not free and settings never change within a
    process. Tests needing different values call ``get_settings.cache_clear()``.
    """
    return Settings()
