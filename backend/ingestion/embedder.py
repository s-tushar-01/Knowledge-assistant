from functools import lru_cache

from llama_index.core.embeddings import BaseEmbedding

from backend.config import get_settings


@lru_cache(maxsize=1)
def get_embed_model() -> BaseEmbedding:
    """
    Singleton embedding model factory.
    Reads EMBEDDING_PROVIDER from config: 'openai' (default) or 'ollama'.
    """
    settings = get_settings()

    if settings.embedding_provider == "openai":
        from llama_index.embeddings.openai import OpenAIEmbedding
        return OpenAIEmbedding(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
            max_retries=1,
        )

    if settings.embedding_provider == "ollama":
        from llama_index.embeddings.ollama import OllamaEmbedding
        return OllamaEmbedding(
            model_name="nomic-embed-text",
            base_url=settings.ollama_base_url,
        )

    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider!r}")
