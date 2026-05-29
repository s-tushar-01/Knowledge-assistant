"""
CLAUDE.md compliance audit — checks every architectural rule against the
current implementation. Run with:
    python scripts/claude_audit.py
"""
import pathlib
import sys

ROOT = pathlib.Path("backend")
findings = []


def check(passed: bool, component: str, rule: str, detail: str) -> None:
    findings.append((passed, component, rule, detail))


# ── Read all source files ─────────────────────────────────────────────────────
cfg      = (ROOT / "config.py").read_text()
emb      = (ROOT / "ingestion/embedder.py").read_text()
vs       = (ROOT / "retrieval/vector_store.py").read_text()
ret      = (ROOT / "retrieval/retriever.py").read_text()
pipe     = (ROOT / "ingestion/pipeline.py").read_text()
chat     = (ROOT / "routers/chat.py").read_text()
docs_r   = (ROOT / "routers/documents.py").read_text()
models   = (ROOT / "db/models.py").read_text()
prompts  = (ROOT / "llm/prompts.py").read_text()
parser   = (ROOT / "ingestion/parser.py").read_text()
chunker  = (ROOT / "ingestion/chunker.py").read_text()
claude   = (ROOT / "llm/claude.py").read_text()
main     = (ROOT / "main.py").read_text()

# ── 1. Chunking parameters (CLAUDE.md § Chunking) ────────────────────────────
check("chunk_size: int = 512"   in cfg, "config.py",   "chunk_size=512",      "512 token default chunk size")
check("chunk_overlap: int = 64" in cfg, "config.py",   "chunk_overlap=64",    "64 token overlap (≈12.5% of 512)")
check("SentenceSplitter"         in chunker, "chunker.py", "SentenceSplitter", "Uses SentenceSplitter as specified")

# ── 2. Retrieval top-k (CLAUDE.md § Retrieval) ───────────────────────────────
check("similarity_top_k: int = 8" in cfg, "config.py", "pre-rank top_k=8",   "8 candidates before re-ranking")
check("rerank_top_k: int = 4"     in cfg, "config.py", "post-rank top_k=4",  "4 final chunks fed to LLM")

# ── 3. Embedding model (CLAUDE.md § Embeddings) ──────────────────────────────
check("text-embedding-3-small" in emb,  "embedder.py", "model=text-embedding-3-small", "Correct OpenAI embed model")
check("@lru_cache"             in emb,  "embedder.py", "singleton factory",            "lru_cache singleton (no re-init per request)")
check("nomic-embed-text"       in emb,  "embedder.py", "local fallback",               "Ollama nomic-embed-text local alternative")

# ── 4. Vector store (CLAUDE.md § Vector DB) ──────────────────────────────────
check("cosine"                 in vs,   "vector_store.py", "cosine similarity",    "hnsw:space=cosine (not dot product)")
check("PersistentClient"       in vs,   "vector_store.py", "persistent Chroma",   "Persists to disk (./chroma_db)")
check("@lru_cache"             in vs,   "vector_store.py", "singleton client",     "lru_cache prevents duplicate Chroma clients")

# ── 5. Hybrid retrieval (CLAUDE.md § Retrieval strategy) ─────────────────────
check("BM25Retriever"          in ret,  "retriever.py", "BM25 retriever",        "BM25Retriever for sparse keyword matching")
check("QueryFusionRetriever"   in ret,  "retriever.py", "fusion retriever",      "QueryFusionRetriever combining BM25 + vector")
check("reciprocal_rerank"      in ret,  "retriever.py", "RRF fusion mode",       "mode=reciprocal_rerank as specified")
check("num_queries=1"          in ret,  "retriever.py", "no sub-queries",        "num_queries=1 (no spurious query expansion)")
check("use_async=True"         in ret,  "retriever.py", "async fusion",          "use_async=True for non-blocking retrieval")

# ── 6. No global Settings mutation (Bug B) ───────────────────────────────────
# li_global.Settings must NEVER be mutated — it is a thread-unsafe shared singleton
ret_lines   = [l.strip() for l in ret.split("\n")]
pipe_lines  = [l.strip() for l in pipe.split("\n")]
bad_ret     = [l for l in ret_lines  if "li_global.Settings." in l and not l.startswith("#")]
bad_pipe    = [l for l in pipe_lines if "li_global.Settings." in l and not l.startswith("#")]
check(len(bad_ret)  == 0, "retriever.py", "no global Settings mutation", f"No li_global.Settings.X=Y mutations (found: {bad_ret})")
check(len(bad_pipe) == 0, "pipeline.py",  "no global Settings mutation", f"No li_global.Settings.X=Y mutations (found: {bad_pipe})")

# Explicit embed_model passing
check("embed_model=embed_model" in pipe,  "pipeline.py",  "explicit embed_model", "embed_model passed explicitly to VectorStoreIndex")
check("embed_model=embed_model" in ret,   "retriever.py", "explicit embed_model", "embed_model passed explicitly to from_vector_store")

# ── 7. Docstore merge (Bug A) ─────────────────────────────────────────────────
check("from_persist_dir" in pipe,          "pipeline.py", "docstore load+merge",  "Loads existing docstore before ingestion")
check("add_documents"    in pipe,          "pipeline.py", "add_documents merge",   "add_documents() merges; does not overwrite")
check("persist("         in pipe,          "pipeline.py", "docstore persist",      "Persists merged docstore after ingestion")

