import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    source_type = Column(String)          # pdf | docx | md | txt | html | eml
    file_size_bytes = Column(Integer)
    sha256_hash = Column(String, unique=True, index=True)
    status = Column(String, default="pending")  # pending | processing | done | failed
    chunk_count = Column(Integer, default=0)
    error_message = Column(String, nullable=True)
    file_path = Column(String, nullable=True)
    ingested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    extra_metadata = Column(JSON, default=dict)
