"""
Celery worker for async document ingestion.

Start the worker:
    celery -A backend.tasks worker --loglevel=info --concurrency=4

Monitor via Flower:
    celery -A backend.tasks flower --port=5555
"""

import asyncio
import logging

from celery import Celery

from backend.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
app = Celery("knowledge_assistant", broker=settings.redis_url, backend=settings.redis_url)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,   # one task at a time per worker (heavy ingestion)
)


@app.task(bind=True, name="tasks.ingest_document", max_retries=3, default_retry_delay=30)
def ingest_document(self, file_path: str, document_id: str) -> dict:
    """
    Celery task: parse → chunk → embed → store.
    Updates DB status on start / completion / failure.
    Falls back to a new event loop since Celery workers are synchronous.
    """
    from backend.db.database import AsyncSessionLocal
    from backend.db import crud
    from backend.ingestion.pipeline import ingest_file

    async def _run():
        # ── Mark processing (short-lived session, closes immediately) ──────────
        async with AsyncSessionLocal() as db:
            await crud.update_document_status(db, document_id, "processing")

        # ── Heavy blocking work — no session held open ─────────────────────────
        chunk_count: int | None = None
        try:
            chunk_count = await ingest_file(file_path, document_id)
        except Exception as exc:
            async with AsyncSessionLocal() as db:
                await crud.update_document_status(
                    db, document_id, "failed", error_message=str(exc)
                )
            raise self.retry(exc=exc)

        # ── Mark done (short-lived session) ────────────────────────────────────
        async with AsyncSessionLocal() as db:
            await crud.update_document_status(
                db, document_id, "done", chunk_count=chunk_count
            )
        return {"status": "done", "chunk_count": chunk_count}

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
