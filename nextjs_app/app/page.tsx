"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Citation {
  source_file: string;
  page_number: number | null;
  section_heading: string;
  score: number;
  text_preview: string;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  streaming?: boolean;
  isError?: boolean;   // true when content is a backend error message
}

interface Document {
  id: string;
  filename: string;
  source_type: string;
  status: "pending" | "processing" | "done" | "failed";
  chunk_count: number;
  file_size_bytes: number;
  ingested_at: string | null;
  error_message: string | null;
}

interface Toast {
  id: string;
  type: "success" | "error";
  message: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function uid() { return Math.random().toString(36).slice(2); }

function formatBytes(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 ** 2).toFixed(1)} MB`;
}

function statusBadge(status: Document["status"]) {
  const map = {
    done:       { cls: "badge-done",       label: "✓ done" },
    failed:     { cls: "badge-failed",     label: "✗ failed" },
    processing: { cls: "badge-processing", label: "⏳ processing" },
    pending:    { cls: "badge-pending",    label: "· pending" },
  };
  const { cls, label } = map[status] ?? map.pending;
  return <span className={`badge ${cls}`}>{label}</span>;
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [docs, setDocs] = useState<Document[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [expandedCitations, setExpandedCitations] = useState<Record<string, boolean>>({});

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);
  const fileInputRef   = useRef<HTMLInputElement>(null);

  // ── Load documents ─────────────────────────────────────────────────────────

  const loadDocs = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/documents`);
      if (res.ok) setDocs(await res.json());
    } catch { /* backend not ready */ }
  }, []);

  useEffect(() => { loadDocs(); }, [loadDocs]);

  // Poll for processing docs
  useEffect(() => {
    const processing = docs.some(d => d.status === "processing" || d.status === "pending");
    if (!processing) return;
    const t = setInterval(loadDocs, 3000);
    return () => clearInterval(t);
  }, [docs, loadDocs]);

  // ── Scroll to bottom ───────────────────────────────────────────────────────

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── Toasts ─────────────────────────────────────────────────────────────────

  const toast = (type: Toast["type"], message: string) => {
    const id = uid();
    setToasts(prev => [...prev, { id, type, message }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  };

  // ── File upload ────────────────────────────────────────────────────────────

  const uploadFile = async (file: File) => {
    setUploading(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`${API}/api/documents/upload`, { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "Upload failed");
      if (data.duplicate) {
        toast("success", `Already indexed: ${file.name}`);
      } else {
        toast("success", `Ingesting ${file.name}…`);
      }
      loadDocs();
    } catch (err: unknown) {
      toast("error", err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const handleFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach(uploadFile);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  // ── Delete document ────────────────────────────────────────────────────────

  const deleteDoc = async (id: string) => {
    await fetch(`${API}/api/documents/${id}`, { method: "DELETE" });
    loadDocs();
  };

  // ── Chat ───────────────────────────────────────────────────────────────────

  const sendMessage = async (query: string) => {
    if (!query.trim() || sending) return;
    const userMsg: Message = { id: uid(), role: "user", content: query };
    const aiMsg: Message   = { id: uid(), role: "assistant", content: "", streaming: true };

    setMessages(prev => [...prev, userMsg, aiMsg]);
    setInput("");
    setSending(true);
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    try {
      const res = await fetch(`${API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      if (!res.ok) {
        // Non-2xx response: parse the JSON error body and emit as error message
        let detail = `Server error ${res.status}`;
        try {
          const errBody = await res.json();
          detail = errBody.detail ?? detail;
        } catch { /* not JSON */ }
        setMessages(prev =>
          prev.map(m =>
            m.id === aiMsg.id
              ? { ...m, content: detail, streaming: false, isError: true }
              : m
          )
        );
        setSending(false);
        return;
      }

      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let citations: Citation[] = [];
      let fullAnswer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const raw = line.slice(5).trim();
          if (raw === "[DONE]") break;
          try {
            const evt = JSON.parse(raw);
            if (evt.type === "citations") {
              citations = evt.citations;
            } else if (evt.type === "token") {
              fullAnswer += evt.content;
              setMessages(prev =>
                prev.map(m =>
                  m.id === aiMsg.id ? { ...m, content: fullAnswer } : m
                )
              );
            } else if (evt.type === "error") {
                          fullAnswer = evt.message ?? "An error occurred.";
                          setMessages(prev =>
                            prev.map(m =>
                              m.id === aiMsg.id
                                ? { ...m, content: fullAnswer, streaming: false, isError: true }
                                : m
                            )
                          );
            }
          } catch { /* malformed event */ }
        }
      }

      setMessages(prev =>
        prev.map(m =>
          m.id === aiMsg.id
            ? { ...m, content: fullAnswer, citations, streaming: false }
            : m
        )
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setMessages(prev =>
        prev.map(m =>
          m.id === aiMsg.id
            ? { ...m, content: `⚠️ Error: ${msg}`, streaming: false }
            : m
        )
      );
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const autoResize = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  };

  const SUGGESTIONS = [
    "Summarize my documents",
    "What are the key findings?",
    "List all mentioned dates",
    "Who are the main people mentioned?",
  ];

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="app-shell">
      {/* ── Sidebar ───────────────────────────────────────────────────────── */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="sidebar-logo">
            <span>🧠</span>
            <span>Knowledge Assistant</span>
          </div>
        </div>

        <div className="sidebar-body">
          {/* Upload zone */}
          <div
            className={`upload-zone${dragOver ? " drag-over" : ""}`}
            onClick={() => fileInputRef.current?.click()}
            onDrop={handleDrop}
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
          >
            <div className="upload-icon">{uploading ? "⏳" : "📂"}</div>
            <div>{uploading ? "Uploading…" : "Drop files here or click to browse"}</div>
            <div className="upload-hint">PDF, DOCX, MD, TXT, HTML, EML</div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.doc,.md,.txt,.html,.htm,.eml"
              onChange={e => handleFiles(e.target.files)}
            />
          </div>

          {/* Document list */}
          <p className="section-title">Indexed Documents ({docs.length})</p>

          {docs.length === 0 && (
            <p style={{ fontSize: ".8rem", color: "var(--text-muted)", textAlign: "center", marginTop: 16 }}>
              No documents yet. Upload one above.
            </p>
          )}

          {docs.map(doc => (
            <div key={doc.id} className="doc-item">
              <div className="doc-info">
                <div className="doc-name" title={doc.filename}>{doc.filename}</div>
                <div className="doc-meta">
                  {statusBadge(doc.status)}
                  {doc.chunk_count > 0 && <span>{doc.chunk_count} chunks</span>}
                  <span>{formatBytes(doc.file_size_bytes ?? 0)}</span>
                </div>
                {doc.status === "processing" && (
                  <div className="progress-bar">
                    <div className="progress-fill progress-indeterminate" />
                  </div>
                )}
                {doc.status === "failed" && doc.error_message && (
                  <div style={{ fontSize: ".7rem", color: "var(--red)", marginTop: 2 }}>
                    {doc.error_message.slice(0, 60)}
                  </div>
                )}
              </div>
              <button
                className="btn btn-danger"
                onClick={() => deleteDoc(doc.id)}
                title="Delete document"
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* ── Main ──────────────────────────────────────────────────────────── */}
      <main className="main">
        <div className="chat-header">
          <div>
            <h1 style={{ fontSize: "1rem", fontWeight: 600 }}>Chat</h1>
            <p style={{ fontSize: ".78rem", color: "var(--text-muted)" }}>
              {docs.filter(d => d.status === "done").length} documents ready
            </p>
          </div>
          {messages.length > 0 && (
            <button
              className="btn btn-ghost"
              style={{ fontSize: ".8rem", padding: "6px 12px" }}
              onClick={() => setMessages([])}
            >
              Clear chat
            </button>
          )}
        </div>

        {/* Messages */}
        <div className="chat-messages">
          {messages.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">🧠</div>
              <div className="empty-title">Ask anything about your documents</div>
              <p style={{ fontSize: ".85rem" }}>
                Upload files in the sidebar, then ask questions below.
              </p>
              <div className="empty-chips">
                {SUGGESTIONS.map(s => (
                  <button key={s} className="empty-chip" onClick={() => sendMessage(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map(msg => (
              <div key={msg.id} className={`message ${msg.role}`}>
                <div className={`avatar ${msg.role === "user" ? "user-av" : "ai-av"}`}>
                  {msg.role === "user" ? "👤" : "🧠"}
                </div>
                <div>
                  <div className={`bubble${msg.isError ? " bubble-error" : ""}`}>
                    {msg.content
                      ? <>
                          {msg.content}
                          {msg.streaming && <span className="cursor" />}
                        </>
                      : msg.streaming
                        ? <span className="thinking-dots">
                            <span /><span /><span />
                          </span>
                        : null
                    }
                  </div>

                  {/* Citations */}
                  {!msg.streaming && msg.citations && msg.citations.length > 0 && (
                    <>
                      <button
                        className="citations-toggle"
                        onClick={() => setExpandedCitations(prev => ({ ...prev, [msg.id]: !prev[msg.id] }))}
                      >
                        📎 {msg.citations.length} source{msg.citations.length > 1 ? "s" : ""}
                        {expandedCitations[msg.id] ? " ▲" : " ▼"}
                      </button>
                      {expandedCitations[msg.id] && (
                        <div className="citations-panel">
                          {msg.citations.map((c, i) => (
                            <div key={i} className="citation-card">
                              <div className="cite-source">
                                {c.source_file} · p.{c.page_number ?? "?"} ·{" "}
                                <span style={{ color: "var(--text-muted)" }}>
                                  {Math.round(c.score * 100)}% match
                                </span>
                              </div>
                              {c.section_heading && (
                                <div style={{ fontStyle: "italic", color: "var(--text-muted)", fontSize: ".75rem" }}>
                                  §  {c.section_heading}
                                </div>
                              )}
                              <div className="cite-preview">{c.text_preview}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="chat-input-area">
          <div className="chat-input-row">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={autoResize}
              onKeyDown={handleKeyDown}
              placeholder="Ask anything about your documents…"
              rows={1}
              disabled={sending}
            />
            <button
              className="send-btn"
              onClick={() => sendMessage(input)}
              disabled={sending || !input.trim()}
              title="Send (Enter)"
            >
              ➤
            </button>
          </div>
          <p className="input-hint">Enter to send · Shift+Enter for new line</p>
        </div>
      </main>

      {/* Toasts */}
      <div className="toast-area">
        {toasts.map(t => (
          <div key={t.id} className={`toast toast-${t.type}`}>{t.message}</div>
        ))}
      </div>
    </div>
  );
}
