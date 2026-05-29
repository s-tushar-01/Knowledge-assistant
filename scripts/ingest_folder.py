#!/usr/bin/env python
"""
Bulk-ingest all supported files in a local directory.

Usage:
    python scripts/ingest_folder.py --path ~/Documents/notes
    python scripts/ingest_folder.py --path ./reports --recursive
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.db.database import init_db, AsyncSessionLocal
from backend.db import crud
from backend.ingestion.pipeline import ingest_file
from backend.ingestion.parser import SUPPORTED_EXTENSIONS

import hashlib
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def ingest_folder(folder: Path, recursive: bool) -> None:
    await init_db()

    pattern = "**/*" if recursive else "*"
    files = [
        f for f in folder.glob(pattern)
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    logger.info(f"Found {len(files)} supported files in {folder}")

    for file_path in files:
        content = file_path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()

        async with AsyncSessionLocal() as db:
            existing = await crud.get_document_by_hash(db, sha256)
            if existing:
                logger.info(f"[SKIP] Already ingested: {file_path.name}")
                continue

            doc = await crud.create_document(
                db,
                filename=file_path.name,
                original_filename=str(file_path),
                source_type=file_path.suffix.lstrip(".").lower(),
                file_size_bytes=len(content),
                sha256_hash=sha256,
                file_path=str(file_path),
                status="pending",
            )

        logger.info(f"[START] {file_path.name} → {doc.id}")
        async with AsyncSessionLocal() as db:
            await crud.update_document_status(db, doc.id, "processing")
            try:
                chunk_count = await ingest_file(str(file_path), doc.id)
                await crud.update_document_status(db, doc.id, "done", chunk_count=chunk_count)
                logger.info(f"[DONE]  {file_path.name} — {chunk_count} chunks")
            except Exception as exc:
                await crud.update_document_status(db, doc.id, "failed", error_message=str(exc))
                logger.error(f"[FAIL]  {file_path.name}: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Bulk-ingest a folder into the knowledge base")
    parser.add_argument("--path", required=True, help="Folder to ingest")
    parser.add_argument("--recursive", action="store_true", help="Recurse into sub-folders")
    args = parser.parse_args()

    folder = Path(args.path).expanduser().resolve()
    if not folder.is_dir():
        logger.error(f"Not a directory: {folder}")
        sys.exit(1)

    asyncio.run(ingest_folder(folder, args.recursive))


if __name__ == "__main__":
    main()
