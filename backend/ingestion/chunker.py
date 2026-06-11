import logging
from typing import List

from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode

from backend.config import get_settings

logger = logging.getLogger(__name__)


def chunk_documents(documents: List[Document]) -> List[BaseNode]:
    """
    Split a list of LlamaIndex Documents into fixed-size overlapping chunks.
    Very short documents are stored as a single chunk to avoid over-splitting.
    """
    settings = get_settings()
    total_chars = sum(len(d.text) for d in documents)

    if total_chars < settings.chunk_size * 4:
        # Short doc — keep as single chunk, no overlap needed
        splitter = SentenceSplitter(chunk_size=settings.chunk_size * 4, chunk_overlap=0)
    else:
        splitter = SentenceSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

    nodes = splitter.get_nodes_from_documents(documents)
    logger.info(f"Chunked {len(documents)} sections into {len(nodes)} nodes")
    return nodes
