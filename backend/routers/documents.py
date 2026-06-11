import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import crud
from backend.db.database import AsyncSessionLocal, get_db
from backend.ingestion.parser import SUPPORTED_EXTENSIONS
from backend.ingestion.pipeline import DOCSTORE_DIR, DOCSTORE_PATH
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
        logger.error(f"[{document_id}] Ingestion failed: {exc}")
        async with AsyncSessionLocal() as db:
            await crud.update_document_status(db, document_id, "failed", error_message=str(exc))
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


def _remove_from_docstore(document_id: str) -> int:
    """Remove this document's nodes from the persisted BM25 docstore."""
    docstore_dir = Path(DOCSTORE_DIR)
    if not docstore_dir.exists():
        return 0

    from llama_index.core.storage.docstore import SimpleDocumentStore

    docstore = SimpleDocumentStore.from_persist_dir(DOCSTORE_DIR)
    node_ids = [
        node_id
        for node_id, node in docstore.docs.items()
        if node.metadata.get("document_id") == document_id
    ]
    for node_id in node_ids:
        docstore.delete_document(node_id, raise_error=False)

    docstore.persist(persist_path=DOCSTORE_PATH)
    return len(node_ids)


@router.delete("/{document_id}", summary="Delete document and its vectors")
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db)):
    doc = await crud.get_document(db, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove chunks from Chroma
    try:
        from backend.retrieval.vector_store import get_chroma_client
        settings = get_settings()
        col = get_chroma_client().get_or_create_collection(settings.chroma_collection_name)
        col.delete(where={"document_id": document_id})
    except Exception as exc:
        logger.warning(f"Could not remove vectors for {document_id}: {exc}")

    # Remove chunks from the BM25 docstore
    try:
        removed = _remove_from_docstore(document_id)
        logger.info("Removed %d BM25 docstore nodes for %s", removed, document_id)
    except Exception as exc:
        logger.warning(f"Could not remove BM25 docstore nodes for {document_id}: {exc}")

    # Remove uploaded file
    if doc.file_path:
        p = Path(doc.file_path)
        if p.exists():
            p.unlink()

    await crud.delete_document(db, document_id)
    return {"message": "Document deleted successfully"}
