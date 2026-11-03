"""Runtime configuration. All secrets come from the environment, never from code."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAIROS_", env_file=".env", extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://kairos:kairos@localhost:5432/kairos"
    redis_url: str | None = None

    # Session cookie
    session_cookie_name: str = "kairos_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = False  # set True behind HTTPS

    # Allowed browser origins (comma separated) for CORS. Same-origin via the
    # Next.js proxy needs none.
    cors_origins: str = ""

    # Seed credentials. The admin password MUST be overridden in production.
    demo_user_email: str = "demo@platform.test"
    demo_user_password: str = "Demo@123456"
    admin_email: str = "admin@platform.test"
    admin_password: str = "Admin@123456"

    # Demo-only convenience: return password-reset links in the API response
    # because the demo has no outbound email. Disable for shared deployments.
    expose_reset_links: bool = True

    # Run the in-process limit-order matcher / expiry settler.
    background_matcher: bool = True
    matcher_interval_seconds: float = 3.0

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
