"""Configuration: secrets from .env, behaviour from config.yaml, my facts from profile.yaml."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_URL = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'freelancehunter.db'}"


# ── Secrets (.env only) ─────────────────────────────────────────────────────
class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    # The tender spec uses GEMINI_API_KEY / TELEGRAM_CHAT_ID / GEMINI_MODEL — both spellings work.
    google_api_key: SecretStr = Field(validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"))
    telegram_bot_token: SecretStr
    telegram_owner_chat_id: int = Field(
        validation_alias=AliasChoices("TELEGRAM_OWNER_CHAT_ID", "TELEGRAM_CHAT_ID")
    )
    gemini_model: str | None = None  # if set, overrides both model names in config.yaml

    database_url: str = DEFAULT_DB_URL
    log_level: str = "INFO"
    config_path: Path = BASE_DIR / "config.yaml"
    profile_path: Path = BASE_DIR / "profile.yaml"
    company_path: Path = BASE_DIR / "company.yaml"
    data_dir: Path = BASE_DIR / "data"

    freelancer_com_token: SecretStr | None = None
    upwork_client_id: SecretStr | None = None
    upwork_client_secret: SecretStr | None = None
    upwork_access_token: SecretStr | None = None

    imap_host: str = "imap.gmail.com"
    imap_user: str | None = None
    imap_app_password: SecretStr | None = None

    google_sheets_credentials_file: Path | None = None

    # 🌐 /tender/lots web sahifasi: o'rnatilsa, sahifa faqat ?key=<WEB_TOKEN> bilan ochiladi
    web_token: SecretStr | None = None


# ── config.yaml ─────────────────────────────────────────────────────────────
class ModelPrice(BaseModel):
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0


class GeminiConfig(BaseModel):
    fast_model: str
    strong_model: str
    temperature: float = Field(0.4, ge=0, le=2)
    max_retries: int = Field(4, ge=1, le=10)
    timeout_s: float = 120
    prices: dict[str, ModelPrice] = {}

    @field_validator("fast_model", "strong_model")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Gemini model name must be set in config.yaml")
        return v.strip()


class PlatformConfig(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    poll_interval_min: int = Field(10, ge=1)
    daily_action_cap: int = Field(30, ge=0)
    min_action_delay_s: int = Field(20, ge=1)
    max_action_delay_s: int = Field(40, ge=1)
    monthly_proposal_quota: int | None = None
    max_project_budget_usd: float | None = None

    @model_validator(mode="after")
    def _delays(self) -> PlatformConfig:
        if self.min_action_delay_s > self.max_action_delay_s:
            raise ValueError("min_action_delay_s must be <= max_action_delay_s")
        return self


class EmailWatchConfig(BaseModel):
    enabled: bool = True  # only active when IMAP_USER and IMAP_APP_PASSWORD are set in .env
    senders: list[str] = ["upwork.com", "fiverr.com"]
    poll_interval_min: int = Field(5, ge=1)
    lookback_days: int = 3
    max_emails_per_poll: int = 20


class FiltersConfig(BaseModel):
    budget_min_usd: float = 0
    budget_max_usd: float = 10_000
    max_job_age_hours: int = 72
    max_proposals: int = 25
    languages: list[str] = ["en", "ru", "uz"]


class WorkerConfig(BaseModel):
    max_fix_iterations: int = 5


_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class TenderKeywords(BaseModel):
    strong: list[str] = []
    weak: list[str] = []


class TenderExclude(BaseModel):
    terms: list[str] = []
    override: list[str] = []


class TenderSourceConfig(BaseModel):
    enabled: bool = True


class TenderAnalysisConfig(BaseModel):
    enabled: bool = True
    max_doc_tokens: int = Field(30_000, ge=1_000)  # TZ text budget per lot (~4 chars = 1 token)
    max_requests_per_day: int = Field(100, ge=1)
    max_scan_pdf_mb: float = 50  # scanned PDFs go to Gemini as a file (Files API)
    max_scan_pdf_pages: int = 150
    backfill_per_run: int = Field(10, ge=0)  # already-announced lots analysed quietly per scan


class TendersConfig(BaseModel):
    enabled: bool = False
    poll_interval_min: int = Field(30, ge=5)
    request_delay_s: float = Field(2.5, ge=2.0)  # spec: >= 2 s between requests
    page_size: int = Field(200, ge=10, le=500)
    scan_descriptions: bool = True
    max_detail_fetches_per_run: int = Field(40, ge=0)
    download_files: bool = True
    max_file_mb: float = 25
    daily_report_time: str = "09:00"
    questions_deadline_days_before: int = 5
    deposit_reminder_workdays: int = 3
    deadline_reminders_hours: list[int] = [24, 3]
    sources: dict[str, TenderSourceConfig] = {}
    keywords: TenderKeywords = TenderKeywords()
    exclude: TenderExclude = TenderExclude()
    analysis: TenderAnalysisConfig = TenderAnalysisConfig()

    @field_validator("daily_report_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.fullmatch(v):
            raise ValueError("daily_report_time must be HH:MM")
        return v


class WebConfig(BaseModel):
    """🌐 Read-only /tender/lots page served from the same process (phone = server)."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = Field(8080, ge=1, le=65535)


