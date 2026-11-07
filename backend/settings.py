import os
from dataclasses import dataclass
from urllib.parse import quote

BOT_IDS = ("researcher", "writer", "reviewer")


@dataclass(frozen=True)
class Settings:
    database_url: str
    owner_token: str
    internal_token: str
    supervisor_token: str
    storage_endpoint: str
    storage_password: str
    model_name: str
    model_base_url: str
    provider_configured: bool
    max_depth: int
    max_handoffs: int
    max_recoveries: int

    @classmethod
    def from_env(cls):
        password = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
        return cls(
            database_url=os.environ.get("DATABASE_URL", f"postgresql://hermbot:{password}@postgres/hermbot"),
            owner_token=os.environ.get("OWNER_TOKEN", ""),
            internal_token=os.environ.get("INTERNAL_TOKEN", ""),
            supervisor_token=os.environ.get("SUPERVISOR_TOKEN", ""),
            storage_endpoint=os.environ.get("STORAGE_ENDPOINT", "http://storage:9000"),
            storage_password=os.environ.get("STORAGE_PASSWORD", ""),
            model_name=os.environ.get("MODEL_NAME", ""),
            model_base_url=os.environ.get("MODEL_BASE_URL", ""),
            provider_configured=all(os.environ.get(key) for key in ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME")),
            max_depth=int(os.environ.get("MAX_DELEGATION_DEPTH", "3")),
            max_handoffs=int(os.environ.get("MAX_HANDOFFS", "10")),
            max_recoveries=int(os.environ.get("MAX_RECOVERIES", "2")),
        )

    def bot_token(self, bot_id):
        return os.environ.get(f"{bot_id.upper()}_TOKEN", "")

    def runtime_key(self, bot_id):
        return os.environ.get(f"{bot_id.upper()}_API_KEY", "")


settings = Settings.from_env()
