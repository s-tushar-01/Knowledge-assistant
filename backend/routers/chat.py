import json
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.llm.claude import stream_answer
from backend.retrieval.retriever import get_index, get_retriever

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str
    conversation_history: Optional[List[ChatMessage]] = []


# ── Streaming endpoint ────────────────────────────────────────────────────────

@router.post("", summary="Stream a grounded answer via SSE")
async def chat(request: ChatRequest):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    async def generate():
        # ── Retrieve ─────────────────────────────────────────────────────────
        try:
            index = get_index()
            retriever = get_retriever(index)
            source_nodes = await retriever.aretrieve(request.query)
        except Exception as exc:
            logger.error(f"Retrieval error: {exc}")
            # Always emit an SSE error event — never a plain HTTP 500 —
            # so the frontend can display a typed, user-facing error message.
            friendly = _friendly_error(exc)
            yield f"data: {json.dumps({'type': 'error', 'message': friendly})}\n\n"
            yield "data: [DONE]\n\n"
            return

        # ── No matching chunks ────────────────────────────────────────────────
        if not source_nodes:
            payload = json.dumps({"type": "token", "content": "I don't have any indexed documents to answer this question. Upload a file first."})
            yield f"data: {payload}\n\n"
            yield "data: [DONE]\n\n"
            return

        # ── Citations event ───────────────────────────────────────────────────
        citations = [
            {
                "source_file": n.node.metadata.get("source_file", "unknown"),
                "page_number": n.node.metadata.get("page_number"),
                "section_heading": n.node.metadata.get("section_heading", ""),
                "score": round(n.score or 0, 4),
                "text_preview": n.node.get_content()[:200],
            }
            for n in source_nodes
        ]
        yield f"data: {json.dumps({'type': 'citations', 'citations': citations})}\n\n"

        # ── Token stream ──────────────────────────────────────────────────────
        try:
            async for token in stream_answer(
                request.query,
                source_nodes,
                history=request.conversation_history,  # forward multi-turn context
            ):
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
        except Exception as exc:
            logger.error(f"Streaming error: {exc}")
            yield f"data: {json.dumps({'type': 'error', 'message': _friendly_error(exc)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _friendly_error(exc: Exception) -> str:
    """Convert backend exceptions to short, user-readable messages."""
    msg = str(exc)
    if "api_key" in msg.lower() or "authentication" in msg.lower() or "401" in msg:
        return "API key not configured. Add ANTHROPIC_API_KEY and OPENAI_API_KEY to .env and restart."
    if "connection" in msg.lower() or "refused" in msg.lower():
        return "Cannot reach the AI provider. Check your internet connection."
    # Trim long tracebacks
    return msg[:200] if len(msg) > 200 else msg



# ── Sync endpoint (for Streamlit / simple clients) ────────────────────────────

@router.post("/sync", summary="Non-streaming chat (returns full answer)")
async def chat_sync(request: ChatRequest):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        index = get_index()
        retriever = get_retriever(index)
        source_nodes = await retriever.aretrieve(request.query)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not source_nodes:
        return {"answer": "I don't have relevant documents to answer this question.", "citations": []}

    from backend.llm.claude import get_answer
    answer = await get_answer(
        request.query,
        source_nodes,
        history=request.conversation_history,  # forward multi-turn context
    )

    citations = [
        {
            "source_file": n.node.metadata.get("source_file", "unknown"),
            "page_number": n.node.metadata.get("page_number"),
            "section_heading": n.node.metadata.get("section_heading", ""),
            "score": round(n.score or 0, 4),
        }
        for n in source_nodes
    ]

    return {"answer": answer, "citations": citations}
