import math
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import crud


@dataclass
class SearchNode:
    text: str
    metadata: dict

    def get_content(self) -> str:
        return self.text


@dataclass
class SearchNodeWithScore:
    node: SearchNode
    score: float


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "this",
    "to", "what", "when", "where", "which", "who", "why", "with", "you",
    "your",
}


def _terms(text: str) -> list[str]:
    return [
        term
        for term in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(term) > 1 and term not in _STOPWORDS
    ]


async def search_chunks(db: AsyncSession, query: str, limit: int = 4) -> list[SearchNodeWithScore]:
    chunks = await crud.list_ready_chunks(db)
    query_terms = _terms(query)
    if not chunks or not query_terms:
        return []

    doc_freq: dict[str, int] = {}
    prepared: list[tuple[object, list[str]]] = []
    for chunk in chunks:
        terms = _terms(chunk.text)
        prepared.append((chunk, terms))
        for term in set(terms):
            doc_freq[term] = doc_freq.get(term, 0) + 1

    total_chunks = len(chunks)
    scored: list[SearchNodeWithScore] = []
    for chunk, terms in prepared:
        score = _score(query_terms, terms, doc_freq, total_chunks)
        if score <= 0:
            continue
        scored.append(SearchNodeWithScore(
            node=SearchNode(
                text=chunk.text,
                metadata={
                    "source_file": chunk.source_file,
                    "page_number": chunk.page_number,
                    "section_heading": chunk.section_heading,
                },
            ),
            score=score,
        ))

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]


def _score(
    query_terms: Iterable[str],
    chunk_terms: list[str],
    doc_freq: dict[str, int],
    total_chunks: int,
) -> float:
    counts: dict[str, int] = {}
    for term in chunk_terms:
        counts[term] = counts.get(term, 0) + 1

    score = 0.0
    for term in query_terms:
        freq = counts.get(term, 0)
        if freq == 0:
            continue
        idf = math.log((total_chunks + 1) / (doc_freq.get(term, 0) + 1)) + 1
        score += (1 + math.log(freq)) * idf
    return round(score, 4)
