from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # JARVIS_ prefix: Claude Code exports CLAUDE_* variables (CLAUDE_EFFORT) that would otherwise leak in.
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_prefix="JARVIS_", extra="ignore")

    port: int = 8000
    # None lets the SDK find a key or `ant auth login` profile itself
    anthropic_api_key: SecretStr | None = Field(None, validation_alias="ANTHROPIC_API_KEY")
    typesafe_api_key: SecretStr | None = Field(None, validation_alias="TYPESAFE_API_KEY")
    # The phone's speech in and out; it never sees this key, only single-use tokens minted from it.
    elevenlabs_api_key: SecretStr | None = Field(None, validation_alias="ELEVENLABS_API_KEY")
    voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs default voice "George"; default voices are the fastest
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    data_dir: Path = BACKEND_DIR / "data"
