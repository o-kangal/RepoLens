"""Application settings, read only from the environment."""

from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPOLENS_", env_ignore_empty=True)

    debug: bool = False
    database_url: SecretStr | None = None
    embedding_model: str = "voyage-code-4"
    voyage_api_key: SecretStr | None = None

    llm_provider: Literal["anthropic", "openai", "azure_openai"] = "anthropic"
    llm_model: str | None = None
    """Model name, or the deployment name on Azure OpenAI. Anthropic has a default."""
    llm_models: Annotated[list[str], NoDecode] = []
    """Further models a query may choose, comma-separated in the environment."""
    llm_refusal_fallback: bool = True
    """Anthropic only: let the API retry a safety refusal on a fallback model."""
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str | None = None

    @field_validator("llm_models", mode="before")
    @classmethod
    def _split_models(cls, value: object) -> object:
        if isinstance(value, str):
            return [model.strip() for model in value.split(",") if model.strip()]
        return value
