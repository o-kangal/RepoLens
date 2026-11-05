"""Application settings, read only from the environment."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPOLENS_", env_ignore_empty=True)

    debug: bool = False
    database_url: SecretStr | None = None