class AppConfig(BaseModel):
    timezone: str = "Asia/Tashkent"
    keywords: list[str] = []
    filters: FiltersConfig = FiltersConfig()
    min_fit_score: int = Field(70, ge=0, le=100)
    max_proposals_per_day: int = 10
    max_scores_per_cycle: int = 15     # Gemini scorings per freelance cycle
    max_llm_calls_per_day: int = 150   # freelance Gemini calls (scoring + proposals) per day
    quota_warn_threshold: int = 2
    tracker_interval_min: int = 5
    daily_summary_time: str = "21:00"
    platforms: dict[str, PlatformConfig] = {}
    gemini: GeminiConfig
    worker: WorkerConfig = WorkerConfig()
    email_watch: EmailWatchConfig = EmailWatchConfig()
    tenders: TendersConfig = TendersConfig()
    web: WebConfig = WebConfig()
    google_sheets_id: str | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"Unknown timezone: {v}") from e
        return v

    @field_validator("daily_summary_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("daily_summary_time must be HH:MM")
        return v


# ── profile.yaml ────────────────────────────────────────────────────────────
class Profile(BaseModel):
    name: str
    location: str = ""
    languages: list[str] = []
    title: str = ""
    bio: str = ""
    experience_since: int | None = None
    products: list[str] = []
    skills: list[str] = []
    portfolio_links: list[str] = []
    min_rate_usd_per_hour: float = 0


# ── company.yaml (tenders) ──────────────────────────────────────────────────
class Company(BaseModel):
    model_config = {"extra": "allow"}  # everything is passed to Gemini as context

    nomi: str = ""
    zakalat_limiti_usd: float = 0
    tillar: list[str] = []


# ── Aggregate ───────────────────────────────────────────────────────────────
class Settings(BaseModel):
    secrets: Secrets
    config: AppConfig
    profile: Profile
    company: Company = Company()

    @property
    def owner_chat_id(self) -> int:
        return self.secrets.telegram_owner_chat_id


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings(secrets: Secrets | None = None) -> Settings:
    secrets = secrets or Secrets()  # type: ignore[call-arg]
    config = AppConfig.model_validate(_load_yaml(secrets.config_path))
    if secrets.gemini_model:
        config.gemini.fast_model = config.gemini.strong_model = secrets.gemini_model
    company = (
        Company.model_validate(_load_yaml(secrets.company_path))
        if secrets.company_path.exists()
        else Company()
    )
    return Settings(
        secrets=secrets,
        config=config,
        profile=Profile.model_validate(_load_yaml(secrets.profile_path)),
        company=company,
    )


@lru_cache
def get_settings() -> Settings:
    return load_settings()
