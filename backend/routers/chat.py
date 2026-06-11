import json
import logging
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.llm.prompts import build_system_prompt
from backend.retrieval.free_search import SearchNodeWithScore, search_chunks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

Provider = Literal["anthropic", "openai"]


class ChatRequest(BaseModel):
    query: str


@router.post("", summary="Stream a cited answer or free search fallback via SSE")
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    x_ai_provider: str | None = Header(default=None),
    x_ai_key: str | None = Header(default=None),
):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    async def generate():
        source_nodes = await search_chunks(db, request.query)
        if not source_nodes:
            payload = {
                "type": "token",
                "content": "I don't have matching document passages for this question. Upload a document first or ask using terms from your files.",
            }
            yield f"data: {json.dumps(payload)}\n\n"
            yield "data: [DONE]\n\n"
            return

        yield f"data: {json.dumps({'type': 'citations', 'citations': _citations(source_nodes)})}\n\n"

        provider = _normalise_provider(x_ai_provider)
        key = (x_ai_key or "").strip()
        if provider and key:
            try:
                async for token in _stream_ai_answer(provider, key, request.query, source_nodes):
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
            except Exception as exc:
                logger.warning("AI answer failed: %s", exc)
                yield f"data: {json.dumps({'type': 'error', 'message': _friendly_error(exc)})}\n\n"
                yield f"data: {json.dumps({'type': 'token', 'content': _free_answer(source_nodes)})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'token', 'content': _free_answer(source_nodes)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/sync", summary="Non-streaming free search answer")
async def chat_sync(request: ChatRequest, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    source_nodes = await search_chunks(db, request.query)
    return {
        "answer": _free_answer(source_nodes) if source_nodes else "I don't have relevant documents to answer this question.",
        "citations": _citations(source_nodes),
    }


def _normalise_provider(provider: str | None) -> Provider | None:
    value = (provider or "").strip().lower()
    if value in {"anthropic", "openai"}:
        return value  # type: ignore[return-value]
    return None


def _citations(source_nodes: list[SearchNodeWithScore]) -> list[dict]:
    return [
        {
            "source_file": n.node.metadata.get("source_file", "unknown"),
            "page_number": n.node.metadata.get("page_number"),
            "section_heading": n.node.metadata.get("section_heading", ""),
            "score": round(n.score or 0, 4),
            "text_preview": n.node.get_content()[:300],
        }
        for n in source_nodes
    ]


def _free_answer(source_nodes: list[SearchNodeWithScore]) -> str:
    lines = [
        "Free mode: I found these matching passages in your documents. Add an AI key in AI Settings for a generated answer.",
        "",
    ]
    for index, item in enumerate(source_nodes, 1):
        meta = item.node.metadata
        source = meta.get("source_file", "unknown")
        page = meta.get("page_number")
        page_text = f", page {page}" if page else ""
        preview = item.node.get_content().replace("\n", " ").strip()[:500]
        lines.append(f"{index}. {source}{page_text}: {preview}")
    return "\n".join(lines)


async def _stream_ai_answer(
    provider: Provider,
    api_key: str,
    query: str,
    source_nodes: list[SearchNodeWithScore],
) -> AsyncGenerator[str, None]:
    system = build_system_prompt(source_nodes)
    if provider == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        async with client.messages.stream(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": query}],
        ) as stream:
            async for text in stream.text_stream:
                yield text
        return

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    stream = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
        stream=True,
    )
    async for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            yield token


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "insufficient_quota" in lower or "quota" in lower or "429" in lower:
        return "AI key quota is exhausted. Free document search is still available below."
    if "api_key" in lower or "authentication" in lower or "401" in lower:
        return "AI key is missing or invalid. Check AI Settings, or clear the key to use free mode."
    if "connection" in lower or "refused" in lower:
        return "Cannot reach the AI provider right now. Free document search is still available below."
    return msg[:200] if len(msg) > 200 else msg
