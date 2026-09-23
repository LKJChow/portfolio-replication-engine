"""Settings loaded from the environment. Nothing secret is ever hardcoded."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sec_user_agent: str = ""
    openfigi_api_key: str = ""
    database_url: str = "postgresql+psycopg://prx:prx@localhost:5432/prx"

    def require_sec_agent(self) -> str:
        """SEC blocks requests without a contact-bearing User-Agent."""
        if not self.sec_user_agent or "@" not in self.sec_user_agent:
            raise RuntimeError(
                "SEC_USER_AGENT must be set to something like "
                '"Your Name your@email.com" — the SEC rejects anonymous traffic.'
            )
        return self.sec_user_agent


@lru_cache
def settings() -> Settings:
    return Settings()
