"""
Personal AI Knowledge Assistant — Streamlit MVP
Run: streamlit run frontend/streamlit_app.py
Requires the FastAPI backend to be running on localhost:8000
"""

import asyncio
import time
from pathlib import Path

import httpx
import streamlit as st

API_BASE = "http://localhost:8000"

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Knowledge Assistant",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stChatMessage { border-radius: 12px; }
    .source-card {
        background: #1e293b; color: #e2e8f0;
        border-left: 3px solid #6366f1;
        border-radius: 8px; padding: 10px 14px;
        margin-bottom: 8px; font-size: 0.83rem;
    }
    .source-card strong { color: #a5b4fc; }
    .badge-done   { color: #4ade80; font-weight: 600; }
    .badge-fail   { color: #f87171; font-weight: 600; }
    .badge-proc   { color: #facc15; font-weight: 600; }
    .badge-pend   { color: #94a3b8; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session state ─────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "citations" not in st.session_state:
    st.session_state.citations = []   # citations for the last answer


# ── Helpers ───────────────────────────────────────────────────────────────────

def api_get(path: str):
    try:
        r = httpx.get(f"{API_BASE}{path}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, **kwargs):
    try:
        r = httpx.post(f"{API_BASE}{path}", timeout=30, **kwargs)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def upload_file(uploaded_file) -> dict | None:
    try:
        r = httpx.post(
            f"{API_BASE}/api/documents/upload",
            files={"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Upload error: {e}")
        return None


def poll_status(document_id: str, max_wait: int = 120) -> str:
    """Poll until done/failed or timeout."""
    start = time.time()
    while time.time() - start < max_wait:
        data = api_get(f"/api/documents/{document_id}/status")
        if data:
            status = data.get("status", "pending")
            if status in ("done", "failed"):
                return status
        time.sleep(2)
    return "timeout"


def status_badge(status: str) -> str:
    classes = {
        "done": "badge-done", "failed": "badge-fail",
        "processing": "badge-proc", "pending": "badge-pend",
    }
    cls = classes.get(status, "badge-pend")
    icons = {"done": "✅", "failed": "❌", "processing": "⏳", "pending": "🕐"}
    icon = icons.get(status, "•")
    return f'<span class="{cls}">{icon} {status}</span>'


def stream_chat(query: str):
    """Call /api/chat and yield (event_type, data) tuples."""
    import json as _json
    with httpx.stream("POST", f"{API_BASE}/api/chat",
                      json={"query": query}, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if raw == "[DONE]":
                break
            try:
                yield _json.loads(raw)
            except Exception:
                continue


# ── Sidebar — document management ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🧠 Knowledge Assistant")
    st.divider()

    # Upload
    st.markdown("### 📂 Upload Document")
    uploaded = st.file_uploader(
        "PDF, DOCX, PPTX, MD, TXT, HTML, EML, MBOX",
        type=["pdf", "docx", "doc", "pptx", "md", "txt", "html", "htm", "eml", "mbox"],
        label_visibility="collapsed",
    )
    if uploaded:
        if st.button("Ingest →", use_container_width=True, type="primary"):
            with st.spinner(f"Uploading {uploaded.name} …"):
                result = upload_file(uploaded)
            if result:
                if result.get("duplicate"):
                    st.info(f"Already ingested: **{result['filename']}**")
                else:
                    doc_id = result["document_id"]
                    st.success(f"Uploaded! Processing …")
                    with st.spinner("Ingesting chunks …"):
                        final_status = poll_status(doc_id)
                    if final_status == "done":
                        data = api_get(f"/api/documents/{doc_id}/status")
                        n = data.get("chunk_count", "?") if data else "?"
                        st.success(f"Done — {n} chunks indexed ✅")
                    elif final_status == "failed":
                        data = api_get(f"/api/documents/{doc_id}/status")
                        st.error(f"Failed: {data.get('error_message', 'unknown error') if data else ''}")
                    else:
                        st.warning("Timed out waiting for ingestion.")

    st.divider()

    # Document list
    st.markdown("### 📚 Indexed Documents")
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()

    docs = api_get("/api/documents") or []
    if not docs:
        st.caption("No documents yet.")
    for doc in docs:
        cols = st.columns([3, 1])
        with cols[0]:
            st.markdown(
                f"**{doc['filename']}**<br>"
                f"{status_badge(doc['status'])} &nbsp; "
                f"<small>{doc.get('chunk_count', 0)} chunks</small>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            if st.button("🗑", key=f"del_{doc['id']}"):
                try:
                    httpx.delete(f"{API_BASE}/api/documents/{doc['id']}", timeout=10)
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        st.divider()


# ── Main area — chat ───────────────────────────────────────────────────────────
st.markdown("## 💬 Ask your knowledge base")

# Render chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Source panel below last assistant message
if st.session_state.citations:
    with st.expander(f"📎 {len(st.session_state.citations)} source(s) used", expanded=False):
        for c in st.session_state.citations:
            score_pct = int((c.get("score") or 0) * 100)
            preview = c.get("text_preview", "")
            st.markdown(
                f"""<div class="source-card">
                <strong>{c.get('source_file', 'unknown')}</strong> &nbsp;
                Page {c.get('page_number', '?')} &nbsp;|&nbsp;
                Relevance: {score_pct}%<br>
                <em>{c.get('section_heading', '')}</em><br>
                <small>{preview}</small>
                </div>""",
                unsafe_allow_html=True,
            )

# Chat input
if prompt := st.chat_input("Ask anything about your documents …"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.citations = []

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        answer_placeholder = st.empty()
        full_answer = ""
        new_citations = []

        try:
            for event in stream_chat(prompt):
                etype = event.get("type")
                if etype == "citations":
                    new_citations = event.get("citations", [])
                elif etype == "token":
                    full_answer += event.get("content", "")
                    answer_placeholder.markdown(full_answer + "▌")
                elif etype == "error":
                    st.error(event.get("message", "Unknown error"))
                    break
            answer_placeholder.markdown(full_answer)
        except Exception as exc:
            st.error(f"Connection error: {exc}")
            full_answer = "Sorry, I couldn't reach the backend."
            answer_placeholder.markdown(full_answer)

    st.session_state.messages.append({"role": "assistant", "content": full_answer})
    st.session_state.citations = new_citations
    st.rerun()
