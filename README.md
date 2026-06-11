# Personal AI Knowledge Assistant

A document-based AI knowledge assistant that lets you upload files, indexes their content, and answers questions with cited sources. The backend is a FastAPI RAG service, and the frontend is a Next.js app.

## Features

- Upload documents and ingest them in the background
- Store document metadata in SQLite
- Store embeddings in ChromaDB
- Retrieve relevant chunks with vector and BM25-style retrieval
- Stream grounded answers over Server-Sent Events
- Return citations with filename, page number, heading, score, and preview text
- Detect duplicate uploads using SHA-256 hashes
- Delete documents and remove their indexed chunks

## Tech Stack

- Backend: FastAPI, SQLAlchemy async, LlamaIndex
- Frontend: Next.js, React, TypeScript
- Vector store: ChromaDB
- Metadata DB: SQLite by default
- LLM: Anthropic Claude
- Embeddings: OpenAI by default, Ollama optional

## Project Structure

```text
.
+-- backend/          # FastAPI app, ingestion, retrieval, database, LLM code
+-- nextjs_app/       # Next.js frontend
+-- scripts/          # Helper scripts
+-- uploads/          # Uploaded files
+-- chroma_db/        # Chroma persistence directory
+-- knowledge.db      # SQLite metadata database
+-- requirements.txt  # Python dependencies
+-- .env.example      # Environment variable template
```

## Prerequisites

- Python 3.11+
- Node.js 20+
- npm
- Anthropic API key
- OpenAI API key, unless you configure Ollama embeddings

## Environment Setup

Copy the example environment file and fill in your keys:

```powershell
Copy-Item .env.example .env
```

Important variables:

```env
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-5
OPENAI_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_PROVIDER=openai
CHROMA_PERSIST_PATH=./chroma_db
CHROMA_COLLECTION_NAME=knowledge_base
DATABASE_URL=sqlite+aiosqlite:///./knowledge.db
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

## Backend Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Run the FastAPI server:

```powershell
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

Interactive API docs:

```text
http://127.0.0.1:8000/docs
```

## Frontend Setup

Open a second terminal:

```powershell
cd nextjs_app
npm install
npm run dev
```

Then open:

```text
http://localhost:3000
```

## Main API Endpoints

- `GET /health` - backend health check
- `POST /api/documents/upload` - upload and ingest a document
- `GET /api/documents` - list uploaded documents
- `GET /api/documents/{document_id}/status` - check ingestion status
- `DELETE /api/documents/{document_id}` - delete a document and indexed chunks
- `POST /api/chat` - stream a cited answer using SSE
- `POST /api/chat/sync` - return a non-streaming answer

## Typical Workflow

1. Start the backend on port `8000`.
2. Start the Next.js frontend on port `3000`.
3. Upload one or more supported documents.
4. Wait until ingestion status is `done`.
5. Ask questions from the frontend.
6. Review citations returned with each answer.

## Notes

- Runtime data such as `uploads/`, `chroma_db/`, and `knowledge.db` is local project state.
- Keep `.env` private and do not commit API keys.
- If using Ollama for embeddings, set `EMBEDDING_PROVIDER=ollama` and make sure Ollama is running at `OLLAMA_BASE_URL`.
