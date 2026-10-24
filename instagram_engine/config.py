from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore"
        )

        # Database
        database_url: str = Field(
            default="postgresql+psycopg2://postgres:postgres@localhost:5432/customer_yab",
            alias="DATABASE_URL"
        )

        # Multi-tenancy
        default_tenant_id: str = Field(default="tenant_default", alias="DEFAULT_TENANT_ID")

        # LLM Settings
        llm_api_base: str = Field(
            default="https://generativelanguage.googleapis.com/v1beta/openai",
            alias="LLM_API_BASE"
        )
        llm_api_key: str = Field(default="", alias="LLM_API_KEY")
        llm_model: str = Field(default="gemini-3.5-flash-lite", alias="LLM_MODEL")
        llm_temperature: float = Field(default=0.3, alias="LLM_TEMPERATURE")

        # Circadian Hours
        timezone: str = Field(default="Asia/Tehran", alias="TIMEZONE")
        outreach_start_hour: int = Field(default=9, alias="OUTREACH_START_HOUR")
        outreach_end_hour: int = Field(default=23, alias="OUTREACH_END_HOUR")

        # Scraper Quotas
        scraper_post_sleep_min: float = Field(default=10.0, alias="SCRAPER_POST_SLEEP_MIN")
        scraper_post_sleep_max: float = Field(default=25.0, alias="SCRAPER_POST_SLEEP_MAX")
        scraper_hashtag_sleep_min: float = Field(default=45.0, alias="SCRAPER_HASHTAG_SLEEP_MIN")
        scraper_hashtag_sleep_max: float = Field(default=120.0, alias="SCRAPER_HASHTAG_SLEEP_MAX")
        max_scraper_hashtags_per_hour: int = Field(default=4, alias="MAX_SCRAPER_HASHTAGS_PER_HOUR")
        max_scraper_hashtags_per_day: int = Field(default=18, alias="MAX_SCRAPER_HASHTAGS_PER_DAY")
        max_scraper_post_comments_per_day: int = Field(default=80, alias="MAX_SCRAPER_POST_COMMENTS_PER_DAY")

        # Sender Quotas
        sender_dm_sleep_min: float = Field(default=720.0, alias="SENDER_DM_SLEEP_MIN")
        sender_dm_sleep_max: float = Field(default=1500.0, alias="SENDER_DM_SLEEP_MAX")
        sender_warming_max_dms_per_day: int = Field(default=8, alias="SENDER_WARMING_MAX_DMS_PER_DAY")
        sender_established_max_dms_per_day: int = Field(default=20, alias="SENDER_ESTABLISHED_MAX_DMS_PER_DAY")
        like_before_dm: bool = Field(default=True, alias="LIKE_BEFORE_DM")

        # Storage
        session_storage_dir: Path = Field(default=Path("./sessions"), alias="SESSION_STORAGE_DIR")

    settings = Settings()

except ImportError:
    import os

    class SettingsFallback:
        def __init__(self):
            self.database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/customer_yab")
            self.default_tenant_id: str = os.getenv("DEFAULT_TENANT_ID", "tenant_default")
            self.llm_api_base: str = os.getenv("LLM_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai")
            self.llm_api_key: str = os.getenv("LLM_API_KEY", "")
            self.llm_model: str = os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")
            self.llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
            self.timezone: str = os.getenv("TIMEZONE", "Asia/Tehran")
            self.outreach_start_hour: int = int(os.getenv("OUTREACH_START_HOUR", "9"))
            self.outreach_end_hour: int = int(os.getenv("OUTREACH_END_HOUR", "23"))
            self.scraper_post_sleep_min: float = float(os.getenv("SCRAPER_POST_SLEEP_MIN", "10.0"))
            self.scraper_post_sleep_max: float = float(os.getenv("SCRAPER_POST_SLEEP_MAX", "25.0"))
            self.scraper_hashtag_sleep_min: float = float(os.getenv("SCRAPER_HASHTAG_SLEEP_MIN", "45.0"))
            self.scraper_hashtag_sleep_max: float = float(os.getenv("SCRAPER_HASHTAG_SLEEP_MAX", "120.0"))
            self.max_scraper_hashtags_per_hour: int = int(os.getenv("MAX_SCRAPER_HASHTAGS_PER_HOUR", "4"))
            self.max_scraper_hashtags_per_day: int = int(os.getenv("MAX_SCRAPER_HASHTAGS_PER_DAY", "18"))
            self.max_scraper_post_comments_per_day: int = int(os.getenv("MAX_SCRAPER_POST_COMMENTS_PER_DAY", "80"))
            self.sender_dm_sleep_min: float = float(os.getenv("SENDER_DM_SLEEP_MIN", "720.0"))
            self.sender_dm_sleep_max: float = float(os.getenv("SENDER_DM_SLEEP_MAX", "1500.0"))
            self.sender_warming_max_dms_per_day: int = int(os.getenv("SENDER_WARMING_MAX_DMS_PER_DAY", "8"))
            self.sender_established_max_dms_per_day: int = int(os.getenv("SENDER_ESTABLISHED_MAX_DMS_PER_DAY", "20"))
            self.like_before_dm: bool = os.getenv("LIKE_BEFORE_DM", "true").lower() in ("true", "1", "yes")
            self.session_storage_dir: Path = Path(os.getenv("SESSION_STORAGE_DIR", "./sessions"))

    settings = SettingsFallback()

