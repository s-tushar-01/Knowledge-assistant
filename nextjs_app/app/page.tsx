"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
  isError?: boolean;
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

interface GraphNode {
  id: string;
  label: string;
  detail: string;
  tone: "primary" | "muted" | "warning" | "error";
}

function uid() {
  return Math.random().toString(36).slice(2);
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function statusLabel(status: Document["status"]) {
  const map = {
    done: "Ready",
    failed: "Failed",
    processing: "Indexing",
    pending: "Queued",
  };
  return map[status] ?? "Queued";
}

function fileInitial(sourceType: string, filename: string) {
  const ext = sourceType || filename.split(".").pop() || "doc";
  return ext.slice(0, 3).toUpperCase();
}

function graphLabelFromFile(filename: string) {
  return filename
    .replace(/\.[^.]+$/, "")
    .replace(/[-_]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 3)
    .join(" ");
}

function graphLabelFromCitation(citation: Citation) {
  const section = citation.section_heading?.trim();
  if (section) return section.split(/\s+/).slice(0, 4).join(" ");
  return graphLabelFromFile(citation.source_file);
}

function formatDate(value: string | null) {
  if (!value) return "Recently added";
  try {
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
  } catch {
    return "Recently added";
  }
}

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
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadDocs = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/documents`);
      if (res.ok) setDocs(await res.json());
    } catch {
      /* backend may not be running yet */
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadDocs(); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadDocs]);

  useEffect(() => {
    const processing = docs.some(d => d.status === "processing" || d.status === "pending");
    if (!processing) return;
    const timer = window.setInterval(loadDocs, 3000);
    return () => window.clearInterval(timer);
  }, [docs, loadDocs]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const readyCount = docs.filter(d => d.status === "done").length;
  const processingCount = docs.filter(d => d.status === "processing" || d.status === "pending").length;
  const totalBytes = docs.reduce((sum, doc) => sum + (doc.file_size_bytes ?? 0), 0);

  const lastCitations = useMemo(() => {
    return [...messages].reverse().find(m => m.role === "assistant" && m.citations?.length)?.citations ?? [];
  }, [messages]);

  const sourceItems = lastCitations.length
    ? lastCitations
    : docs.slice(0, 4).map((doc): Citation => ({
        source_file: doc.filename,
        page_number: null,
        section_heading: statusLabel(doc.status),
        score: doc.status === "done" ? 1 : 0.25,
        text_preview: doc.status === "done"
          ? `${doc.chunk_count || 0} chunks indexed and ready for retrieval.`
          : doc.error_message || "Waiting for indexing to finish.",
      }));

  const graphData = useMemo(() => {
    if (lastCitations.length) {
      const seen = new Set<string>();
      const nodes = lastCitations.reduce<GraphNode[]>((items, citation, index) => {
        const label = graphLabelFromCitation(citation);
        const key = label.toLowerCase();
        if (!label || seen.has(key)) return items;
        seen.add(key);
        items.push({
          id: `${citation.source_file}-${index}`,
          label,
          detail: citation.page_number ? `Page ${citation.page_number}` : citation.source_file,
          tone: index === 0 ? "primary" : "muted",
        });
        return items;
      }, []);

      return {
        center: "Cited answer",
        context: "Latest response",
        nodes: nodes.slice(0, 5),
      };
    }

    const nodes = docs.slice(0, 5).map((doc): GraphNode => ({
      id: doc.id,
      label: graphLabelFromFile(doc.filename) || doc.source_type.toUpperCase() || "Document",
      detail: `${statusLabel(doc.status)} - ${doc.chunk_count || 0} chunks`,
      tone: doc.status === "done"
        ? "primary"
        : doc.status === "failed"
          ? "error"
          : "warning",
    }));

    return {
      center: docs.length ? `${docs.length} document${docs.length === 1 ? "" : "s"}` : "No data",
      context: docs.length ? "Indexed library" : "Upload files",
      nodes,
    };
  }, [docs, lastCitations]);

  const toast = (type: Toast["type"], message: string) => {
    const id = uid();
    setToasts(prev => [...prev, { id, type, message }]);
    window.setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  };

  const uploadFile = async (file: File) => {
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch(`${API}/api/documents/upload`, { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "Upload failed");
      toast("success", data.duplicate ? `Already indexed: ${file.name}` : `Indexing ${file.name}`);
      void loadDocs();
    } catch (err: unknown) {
      toast("error", err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const handleFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach(file => { void uploadFile(file); });
  };

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    handleFiles(event.dataTransfer.files);
  };

  const deleteDoc = async (id: string) => {
    await fetch(`${API}/api/documents/${id}`, { method: "DELETE" });
    void loadDocs();
  };

  const sendMessage = async (query: string) => {
    if (!query.trim() || sending) return;
    if (readyCount === 0) {
      toast("error", processingCount > 0 ? "Wait until your document finishes indexing." : "Upload a document before asking for insights.");
      return;
    }
    const userMsg: Message = { id: uid(), role: "user", content: query };
    const aiMsg: Message = { id: uid(), role: "assistant", content: "", streaming: true };

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
        let detail = `Server error ${res.status}`;
        try {
          const errBody = await res.json();
          detail = errBody.detail ?? detail;
        } catch {
          /* not JSON */
        }
        setMessages(prev => prev.map(m => (
          m.id === aiMsg.id ? { ...m, content: detail, streaming: false, isError: true } : m
        )));
        setSending(false);
        return;
      }

      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let citations: Citation[] = [];

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
              const token = evt.content ?? "";
              setMessages(prev => prev.map(m => (
                m.id === aiMsg.id ? { ...m, content: `${m.content}${token}` } : m
              )));
            } else if (evt.type === "error") {
              const message = evt.message ?? "An error occurred.";
              setMessages(prev => prev.map(m => (
                m.id === aiMsg.id ? { ...m, content: message, streaming: false, isError: true } : m
              )));
            }
          } catch {
            /* malformed SSE event */
          }
        }
      }

      setMessages(prev => prev.map(m => (
        m.id === aiMsg.id ? { ...m, citations, streaming: false } : m
      )));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setMessages(prev => prev.map(m => (
        m.id === aiMsg.id ? { ...m, content: `Error: ${msg}`, streaming: false, isError: true } : m
      )));
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage(input);
    }
  };

  const autoResize = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(event.target.value);
    const el = event.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  };

  const suggestions = [
    "Summarize my documents",
    "What are the key risks?",
    "List decisions and dates",
    "Show supporting evidence",
  ];

  return (
    <div className="app-shell">
      <aside className="library-panel">
        <header className="brand-row">
          <button className="icon-btn" aria-label="Menu">
            <span />
            <span />
            <span />
          </button>
          <div>
            <p className="eyebrow">Personal RAG</p>
            <h1>Knowledge Assistant</h1>
          </div>
          <button className="icon-btn compose-btn" aria-label="New chat">+</button>
        </header>

        <section
          className={`upload-zone${dragOver ? " drag-over" : ""}`}
          onClick={() => fileInputRef.current?.click()}
          onDrop={handleDrop}
          onDragOver={event => { event.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
        >
          <div className="upload-mark" aria-hidden="true">UP</div>
          <h2>{uploading ? "Uploading files" : "Upload documents"}</h2>
          <p>Drag and drop files here or click to browse.</p>
          <span>PDF, DOCX, TXT, MD</span>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md"
            onChange={event => handleFiles(event.target.files)}
          />
        </section>

        <section className="summary-tile">
          <div className="status-ring">OK</div>
          <div>
            <strong>{readyCount} documents ready</strong>
            <p>{processingCount ? `${processingCount} still processing` : "All indexed files are ready"}</p>
          </div>
          <div className="summary-bar"><span style={{ width: docs.length ? `${Math.max(18, (readyCount / docs.length) * 100)}%` : "0%" }} /></div>
        </section>

        <section className="document-section">
          <div className="section-head">
            <p>Document Library</p>
            <button className="mini-btn" onClick={() => void loadDocs()}>Refresh</button>
          </div>

          <label className="search-box">
            <span>Search</span>
            <input placeholder="Search documents" disabled />
          </label>

          <div className="document-list">
            {docs.length === 0 ? (
              <div className="empty-library">
                <strong>No documents yet</strong>
                <p>Upload a file above to start building your private knowledge base.</p>
              </div>
            ) : docs.map(doc => (
              <article key={doc.id} className={`doc-item ${doc.status}`}>
                <div className="file-chip">{fileInitial(doc.source_type, doc.filename)}</div>
                <div className="doc-info">
                  <strong title={doc.filename}>{doc.filename}</strong>
                  <span>{formatDate(doc.ingested_at)} - {formatBytes(doc.file_size_bytes ?? 0)}</span>
                  {(doc.status === "processing" || doc.status === "pending") && (
                    <div className="mini-progress"><span /></div>
                  )}
                  {doc.status === "failed" && doc.error_message && (
                    <em>{doc.error_message.slice(0, 72)}</em>
                  )}
                </div>
                <div className="doc-actions">
                  <span className={`badge ${doc.status}`}>{statusLabel(doc.status)}</span>
                  <button className="delete-btn" onClick={() => void deleteDoc(doc.id)} title="Remove document">Remove</button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <footer className="storage-strip">
          <span>Storage</span>
          <div className="storage-bar"><span style={{ width: `${Math.min(100, (totalBytes / (10 * 1024 ** 3)) * 100)}%` }} /></div>
          <span>{formatBytes(totalBytes)}</span>
        </footer>
      </aside>

      <main className="chat-panel">
        <header className="chat-topbar">
          <div>
            <p className="eyebrow">Ask your knowledge base</p>
            <h2>Grounded answers with citations</h2>
          </div>
          <div className="topbar-actions">
            <button className="filter-btn">All documents</button>
            {messages.length > 0 && <button className="filter-btn" onClick={() => setMessages([])}>Clear chat</button>}
          </div>
        </header>

        <section className="chat-thread">
          {messages.length === 0 ? (
            <div className="welcome-state">
              <div className="assistant-mark">AI</div>
              <h3>Ask anything about your documents</h3>
              <p>{processingCount > 0 ? "Your document is being indexed. Questions will unlock when it is ready." : "Upload files to ask for summaries, decisions, risks, dates, or exact evidence from the sources."}</p>
              {readyCount > 0 && (
                <div className="suggestion-row">
                  {suggestions.map(suggestion => (
                    <button key={suggestion} onClick={() => void sendMessage(suggestion)}>{suggestion}</button>
                  ))}
                </div>
              )}
            </div>
          ) : messages.map(msg => (
            <article key={msg.id} className={`message ${msg.role}`}>
              <div className="avatar">{msg.role === "user" ? "You" : "AI"}</div>
              <div className="message-body">
                {msg.role === "assistant" && <div className="answer-label">Cited answer</div>}
                <div className={`bubble${msg.isError ? " error" : ""}`}>
                  {msg.content ? (
                    <>
                      {msg.content}
                      {msg.streaming && <span className="cursor" />}
                    </>
                  ) : msg.streaming ? (
                    <span className="thinking-dots"><span /><span /><span /></span>
                  ) : null}
                </div>

                {!msg.streaming && msg.citations && msg.citations.length > 0 && (
                  <div className="citation-block">
                    <button
                      className="citations-toggle"
                      onClick={() => setExpandedCitations(prev => ({ ...prev, [msg.id]: !prev[msg.id] }))}
                    >
                      {msg.citations.length} source{msg.citations.length > 1 ? "s" : ""} used
                    </button>
                    {expandedCitations[msg.id] && (
                      <div className="inline-citations">
                        {msg.citations.map((citation, index) => (
                          <div key={`${citation.source_file}-${index}`} className="inline-citation">
                            <strong>{index + 1}. {citation.source_file}</strong>
                            <span>{citation.page_number ? `Page ${citation.page_number}` : "Indexed source"} - {Math.round((citation.score || 0) * 100)}% match</span>
                            <p>{citation.text_preview}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </article>
          ))}
          <div ref={messagesEndRef} />
        </section>

        <section className="composer-area">
          {readyCount > 0 && (
            <div className="quick-actions">
              {suggestions.slice(1).map(suggestion => (
                <button key={suggestion} onClick={() => void sendMessage(suggestion)}>{suggestion}</button>
              ))}
            </div>
          )}
          <div className="composer">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={autoResize}
              onKeyDown={handleKeyDown}
              placeholder={readyCount > 0 ? "Ask a follow-up question..." : "Upload and index a document first..."}
              rows={1}
              disabled={sending || readyCount === 0}
            />
            <button className="send-btn" onClick={() => void sendMessage(input)} disabled={sending || !input.trim() || readyCount === 0}>
              Send
            </button>
          </div>
          <p className="input-hint">Enter to send. Shift+Enter for a new line.</p>
        </section>
      </main>

      <aside className="source-panel">
        <header className="source-header">
          <div>
            <p className="eyebrow">Sources</p>
            <h2>{sourceItems.length || 0} references</h2>
          </div>
          <button className="icon-btn" aria-label="Filter sources">
            <span />
            <span />
            <span />
          </button>
        </header>

        <section className="source-list">
          {sourceItems.length === 0 ? (
            <div className="empty-sources">
              <strong>No sources yet</strong>
              <p>Citations will appear here after the assistant answers.</p>
            </div>
          ) : sourceItems.map((source, index) => (
            <article key={`${source.source_file}-${index}`} className="source-card">
              <div className="source-index">{index + 1}</div>
              <div className="source-content">
                <div className="source-title-row">
                  <strong>{source.source_file}</strong>
                  <span>{Math.round((source.score || 0) * 100)}%</span>
                </div>
                <p className="source-meta">{source.page_number ? `Page ${source.page_number}` : source.section_heading || "Indexed document"}</p>
                <p>{source.text_preview || "Source preview will appear after retrieval."}</p>
              </div>
            </article>
          ))}
        </section>

        <section className="graph-panel">
          <div className="section-head">
            <p>Knowledge Graph</p>
            <button className="mini-btn">{graphData.context}</button>
          </div>
          {graphData.nodes.length === 0 ? (
            <div className="graph-empty">
              <strong>No graph yet</strong>
              <p>Upload documents or ask a cited question to generate source relationships.</p>
            </div>
          ) : (
            <div className="graph">
              {graphData.nodes.length > 1 && (
                <>
                  <span className="line vertical" />
                  <span className="line horizontal" />
                  <span className="line diagonal-a" />
                  <span className="line diagonal-b" />
                </>
              )}
              <div className="node node-center primary">
                <strong>{graphData.center}</strong>
                <span>{graphData.context}</span>
              </div>
              {graphData.nodes.map((node, index) => (
                <div key={node.id} className={`node node-${index} ${node.tone}`} title={node.detail}>
                  <strong>{node.label}</strong>
                  <span>{node.detail}</span>
                </div>
              ))}
            </div>
          )}
          <div className="legend">
            <span><i className="dot primary" /> Key themes</span>
            <span><i className="dot muted" /> Sources</span>
            <span><i className="dot warning" /> Processing</span>
          </div>
        </section>
      </aside>

      <div className="toast-area">
        {toasts.map(item => <div key={item.id} className={`toast ${item.type}`}>{item.message}</div>)}
      </div>
    </div>
  );
}
