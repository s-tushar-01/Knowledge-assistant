import logging
from typing import AsyncGenerator, List, Optional

import anthropic

from backend.config import get_settings
from backend.llm.prompts import build_system_prompt

logger = logging.getLogger(__name__)


def _build_messages(history: List, query: str) -> List[dict]:
    """
    Build the Anthropic messages array for a multi-turn conversation.

    CLAUDE.md § LLM: messages is an array to support multi-turn.
    The conversation_history from ChatRequest is forwarded here so Claude
    has context from previous turns. Without this, every query reaches
    Claude as if it's the first message — follow-up questions like
    "expand on point 3" would always fail.

    history: list of ChatMessage (role, content) from ChatRequest
    query:   the current user query string
    """
    msgs = [{"role": m.role, "content": m.content} for m in (history or [])]
    msgs.append({"role": "user", "content": query})
    return msgs


async def stream_answer(
    query: str,
    source_nodes: List,
    history: Optional[List] = None,
) -> AsyncGenerator[str, None]:
    """
    Streams a grounded answer token-by-token from Claude.
    source_nodes: List[NodeWithScore] from the LlamaIndex retriever.
    history:      conversation_history from ChatRequest (may be empty list).
    """
    settings = get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    system = build_system_prompt(source_nodes)
    messages = _build_messages(history, query)

    logger.info(
        f"Streaming answer | query={query[:80]!r} | nodes={len(source_nodes)} | history_turns={len(history or [])}"
    )

    async with client.messages.stream(
        model=settings.claude_model,
        max_tokens=2048,
        system=system,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def get_answer(
    query: str,
    source_nodes: List,
    history: Optional[List] = None,
) -> str:
    """Non-streaming variant used by Streamlit and CLI scripts."""
    settings = get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    system = build_system_prompt(source_nodes)
    messages = _build_messages(history, query)

    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        system=system,
        messages=messages,
    )
    return response.content[0].text