# ── 8. Async event loop (Bug D) ───────────────────────────────────────────────
check("get_running_loop" in pipe,          "pipeline.py", "get_running_loop",      "Uses get_running_loop() (not deprecated get_event_loop)")
check("get_event_loop()" not in pipe,      "pipeline.py", "no get_event_loop",     "No deprecated get_event_loop() call")
check("run_in_executor"  in pipe,          "pipeline.py", "ThreadPoolExecutor",    "Blocking I/O offloaded to executor")

# ── 9. DB session lock (Bug C) ───────────────────────────────────────────────
ingestion_body = docs_r.split("async def _run_ingestion")[1].split("\nasync def ")[0]
session_blocks = ingestion_body.count("async with AsyncSessionLocal")
check(session_blocks >= 2,  "documents.py", "short-lived DB sessions",
      f"Found {session_blocks} short-lived AsyncSessionLocal blocks (lock-free during heavy ingest)")

# ── 10. SSE streaming — never raise HTTPException inside generator ────────────
# Extract only the `async def chat` body (before chat_sync)
chat_gen_body = chat.split("async def chat(request")[1].split("async def chat_sync")[0]
check("StreamingResponse" in chat_gen_body,    "chat.py", "StreamingResponse",       "StreamingResponse wraps the generator")
check("text/event-stream" in chat_gen_body,    "chat.py", "SSE media type",          "media_type=text/event-stream")
check("raise HTTPException" not in chat_gen_body, "chat.py", "no HTTPException in SSE",
      "No HTTPException inside SSE generator (would break stream)")
check("type.*error" in chat_gen_body or '"type": "error"' in chat_gen_body or
      "type', 'error'" in chat_gen_body,        "chat.py", "typed SSE error events",  "Errors emitted as typed SSE {type:error} events")
check("_friendly_error" in chat,               "chat.py", "friendly error messages", "_friendly_error() translates exceptions to user text")

# ── 11. Prompt assembly (CLAUDE.md § LLM) ────────────────────────────────────
check("CONTEXT" in prompts,                    "prompts.py", "context sentinel",       "<<CONTEXT>> sentinel in system prompt")
check(".replace(" in prompts,                  "prompts.py", "str.replace injection",  "str.replace() injects context (not str.format)")
check(".format(" not in prompts.replace("# NOTE:", ""), "prompts.py", "no str.format", "No str.format() that would fail on { in chunks")
check("cite the source" in prompts or "cite" in prompts.lower(), "prompts.py", "citation instruction", "Prompt instructs Claude to cite sources")
check("ONLY" in prompts,                       "prompts.py", "grounding instruction",  "Prompt constrains to context only")

# ── 12. Metadata schema (CLAUDE.md § Metadata store) ─────────────────────────
check("sha256_hash"    in models, "models.py", "sha256 dedup column",   "SHA-256 for deduplication")
check("status"         in models, "models.py", "status column",         "pending/processing/done/failed lifecycle")
check("chunk_count"    in models, "models.py", "chunk_count column",    "chunk_count tracked in DB")
check("ingested_at"    in models, "models.py", "ingested_at timestamp", "ingested_at timestamp column")
check("source_type"    in models, "models.py", "source_type column",    "source_type (pdf/docx/md...)")

# ── 13. Metadata exclusions from embed/LLM (CLAUDE.md § Chunking metadata) ───
check("excluded_embed_metadata_keys" in parser, "parser.py", "embed exclusion keys",  "file_path/document_id excluded from embeddings")
check("excluded_llm_metadata_keys"   in parser, "parser.py", "llm  exclusion keys",   "file_path/document_id excluded from LLM context")

# ── 14. Document parser uses Unstructured (CLAUDE.md § Document parsing) ──────
check("from unstructured" in parser or "unstructured.partition" in parser, "parser.py",
      "Unstructured parser", "Uses Unstructured as primary parser")
check("section_heading" in parser, "parser.py", "section metadata",      "section_heading metadata preserved")
check("page_number"     in parser, "parser.py", "page metadata",         "page_number metadata preserved")

# ── 15. CORS + async FastAPI wiring ──────────────────────────────────────────
check("CORSMiddleware"      in main, "main.py", "CORS middleware",        "CORSMiddleware configured")
check("asynccontextmanager" in main, "main.py", "lifespan context",       "asynccontextmanager lifespan pattern")
check("init_db"             in main, "main.py", "DB init on startup",     "init_db() called at startup")

# ── Report ────────────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("  CLAUDE.md ARCHITECTURE COMPLIANCE AUDIT")
print("=" * 72)

passed_all = failed_all = 0
for (passed, component, rule, detail) in findings:
    icon = "✅" if passed else "❌"
    if passed:
        passed_all += 1
    else:
        failed_all += 1
    print(f"  {icon}  [{component:<22s}] {rule}")
    if not passed:
        print(f"         ↳ VIOLATION: {detail}")

print()
print(f"  Result: {passed_all} PASS, {failed_all} FAIL  (total {passed_all+failed_all} checks)")
if failed_all == 0:
    print("  ✅  All architecture checks PASSED — system is CLAUDE.md compliant")
else:
    print("  ❌  VIOLATIONS found — see items marked with ❌ above")
print("=" * 72)
sys.exit(0 if failed_all == 0 else 1)
