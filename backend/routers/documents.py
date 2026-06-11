import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import crud
from backend.db.database import AsyncSessionLocal, get_db
from backend.ingestion.parser import SUPPORTED_EXTENSIONS
from backend.ingestion.pipeline import ingest_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


# ── Background ingestion task ─────────────────────────────────────────────────

async def _run_ingestion(file_path: str, document_id: str) -> None:
    """
    FastAPI BackgroundTask: runs ingestion pipeline and updates DB status.

    Uses three short-lived sessions so the SQLite write lock is never held
    during the 15–45 s blocking ingest_file call (Bug C fix — same pattern
    as tasks.py which was fixed in the previous audit pass).
    """
    # ── Mark processing (session closes immediately after) ───────────────────
    async with AsyncSessionLocal() as db:
        await crud.update_document_status(db, document_id, "processing")

    # ── Heavy work — no DB session open ──────────────────────────────────────
    try:
        chunk_count = await ingest_file(file_path, document_id)
    except Exception as exc:
        message = _friendly_ingestion_error(exc)
        logger.error(f"[{document_id}] Ingestion failed: {message}")
        async with AsyncSessionLocal() as db:
            await crud.update_document_status(db, document_id, "failed", error_message=message)
        return

    # ── Mark done (session closes immediately after) ──────────────────────────
    async with AsyncSessionLocal() as db:
        await crud.update_document_status(db, document_id, "done", chunk_count=chunk_count)
    logger.info(f"[{document_id}] Ingestion done — {chunk_count} chunks")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload", summary="Upload and ingest a document")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    safe_name = Path(file.filename or "upload").name
    ext = Path(safe_name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension '{ext or '(none)'}'. Supported: {supported}",
        )

    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()

    # Dedup check
    existing = await crud.get_document_by_hash(db, sha256)
    if existing:
        if existing.status == "failed" or (existing.chunk_count or 0) == 0:
            await crud.update_document_status(db, existing.id, "pending", error_message="")
            existing_path = existing.file_path
            if not existing_path or not Path(existing_path).exists():
                dest = UPLOAD_DIR / f"{existing.id}{ext}"
                dest.write_bytes(content)
                existing.file_path = str(dest)
                await db.commit()
                existing_path = str(dest)
            background_tasks.add_task(_run_ingestion, existing_path, existing.id)
            return {
                "document_id": existing.id,
                "filename": existing.filename,
                "status": "pending",
                "message": "Existing document queued for free re-indexing",
                "duplicate": True,
            }
        return {
            "document_id": existing.id,
            "filename": existing.filename,
            "status": existing.status,
            "message": "Document already ingested",
            "duplicate": True,
        }

    doc = await crud.create_document(
        db,
        filename=safe_name,
        original_filename=file.filename or safe_name,
        source_type=ext.lstrip("."),
        file_size_bytes=len(content),
        sha256_hash=sha256,
        status="pending",
    )

    dest = UPLOAD_DIR / f"{doc.id}{ext}"
    dest.write_bytes(content)
    doc.file_path = str(dest)
    await db.commit()

    background_tasks.add_task(_run_ingestion, str(dest), doc.id)

    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "message": "Ingestion started in background",
        "duplicate": False,
    }


@router.get("", summary="List all documents")
async def list_documents(db: AsyncSession = Depends(get_db)):
    docs = await crud.list_documents(db)
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "source_type": d.source_type,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "file_size_bytes": d.file_size_bytes,
            "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "error_message": d.error_message,
        }
        for d in docs
    ]


@router.get("/{document_id}/status", summary="Check ingestion status")
async def get_status(document_id: str, db: AsyncSession = Depends(get_db)):
    doc = await crud.get_document(db, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "error_message": doc.error_message,
        "ingested_at": doc.ingested_at.isoformat() if doc.ingested_at else None,
    }


@router.delete("/{document_id}", summary="Delete document and its chunks")
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db)):
    doc = await crud.get_document(db, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await crud.delete_document_chunks(db, document_id)

    # Remove uploaded file
    if doc.file_path:
        p = Path(doc.file_path)
        if p.exists():
            p.unlink()

    await crud.delete_document(db, document_id)
    return {"message": "Document deleted successfully"}


def _friendly_ingestion_error(exc: Exception) -> str:
    message = str(exc)
    lower = message.lower()
    if "api_key" in lower or "authentication" in lower or "401" in lower:
        return "Could not read this document because a parser dependency or key is missing."
    return message[:300] if len(message) > 300 else message
