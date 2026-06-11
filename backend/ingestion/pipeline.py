import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.db import crud
from backend.db.database import AsyncSessionLocal
from backend.ingestion.chunker import chunk_documents
from backend.ingestion.parser import parse_document

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")


async def ingest_file(file_path: str, document_id: str) -> int:
    """
    Parse and chunk documents into SQLite-searchable text.
    This path is free by default and does not call embedding APIs.
    """
    loop = asyncio.get_running_loop()
    chunks = await loop.run_in_executor(
        _executor,
        _parse_and_chunk_sync,
        str(file_path),
        document_id,
    )

    async with AsyncSessionLocal() as db:
        return await crud.replace_document_chunks(db, document_id, chunks)


def _parse_and_chunk_sync(file_path: str, document_id: str) -> list[dict]:
    file_path_obj = Path(file_path)
    logger.info("[%s] Ingesting searchable chunks: %s", document_id, file_path_obj.name)

    documents = parse_document(file_path_obj, document_id)
    if not documents:
        raise ValueError(f"No content extracted from {file_path_obj.name}")

    nodes = chunk_documents(documents)
    if not nodes:
        raise ValueError(f"No chunks produced from {file_path_obj.name}")

    chunks: list[dict] = []
    for index, node in enumerate(nodes):
        metadata = node.metadata or {}
        chunks.append({
            "source_file": metadata.get("source_file", file_path_obj.name),
            "page_number": metadata.get("page_number"),
            "section_heading": metadata.get("section_heading", ""),
            "chunk_index": index,
            "text": node.get_content(),
        })

    logger.info("[%s] Prepared %d searchable chunks", document_id, len(chunks))
    return chunks
