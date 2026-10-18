import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

KEY_NAMES_BY_HOST = {
    "integrate.api.nvidia.com": "NVIDIA_API_KEY",
    "openrouter.ai": "OPENROUTER_API_KEY",
    "api.z.ai": "ZAI_API_KEY",
    "api.groq.com": "GROQ_API_KEY",
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    port: int = 8000
    llm_provider: Literal["openai_style", "claude"] = "openai_style"
    llm_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_model: str = "z-ai/glm-5.3-flash"
    llm_api_key: SecretStr | None = None
    llm_images: Literal["auto", "yes", "no"] = "auto"
    allow_paid_models: bool = False
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    data_dir: Path = BACKEND_DIR / "data"

    @property
    def llm_host(self) -> str:
        return (urlparse(self.llm_base_url).hostname or "").lower()

    def api_key(self) -> str | None:
        if self.llm_api_key and self.llm_api_key.get_secret_value().strip():
            return self.llm_api_key.get_secret_value().strip()
        if self.llm_provider == "claude":
            return None  # the Anthropic SDK reads ANTHROPIC_API_KEY itself
        if self.llm_host in LOCAL_HOSTS:
            return "ollama"  # local servers want some key and ignore it
        name = KEY_NAMES_BY_HOST.get(self.llm_host)
        return os.environ.get(name) if name else None


def load_settings(**overrides) -> Settings:
    return Settings(**overrides)
