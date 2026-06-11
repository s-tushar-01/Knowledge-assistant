# Personal AI Knowledge Assistant

A document-based knowledge assistant that works free by default. Upload PDF, DOCX, TXT, or MD files, search their contents with citations, and optionally add your own AI API key in the frontend for generated answers.

## Features

- Upload and parse PDF, DOCX, TXT, and MD files
- Store document metadata and searchable text chunks in SQLite
- Ask questions in free mode using keyword-based document search
- Return citations with filename, page number, score, and text preview
- Optionally generate AI answers with a session-only user API key
- Support Anthropic and OpenAI for optional AI answers
- Stream chat responses over Server-Sent Events
- Detect duplicate uploads using SHA-256 hashes
- Delete documents and their saved chunks

## How It Works

Free mode:

```text
Upload document -> parse text -> chunk text -> save chunks in SQLite -> search chunks -> show matching passages with citations
```

Optional AI mode:

```text
User enters API key in AI Settings -> frontend sends key only for that chat request -> backend retrieves document chunks -> AI generates an answer from those chunks
```

API keys entered in the UI are session-only. They are not saved to the database or `.env`, and they clear on page refresh.

## Tech Stack

- Backend: FastAPI, SQLAlchemy async, SQLite
- Frontend: Next.js, React, TypeScript
- Document parsing: pypdf, python-docx, plain text readers
- Optional AI providers: Anthropic, OpenAI

## Project Structure

```text
.
+-- backend/          # FastAPI app, ingestion, retrieval, database, optional AI chat
+-- nextjs_app/       # Next.js frontend
+-- scripts/          # Helper scripts
+-- uploads/          # Local uploaded files, ignored by git
+-- knowledge.db      # Local SQLite database, ignored by git
+-- requirements.txt  # Python dependencies
+-- .env.example      # Environment variable template
```

## Prerequisites

- Python 3.11+
- Node.js 20+
- npm

AI provider keys are optional. The app still uploads, indexes, searches, and cites documents without them.

## Environment Setup

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Minimum backend variables:

```env
DATABASE_URL=sqlite+aiosqlite:///./knowledge.db
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

For deployed frontend, set `CORS_ORIGINS` to your Vercel URL:

```env
CORS_ORIGINS=https://your-vercel-app.vercel.app
```

You do not need these in backend env anymore:

```env
OPENAI_API_KEY
ANTHROPIC_API_KEY
CHROMA_COLLECTION_NAME
CHROMA_PERSIST_PATH
EMBEDDING_MODEL
EMBEDDING_PROVIDER
```

Users can enter Anthropic or OpenAI keys in the frontend AI Settings panel when they want AI-generated answers.

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

If the frontend is deployed separately, set this in Vercel:

```env
NEXT_PUBLIC_API_URL=https://your-render-backend.onrender.com
```

## Main API Endpoints

- `GET /health` - backend health check
- `POST /api/documents/upload` - upload and ingest a document
- `GET /api/documents` - list uploaded documents
- `GET /api/documents/{document_id}/status` - check ingestion status
- `DELETE /api/documents/{document_id}` - delete a document and saved chunks
- `POST /api/chat` - stream free search results or optional AI answer using SSE
- `POST /api/chat/sync` - return a non-streaming free search answer

Optional AI request headers:

```text
X-AI-Provider: anthropic | openai
X-AI-Key: user_session_api_key
```

## Deployment

Recommended free hosting:

- Frontend: Vercel
- Backend: Render free web service

Render backend settings:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

Render env:

```env
DATABASE_URL=sqlite+aiosqlite:///./knowledge.db
CORS_ORIGINS=https://your-vercel-app.vercel.app
```

Vercel env:

```env
NEXT_PUBLIC_API_URL=https://your-render-backend.onrender.com
```

Because Render free storage is ephemeral, uploaded files and SQLite data may reset after redeploys or restarts. This setup is best for demos and lightweight use.

## Typical Workflow

1. Start the backend on port `8000`.
2. Start the Next.js frontend on port `3000`.
3. Upload a supported document.
4. Wait until ingestion status is `Ready`.
5. Ask questions in free mode for matching passages and citations.
6. Optionally paste an Anthropic or OpenAI key in AI Settings for generated answers.

## Notes

- Keep `.env` private.
- Do not commit uploaded files or `knowledge.db`.
- API keys entered in the frontend are temporary and clear on refresh.
- Free mode is search-based, not a full AI summarizer.
