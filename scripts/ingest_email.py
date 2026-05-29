#!/usr/bin/env python
"""
Ingest emails from an MBOX file.

Usage:
    python scripts/ingest_email.py --path ~/mail/archive.mbox
    python scripts/ingest_email.py --path inbox.mbox --since 2024-01-01
"""

import argparse
import asyncio
import hashlib
import logging
import mailbox
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.db.database import init_db, AsyncSessionLocal
from backend.db import crud
from backend.ingestion.pipeline import ingest_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def ingest_mbox(mbox_path: Path, since: datetime | None) -> None:
    await init_db()

    mbox = mailbox.mbox(str(mbox_path))
    messages = list(mbox)
    logger.info(f"Found {len(messages)} messages in {mbox_path.name}")

    count = 0
    for msg in messages:
        date_str = msg.get("Date", "")
        subject = msg.get("Subject", "(no subject)")
        sender = msg.get("From", "unknown")

        # Date filter
        if since:
            try:
                from email.utils import parsedate_to_datetime
                msg_date = parsedate_to_datetime(date_str)
                if msg_date.replace(tzinfo=None) < since:
                    continue
            except Exception:
                pass

        # Extract body
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body += part.get_payload(decode=True).decode("utf-8", errors="replace")
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            except Exception:
                body = str(msg.get_payload())

        full_text = f"From: {sender}\nDate: {date_str}\nSubject: {subject}\n\n{body}"
        sha256 = hashlib.sha256(full_text.encode()).hexdigest()

        async with AsyncSessionLocal() as db:
            if await crud.get_document_by_hash(db, sha256):
                continue

            doc = await crud.create_document(
                db,
                filename=f"email_{sha256[:8]}.eml",
                original_filename=f"{subject[:60]}.eml",
                source_type="eml",
                file_size_bytes=len(full_text.encode()),
                sha256_hash=sha256,
                status="pending",
            )

        # Write to temp file so the parser can read it
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(full_text)
            tmp_path = tmp.name

        async with AsyncSessionLocal() as db:
            await crud.update_document_status(db, doc.id, "processing")
            try:
                chunk_count = await ingest_file(tmp_path, doc.id)
                await crud.update_document_status(db, doc.id, "done", chunk_count=chunk_count)
                count += 1
            except Exception as exc:
                await crud.update_document_status(db, doc.id, "failed", error_message=str(exc))
                logger.error(f"Failed email '{subject}': {exc}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

    logger.info(f"Ingested {count} new emails from {mbox_path.name}")


def main():
    parser = argparse.ArgumentParser(description="Ingest emails from an MBOX file")
    parser.add_argument("--path", required=True, help="Path to .mbox file")
    parser.add_argument("--since", help="Only ingest emails after this date (YYYY-MM-DD)")
    args = parser.parse_args()

    mbox_path = Path(args.path).expanduser().resolve()
    if not mbox_path.exists():
        logger.error(f"File not found: {mbox_path}")
        sys.exit(1)

    since = datetime.strptime(args.since, "%Y-%m-%d") if args.since else None
    asyncio.run(ingest_mbox(mbox_path, since))


if __name__ == "__main__":
    main()
