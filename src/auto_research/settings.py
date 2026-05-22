from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    runpod_api_key: SecretStr = Field(default=SecretStr(""))
    runpod_ssh_private_key_path: Path = Path("~/.ssh/runpod_ed25519").expanduser()
    runpod_network_volume_id: str | None = None
    runpod_pod_datacenter: str = "US-KS-2"

    semantic_scholar_api_key: SecretStr | None = None
    contact_email: str = "research@example.com"
    hf_token: SecretStr | None = None

    max_cost_usd: float = 50.0
    max_concurrent_pods: int = 4

    workspace_root: Path = Path("./workspaces")
    db_path: Path = Path("./workspaces/state.db")

    log_level: str = "INFO"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    def workspace_for(self, run_id: str) -> Path:
        path = self.workspace_root / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
