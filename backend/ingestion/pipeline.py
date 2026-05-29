import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.storage.docstore import SimpleDocumentStore

from backend.ingestion.parser import parse_document
from backend.ingestion.chunker import chunk_documents
from backend.ingestion.embedder import get_embed_model
from backend.retrieval.vector_store import get_vector_store

logger = logging.getLogger(__name__)

# Thread pool for CPU/IO-bound ingestion work.
# CLAUDE.md: "Document ingestion is too slow for synchronous HTTP requests."
# Running inside async def without executor blocks the event loop for 15–45 s per PDF.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ingest")

# Where the BM25 docstore is persisted so retriever.py can load it.
DOCSTORE_DIR = "./docstore"


async def ingest_file(file_path: str, document_id: str) -> int:
    """
    Full ingestion pipeline: parse → chunk → embed → store in Chroma + docstore.
    Returns the number of chunks stored.

    Offloads all blocking work to a thread pool so the FastAPI event loop
    remains responsive during ingestion (CLAUDE.md § Task queue).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        _ingest_sync,
        str(file_path),
        document_id,
    )


def _ingest_sync(file_path: str, document_id: str) -> int:
    """
    Synchronous ingestion body — safe to block; runs in a thread pool.
    Steps: parse → chunk → embed → store vectors + merge into persisted docstore.
    """
    file_path = Path(file_path)
    logger.info(f"[{document_id}] Ingesting (thread): {file_path.name}")

    # 1 ── Parse ──────────────────────────────────────────────────────────────
    documents = parse_document(file_path, document_id)
    if not documents:
        raise ValueError(f"No content extracted from {file_path.name}")

    # 2 ── Chunk ───────────────────────────────────────────────────────────────
    nodes = chunk_documents(documents)
    if not nodes:
        raise ValueError(f"No chunks produced from {file_path.name}")

    # 3 ── Embedding model (Bug B fix) ─────────────────────────────────────────
    # Do NOT mutate li_global.Settings — it is a module-level singleton shared
    # with the async query path. Concurrent ingestion + query would race on it.
    # Pass embed_model explicitly to every LlamaIndex constructor instead.
    embed_model = get_embed_model()

    # 4 ── Build storage context (Bug A fix) ────────────────────────────────────
    # Load the existing persisted docstore and MERGE new nodes into it.
    # Creating a fresh docstore each time overwrites all previously ingested
    # documents' nodes, making BM25 miss every document except the last one.
    vector_store = get_vector_store()
    docstore_path = Path(DOCSTORE_DIR)
    if docstore_path.exists():
        docstore = SimpleDocumentStore.from_persist_dir(DOCSTORE_DIR)
        logger.debug(
            "[%s] Loaded existing docstore: %d nodes", document_id, len(docstore.docs)
        )
    else:
        docstore = SimpleDocumentStore()
        logger.debug("[%s] Creating new docstore", document_id)

    docstore.add_documents(nodes)   # merge — does not remove existing nodes

    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
        docstore=docstore,
    )

    # 5 ── Embed + store (embed_model passed explicitly, not via global) ────────
    VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        embed_model=embed_model,   # explicit — no global mutation (Bug B fix)
        show_progress=True,
    )

    # 6 ── Persist merged docstore ─────────────────────────────────────────────
    storage_context.docstore.persist(persist_path=DOCSTORE_DIR)
    logger.info(
        "[%s] Done — %d chunks stored, docstore now has %d total nodes",
        document_id, len(nodes), len(docstore.docs),
    )
    return len(nodes)

