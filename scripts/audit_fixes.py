#!/usr/bin/env python
"""
Audit validation script — run AFTER starting the FastAPI backend.

    uvicorn backend.main:app --reload --port 8000
    python scripts/audit_fixes.py

Tests each of the 5 bugs that were identified and fixed.
"""

import json
import sys
import time
import threading
from pathlib import Path

import httpx

API = "http://localhost:8000"
PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
results = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    print(f"{tag}  {label}")
    if detail:
        print(f"       {detail}")
    results.append(ok)


def wait_for_status(doc_id: str, target: str, timeout: int = 120) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = httpx.get(f"{API}/api/documents/{doc_id}/status", timeout=5)
        status = r.json().get("status", "unknown")
        if status in (target, "failed", "done"):
            return status
        time.sleep(2)
    return "timeout"


# ── Helper: create a small in-memory PDF/text file ────────────────────────────

def make_test_file(content: str, suffix: str = ".txt") -> bytes:
    return content.encode()


# ── Test 0: backend reachable ──────────────────────────────────────────────────

print("\n=== Checking backend health ===")
try:
    r = httpx.get(f"{API}/health", timeout=5)
    check("Backend reachable", r.status_code == 200)
except Exception as e:
    print(f"{FAIL}  Backend not reachable: {e}")
    print("       Start it with: uvicorn backend.main:app --reload --port 8000")
    sys.exit(1)


# ── Test Bug 5: curly braces in chunk text don't crash prompt assembly ─────────

print("\n=== Bug 5: Curly-brace-safe prompt assembly ===")
brace_content = (
    'This document contains JSON: {"key": "value", "nested": {"a": 1}}\n'
    "Also YAML:\nconfig:\n  items: {host: localhost, port: 8000}\n"
    "This is a test document for the knowledge assistant."
)

resp = httpx.post(
    f"{API}/api/documents/upload",
    files={"file": ("brace_test.txt", brace_content.encode(), "text/plain")},
    timeout=10,
)
check("Upload succeeds (brace file)", resp.status_code == 200)
doc_id = resp.json().get("document_id")

if doc_id:
    final = wait_for_status(doc_id, "done")
    check("Ingestion completes without error", final == "done", f"status={final}")

    # Query — should NOT raise KeyError
    events = []
    with httpx.stream(
        "POST", f"{API}/api/chat",
        json={"query": "What JSON keys are in this document?"},
        timeout=60,
    ) as s:
        for line in s.iter_lines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw != "[DONE]":
                    try:
                        events.append(json.loads(raw))
                    except Exception:
                        pass

    error_events = [e for e in events if e.get("type") == "error"]
    token_events = [e for e in events if e.get("type") == "token"]
    check("No error event (no KeyError from braces)", len(error_events) == 0,
          f"errors={error_events}")
    check("At least one token streamed", len(token_events) > 0,
          f"tokens={len(token_events)}")


# ── Test Bug 4: async retrieval doesn't block event loop ──────────────────────

print("\n=== Bug 4: Async retrieval ===")

concurrent_latencies = []
errors = []

def send_query(q: str):
    t0 = time.time()
    try:
        tokens = []
        with httpx.stream("POST", f"{API}/api/chat",
                          json={"query": q}, timeout=30) as s:
            for line in s.iter_lines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw != "[DONE]":
                        try:
                            ev = json.loads(raw)
                            if ev.get("type") == "token":
                                tokens.append(ev["content"])
                        except Exception:
                            pass
        concurrent_latencies.append(time.time() - t0)
    except Exception as e:
        errors.append(str(e))

threads = [threading.Thread(target=send_query, args=(f"test query {i}",)) for i in range(3)]
t_start = time.time()
for t in threads: t.start()
for t in threads: t.join()
wall = time.time() - t_start

check("3 concurrent queries complete without error", len(errors) == 0, f"errors={errors}")
if concurrent_latencies:
    avg = sum(concurrent_latencies) / len(concurrent_latencies)
    # If event loop was blocked, wall time ≈ sum of latencies; concurrent, wall ≈ max
    check(
        "Queries ran concurrently (wall < sum of latencies)",
        wall < sum(concurrent_latencies) * 0.8,
        f"wall={wall:.1f}s, sum_latencies={sum(concurrent_latencies):.1f}s",
    )


# ── Test Bug 3: status poll during ingestion returns promptly ─────────────────

print("\n=== Bug 3: DB not locked during ingestion ===")

large_content = ("This is paragraph content. " * 200 + "\n") * 30  # ~180KB
resp2 = httpx.post(
    f"{API}/api/documents/upload",
    files={"file": ("large_test.txt", large_content.encode(), "text/plain")},
    timeout=10,
)
check("Large file upload accepted immediately", resp2.status_code == 200)
doc_id2 = resp2.json().get("document_id")

if doc_id2:
    # Poll status immediately — should not hang on SQLite lock
    t0 = time.time()
    r = httpx.get(f"{API}/api/documents/{doc_id2}/status", timeout=3)
    poll_latency = time.time() - t0
    check(
        "Status poll returns in <2s during ingestion",
        poll_latency < 2.0 and r.status_code == 200,
        f"latency={poll_latency:.2f}s status={r.json().get('status')}",
    )


# ── Test Bug 2: BM25 docstore populated after ingestion ───────────────────────

print("\n=== Bug 2: BM25 docstore persisted ===")
docstore_path = Path("./docstore")
check(
    "Docstore directory created after ingestion",
    docstore_path.exists(),
    f"path={docstore_path.resolve()}",
)

# The retriever log should show "Hybrid retriever active" not "vector-only"
# (check server logs manually or inspect via debug endpoint)
print("       → Check server logs for 'Hybrid retriever active' (not 'vector-only')")


# ── Test Bug 1: event loop not blocked during ingestion ───────────────────────

print("\n=== Bug 1: Event loop stays responsive during ingestion ===")

def health_probe(results_list: list):
    """Hammer the health endpoint while ingestion runs."""
    for _ in range(10):
        try:
            t0 = time.time()
            r = httpx.get(f"{API}/health", timeout=2)
            results_list.append(time.time() - t0)
        except Exception:
            results_list.append(999.0)
        time.sleep(0.5)

probe_results: list = []
probe_thread = threading.Thread(target=health_probe, args=(probe_results,))

# Trigger an ingestion and immediately start probing
medium_content = ("test content paragraph. " * 100 + "\n") * 20
httpx.post(
    f"{API}/api/documents/upload",
    files={"file": ("eventloop_test.txt", medium_content.encode(), "text/plain")},
    timeout=5,
)
probe_thread.start()
probe_thread.join()

if probe_results:
    max_latency = max(probe_results)
    check(
        "Health endpoint responds in <500ms while ingestion runs",
        max_latency < 0.5,
        f"max_health_latency={max_latency*1000:.0f}ms",
    )


# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'='*45}")
passed = sum(results)
total = len(results)
print(f"Results: {passed}/{total} passed")
if passed == total:
    print("\033[92mAll checks passed ✓\033[0m")
else:
    failed = [i + 1 for i, ok in enumerate(results) if not ok]
    print(f"\033[91mFailed checks: {failed}\033[0m")
    sys.exit(1)
