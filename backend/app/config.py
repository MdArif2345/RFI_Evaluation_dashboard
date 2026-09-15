from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = BACKEND_DIR.parent
DEFAULT_QNA_PATH = DASHBOARD_DIR / "cmc_response_documents.jsonl"
FALLBACK_QNA_PATH = DASHBOARD_DIR / "QnA_pairs_extracted.json"
EXTRA_QNA_PATH = DASHBOARD_DIR / "cmc_response_documents (3).json"
CODE_CATALOG_PATH = DASHBOARD_DIR / "code_catalog.json"
DEFAULT_CHROMA_DIR = BACKEND_DIR / "chroma_data"
DEFAULT_CHAT_DB_PATH = BACKEND_DIR / "chat_history.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Chat / generation provider ---
    # "mga"    -> Bayer myGenAssist (OpenAI-compatible gateway)
    # "openai" -> public OpenAI
    llm_provider: Literal["mga", "openai"] = "mga"

    # myGenAssist
    mga_api_key: str = ""
    mga_base_url: str = ""
    mga_model: str = "gpt-4o"
    # Set only if MGA is Azure-OpenAI style (adds ?api-version=...)
    mga_api_version: str = ""
    # Header carrying the key: "Authorization" (Bearer), "api-key", "Ocp-Apim-Subscription-Key"
    mga_auth_header: str = "Authorization"

    # Public OpenAI (fallback / not usable on restricted enterprise network)
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"

    # --- Embeddings ---
    # "local"  -> on-device ONNX model, nothing leaves the machine (default)
    # "openai" -> OpenAI embeddings API
    # "mga"    -> MGA embeddings endpoint, only if your gateway exposes one
    embed_provider: Literal["local", "openai", "mga"] = "local"
    openai_embed_model: str = "text-embedding-3-small"
    mga_embed_model: str = "text-embedding-3-small"

    # --- Access control ---
    app_password: str = "cmc-share"
    app_username: str = "cmc"

    # --- Data / index ---
    qna_path: Path = DEFAULT_QNA_PATH
    chroma_dir: Path = DEFAULT_CHROMA_DIR
    chroma_collection: str = "cmc_qna"
    chat_db_path: Path = DEFAULT_CHAT_DB_PATH

    retrieve_top_k: int = 5

    def chat_key(self) -> str:
        return self.mga_api_key if self.llm_provider == "mga" else self.openai_api_key

    def chat_model(self) -> str:
        return self.mga_model if self.llm_provider == "mga" else self.openai_chat_model

    def chat_key_ready(self) -> bool:
        key = self.chat_key()
        return bool(key) and not key.startswith("REPLACE_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
