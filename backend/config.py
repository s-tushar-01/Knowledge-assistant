from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── LLM ──────────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-5"

    # ── Embeddings ────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_provider: Literal["openai", "ollama"] = "openai"
    ollama_base_url: str = "http://localhost:11434"

    # ── Vector DB ─────────────────────────────────────────────────────────────
    chroma_persist_path: str = "./chroma_db"
    chroma_collection_name: str = "knowledge_base"

    # ── Qdrant (production) ───────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    # ── Metadata DB ───────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./knowledge.db"

    # ── Task queue ────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Retrieval ─────────────────────────────────────────────────────────────
    similarity_top_k: int = 8
    rerank_top_k: int = 4
    chunk_size: int = 512
    chunk_overlap: int = 64

    # ── LlamaParse (optional) ─────────────────────────────────────────────────
    llama_cloud_api_key: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
