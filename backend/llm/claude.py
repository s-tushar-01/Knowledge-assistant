import logging
from typing import AsyncGenerator, List

import anthropic

from backend.config import get_settings
from backend.llm.prompts import build_system_prompt

logger = logging.getLogger(__name__)


def _build_messages(query: str) -> List[dict]:
    """Build a stateless Anthropic message array for the current question only."""
    return [{"role": "user", "content": query}]


async def stream_answer(
    query: str,
    source_nodes: List,
) -> AsyncGenerator[str, None]:
    """
    Stream a grounded answer from Claude without conversational memory.

    The model receives only:
    - the current user query
    - the retrieved source chunks injected into the system prompt
    """
    settings = get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    system = build_system_prompt(source_nodes)
    messages = _build_messages(query)

    logger.info(
        "Streaming stateless answer | query=%r | nodes=%d",
        query[:80],
        len(source_nodes),
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
) -> str:
    """Non-streaming stateless variant used by Streamlit and simple clients."""
    settings = get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    system = build_system_prompt(source_nodes)
    messages = _build_messages(query)

    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        system=system,
        messages=messages,
    )
    return response.content[0].text
