from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Document


async def create_document(db: AsyncSession, **kwargs) -> Document:
    doc = Document(**kwargs)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def get_document(db: AsyncSession, document_id: str) -> Optional[Document]:
    result = await db.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()


async def get_document_by_hash(db: AsyncSession, sha256_hash: str) -> Optional[Document]:
    result = await db.execute(select(Document).where(Document.sha256_hash == sha256_hash))
    return result.scalar_one_or_none()


async def list_documents(db: AsyncSession) -> List[Document]:
    result = await db.execute(select(Document).order_by(Document.created_at.desc()))
    return list(result.scalars().all())


async def update_document_status(
    db: AsyncSession,
    document_id: str,
    status: str,
    chunk_count: Optional[int] = None,
    error_message: Optional[str] = None,
) -> Optional[Document]:
    doc = await get_document(db, document_id)
    if doc is None:
        return None
    doc.status = status
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    if error_message is not None:
        doc.error_message = error_message
    if status == "done":
        doc.ingested_at = datetime.utcnow()
    await db.commit()
    await db.refresh(doc)
    return doc


async def delete_document(db: AsyncSession, document_id: str) -> bool:
    doc = await get_document(db, document_id)
    if doc is None:
        return False
    await db.delete(doc)
    await db.commit()
    return True
