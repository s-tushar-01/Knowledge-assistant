from llama_index.core import VectorStoreIndex
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.response_synthesizers import get_response_synthesizer

from backend.retrieval.retriever import get_retriever


def get_query_engine(index: VectorStoreIndex | None = None) -> RetrieverQueryEngine:
    """
    LlamaIndex query engine using tree_summarize for concise cited answers.
    Used by scripts; the API layer calls the retriever + Claude directly for streaming.
    """
    retriever = get_retriever(index)
    synthesizer = get_response_synthesizer(response_mode="tree_summarize")
    return RetrieverQueryEngine(
        retriever=retriever,
        response_synthesizer=synthesizer,
    )
