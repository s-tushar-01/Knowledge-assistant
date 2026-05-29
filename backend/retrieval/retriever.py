import logging
from pathlib import Path

import llama_index.core.settings as li_global  # kept for VectorStoreIndex internals; do not mutate
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.retrievers import VectorIndexRetriever, QueryFusionRetriever
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.retrievers.bm25 import BM25Retriever

from backend.config import get_settings
from backend.ingestion.embedder import get_embed_model
from backend.retrieval.vector_store import get_vector_store

logger = logging.getLogger(__name__)

# Must match DOCSTORE_DIR in pipeline.py
DOCSTORE_DIR = "./docstore"


def get_index() -> VectorStoreIndex:
    """
    Load the VectorStoreIndex from the persisted Chroma collection.
    Also loads the SimpleDocumentStore written by the ingestion pipeline
    so that BM25Retriever has actual node text to work with.

    embed_model is passed explicitly — never via li_global.Settings —
    to avoid a data race with the ingestion thread pool (Bug B fix).

    CLAUDE.md: "Always combine vector search with metadata filters to keep
    results in scope" — hybrid BM25 + vector is the required retrieval mode.
    """
    embed_model = get_embed_model()
    # Do NOT set li_global.Settings.embed_model here — that global is shared
    # with the ingestion ThreadPoolExecutor and is not thread-safe to mutate.

    vector_store = get_vector_store()

    # Load docstore if it exists (written by pipeline._ingest_sync)
    docstore_path = Path(DOCSTORE_DIR)
    if docstore_path.exists():
        docstore = SimpleDocumentStore.from_persist_dir(DOCSTORE_DIR)
        logger.debug(f"Loaded docstore: {len(docstore.docs)} nodes")
    else:
        docstore = SimpleDocumentStore()
        logger.warning(
            "Docstore not found at %s — BM25 will be unavailable until first ingestion",
            DOCSTORE_DIR,
        )

    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
        docstore=docstore,
    )

    return VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        storage_context=storage_context,
        embed_model=embed_model,
    )


def get_retriever(index: VectorStoreIndex | None = None):
    """
    Hybrid BM25 + vector retriever using Reciprocal Rank Fusion (Phase 7).

    Each sub-retriever fetches similarity_top_k=8 candidates.
    QueryFusionRetriever fuses and returns rerank_top_k=4 final nodes.

    Falls back to vector-only gracefully if no docstore is available
    (e.g. on first startup before any document has been ingested).
    """
    settings = get_settings()
    if index is None:
        index = get_index()

    vector_retriever = VectorIndexRetriever(
        index=index,
        similarity_top_k=settings.similarity_top_k,
    )

    # BM25 works only when docstore has nodes (populated during ingestion)
    if len(index.docstore.docs) == 0:
        logger.warning("Docstore is empty — using vector-only retrieval")
        return vector_retriever

    try:
        bm25_retriever = BM25Retriever.from_defaults(
            docstore=index.docstore,
            similarity_top_k=settings.similarity_top_k,
        )
        retriever = QueryFusionRetriever(
            retrievers=[vector_retriever, bm25_retriever],
            similarity_top_k=settings.rerank_top_k,
            num_queries=1,           # disable sub-query generation; just fuse
            mode="reciprocal_rerank",
            use_async=True,
        )
        logger.debug(
            "Hybrid retriever active — vector top_k=%d, BM25 top_k=%d, fused top_k=%d",
            settings.similarity_top_k,
            settings.similarity_top_k,
            settings.rerank_top_k,
        )
        return retriever

    except Exception as exc:
        logger.warning("BM25 initialisation failed (%s) — falling back to vector-only", exc)
        return vector_retriever
