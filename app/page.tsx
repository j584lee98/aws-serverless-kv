"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Amplify } from "aws-amplify";
import { Authenticator, useAuthenticator } from "@aws-amplify/ui-react";
import { fetchAuthSession } from "aws-amplify/auth";
import "@aws-amplify/ui-react/styles.css";

// Configure Amplify
Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: process.env.NEXT_PUBLIC_USER_POOL_ID || "",
      userPoolClientId: process.env.NEXT_PUBLIC_USER_POOL_CLIENT_ID || "",
    },
  },
});

// ─── Constants ────────────────────────────────────────────────────────────────
const MAX_FILES        = 5;
const MAX_FILE_MB      = 10;
const MAX_QUERY_CHARS  = 2000;
const DAILY_MSG_LIMIT  = 20;
const ALLOWED_EXTENSIONS = ["pdf", "docx", "txt", "csv", "md", "png", "jpg", "jpeg", "tiff"];
const ALLOWED_ACCEPT   = ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(",");

// ─── Types ────────────────────────────────────────────────────────────────────
interface DocFile {
  name: string;
  size: number;
  lastModified: string;
  indexStatus: "processing" | "indexed" | "error" | "unknown";
  chunkCount?: number;
  lastIndexed?: string;
  indexError?: string;
}

interface Source {
  filename: string;
  chunk_index: number;
  score: number;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

interface Toast {
  id: number;
  type: "error" | "info";
  message: string;
}

// ─── Icons ────────────────────────────────────────────────────────────────────
const BotIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-5 h-5 text-white">
    <path d="M12 8V4H8" /><rect width="16" height="12" x="4" y="8" rx="2" />
    <path d="M2 14h2" /><path d="M20 14h2" /><path d="M15 13v2" /><path d="M9 13v2" />
  </svg>
);

const UserIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-5 h-5 text-white">
    <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
  </svg>
);

const SendIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" />
  </svg>
);

const UploadIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
  </svg>
);

const TrashIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </svg>
);

const FileIcon = () => (
  <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
  </svg>
);

// ─── Toast notifications ──────────────────────────────────────────────────────
function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-start gap-3 max-w-sm px-4 py-3 rounded-xl shadow-lg pointer-events-auto text-sm
            ${t.type === "error" ? "bg-red-600 text-white" : "bg-gray-800 text-white"}`}
        >
          <span className="flex-1">{t.message}</span>
          <button onClick={() => onDismiss(t.id)} className="text-white/70 hover:text-white shrink-0">✕</button>
        </div>
      ))}
    </div>
  );
}

// ─── Indexing status badge ────────────────────────────────────────────────────
function StatusBadge({ status, error }: { status: DocFile["indexStatus"]; error?: string }) {
  if (status === "indexed")
    return <span className="text-xs text-green-600 font-medium">✓ Indexed</span>;
  if (status === "processing")
    return (
      <span className="flex items-center gap-1 text-xs text-amber-500 font-medium">
        <span className="w-2.5 h-2.5 border-2 border-amber-400 border-t-transparent rounded-full animate-spin inline-block" />
        Processing…
      </span>
    );
  if (status === "error")
    return <span className="text-xs text-red-500 font-medium" title={error || "Processing failed"}>✗ Error</span>;
  return <span className="text-xs text-gray-400">Pending</span>;
}

// ─── Knowledge Vault sidebar ──────────────────────────────────────────────────
function KnowledgeVault({ onToast }: { onToast: (msg: string, type?: Toast["type"]) => void }) {
  const [files, setFiles]       = useState<DocFile[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [pollingKey, setPollingKey]   = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const apiUrl  = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const baseUrl = apiUrl.replace(/\/chat$/, "");

  const fetchFiles = useCallback(async () => {
    try {
      const session = await fetchAuthSession();
      const token   = session.tokens?.idToken?.toString();
      if (!token) return;
      const res = await fetch(`${baseUrl}/documents`, { headers: { Authorization: token } });
      if (!res.ok) return;
      const data = await res.json();
      setFiles(data.files ?? []);
      return data.files as DocFile[];
    } catch (e) {
      console.error("fetchFiles:", e);
    }
  }, [baseUrl]);

  useEffect(() => { fetchFiles(); }, [fetchFiles]);

  // Poll until the new file reaches a terminal indexing state
  useEffect(() => {
    if (!pollingKey) return;
    let attempts = 0;
    const interval = setInterval(async () => {
      attempts++;
      const current = await fetchFiles();
      const target  = current?.find((f) => f.name === pollingKey);
      if (target?.indexStatus === "indexed") {
        onToast(`"${pollingKey}" indexed successfully (${target.chunkCount ?? 0} chunks).`, "info");
        setPollingKey(null);
        clearInterval(interval);
      } else if (target?.indexStatus === "error") {
        onToast(`Processing failed for "${pollingKey}": ${target.indexError || "unknown error"}.`);
        setPollingKey(null);
        clearInterval(interval);
      } else if (attempts >= 30) {
        onToast(`"${pollingKey}" is taking longer than expected. Check back later.`, "info");
        setPollingKey(null);
        clearInterval(interval);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [pollingKey, fetchFiles, onToast]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      onToast(`File type .${ext} is not supported. Allowed: ${ALLOWED_EXTENSIONS.join(", ")}`);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      onToast(`File exceeds the ${MAX_FILE_MB} MB size limit.`);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setIsUploading(true);
    try {
      const session = await fetchAuthSession();
      const token   = session.tokens?.idToken?.toString();
      if (!token) throw new Error("No session");

      const initRes = await fetch(`${baseUrl}/documents`, {
        method: "POST",
        headers: { Authorization: token, "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, fileType: file.type, fileSize: file.size }),
      });
      if (!initRes.ok) {
        const err = await initRes.json();
        throw new Error(err.error || "Upload initialisation failed");
      }
      const { uploadUrl } = await initRes.json();

      const uploadRes = await fetch(uploadUrl, {
        method: "PUT",
        headers: { "Content-Type": file.type },
        body: file,
      });
      if (!uploadRes.ok) throw new Error("Upload to S3 failed");

      onToast(`"${file.name}" uploaded — indexing started…`, "info");
      await fetchFiles();
      setPollingKey(file.name);
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDelete = async (filename: string) => {
    if (!confirm(`Delete "${filename}" and all its indexed data?`)) return;
    try {
      const session = await fetchAuthSession();
      const token   = session.tokens?.idToken?.toString();
      if (!token) return;
      const res = await fetch(`${baseUrl}/documents?filename=${encodeURIComponent(filename)}`, {
        method: "DELETE",
        headers: { Authorization: token },
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || "Delete failed");
      }
      await fetchFiles();
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const atLimit = files.length >= MAX_FILES;

  return (
    <div className="w-72 bg-white flex-shrink-0 flex flex-col h-full border-r border-gray-200">
      <div className="px-4 pt-4 pb-3 border-b border-gray-100">
        <h2 className="text-sm font-semibold text-gray-700">Knowledge Vault</h2>
        <p className="text-xs text-gray-400 mt-0.5">{files.length}/{MAX_FILES} files · max {MAX_FILE_MB} MB each</p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {files.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <span className="text-3xl mb-2">&#128193;</span>
            <p className="text-sm font-medium text-gray-500">No documents yet</p>
            <p className="text-xs text-gray-400 mt-1">Upload files to add context to your chats</p>
          </div>
        )}
        {files.map((file) => (
          <div key={file.name} className="group relative flex items-start justify-between p-3 rounded-xl bg-white border border-gray-200 hover:border-blue-200 hover:shadow-sm transition-all">
            <div className="flex flex-col min-w-0 pr-2 gap-0.5">
              <div className="flex items-center gap-1.5">
                <span className="text-blue-500"><FileIcon /></span>
                <span className="text-gray-700 text-sm font-medium truncate" title={file.name}>{file.name}</span>
              </div>
              <div className="flex items-center gap-2 pl-5">
                <span className="text-gray-400 text-xs">{(file.size / 1024).toFixed(1)} KB</span>
                <span className="text-gray-300">·</span>
                <StatusBadge status={file.indexStatus} error={file.indexError} />
              </div>
            </div>
            <button
              onClick={() => handleDelete(file.name)}
              className="text-gray-300 hover:text-red-500 hover:bg-red-50 p-1.5 rounded-lg opacity-0 group-hover:opacity-100 transition-all mt-0.5 shrink-0"
              title="Delete file"
            >
              <TrashIcon />
            </button>
          </div>
        ))}
      </div>

      <div className="p-3 border-t border-gray-100">
        <input
          type="file"
          ref={fileInputRef}
          className="hidden"
          accept={ALLOWED_ACCEPT}
          onChange={handleUpload}
          disabled={isUploading || atLimit}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading || atLimit}
          className="w-full flex items-center justify-center gap-2 bg-white hover:bg-gray-50 text-gray-700 border border-gray-300 hover:border-blue-400 hover:text-blue-600 rounded-xl py-2.5 px-4 text-sm font-semibold transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isUploading ? (
            <span className="flex items-center gap-2">
              <span className="w-3.5 h-3.5 border-2 border-gray-400 border-t-blue-600 rounded-full animate-spin" />
              Uploading…
            </span>
          ) : atLimit ? (
            <span>Limit reached ({MAX_FILES}/{MAX_FILES})</span>
          ) : (
            <><UploadIcon /><span>Upload Document</span></>
          )}
        </button>
        <p className="text-xs text-gray-400 text-center mt-2">
          {ALLOWED_EXTENSIONS.join(", ")}
        </p>
      </div>
    </div>
  );
}

// ─── Sources panel (collapsed by default under assistant messages) ─────────────
function SourcesPanel({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (!sources || sources.length === 0) return null;
  return (
    <div className="mt-2 border-t border-gray-100 pt-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-xs text-blue-500 hover:text-blue-700 transition-colors"
      >
        <svg className={`w-3 h-3 transition-transform ${open ? "rotate-90" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        {sources.length} source{sources.length > 1 ? "s" : ""} referenced
      </button>
      {open && (
        <div className="mt-1.5 space-y-1">
          {sources.map((s, i) => (
            <div key={i} className="flex items-center gap-2 text-xs text-gray-500 bg-gray-50 rounded-lg px-2 py-1.5">
              <svg className="w-3 h-3 text-blue-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <span className="font-medium truncate">{s.filename}</span>
              <span className="text-gray-400 shrink-0">chunk {s.chunk_index} · {Math.round(s.score * 100)}% match</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Main chat interface ──────────────────────────────────────────────────────
function ChatInterface() {
  const { signOut } = useAuthenticator((ctx) => [ctx.user]);
  const [messages,  setMessages]  = useState<ChatMessage[]>([]);
  const [input,     setInput]     = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [toasts,    setToasts]    = useState<Toast[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const toastId        = useRef(0);

  const addToast = useCallback((message: string, type: Toast["type"] = "error") => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, message, type }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);

  const dismissToast = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  const apiUrl   = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const charsLeft = MAX_QUERY_CHARS - input.length;
  const nearLimit = charsLeft <= 200;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || trimmed.length > MAX_QUERY_CHARS) return;

    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setInput("");
    setIsLoading(true);

    try {
      const session = await fetchAuthSession();
      const token   = session.tokens?.idToken?.toString();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = token;

      const res = await fetch(apiUrl, {
        method: "POST",
        headers,
        body: JSON.stringify({ message: trimmed }),
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        const errMsg  = errBody.error || "Request failed";
        // Surface quota/auth errors inline as assistant messages
        if (res.status === 429 || res.status === 401 || res.status === 400) {
          setMessages((prev) => [...prev, { role: "assistant", content: `⚠️ ${errMsg}` }]);
          return;
        }
        throw new Error(errMsg);
      }

      const data = await res.json();
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply, sources: data.sources }]);
    } catch (err) {
      console.error(err);
      addToast(err instanceof Error ? err.message : String(err));
      setMessages((prev) => [...prev, {
        role: "assistant",
        content: "Sorry, I couldn't connect to the backend right now. Please try again.",
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-screen bg-gray-100 w-full overflow-hidden">
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      <KnowledgeVault onToast={addToast} />

      <div className="flex-1 flex flex-col h-full min-w-0">
        {/* Header */}
        <header className="bg-gray-100 px-6 py-3 flex items-center justify-between border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="bg-blue-600 p-2 rounded-lg shadow-sm">
              <BotIcon />
            </div>
            <h1 className="text-lg font-bold text-gray-800">Knowledge Vault AI</h1>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-xs text-gray-400 hidden sm:block">{DAILY_MSG_LIMIT} messages / day</span>
            <button
              onClick={signOut}
              className="text-sm text-gray-600 hover:text-gray-900 px-3 py-1.5 border border-gray-300 rounded-lg hover:bg-gray-200 transition-colors"
            >
              Sign out
            </button>
          </div>
        </header>

        {/* Messages */}
        <div className={`flex-1 p-4 sm:p-6 space-y-5 ${messages.length === 0 ? "overflow-hidden" : "overflow-y-auto"}`}>
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center opacity-50 gap-3">
              <div className="w-20 h-20 bg-gray-200 rounded-full flex items-center justify-center">
                <BotIcon />
              </div>
              <p className="text-lg font-medium text-gray-500">How can I help you today?</p>
              <p className="text-sm text-gray-400">Upload documents in the sidebar for context-aware answers.</p>
            </div>
          ) : (
            messages.map((msg, i) => (
              <div key={i} className={`flex w-full ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`flex max-w-[85%] sm:max-w-[75%] gap-3 ${msg.role === "user" ? "flex-row-reverse" : "flex-row"}`}>
                  <div className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${msg.role === "user" ? "bg-indigo-600" : "bg-blue-600"}`}>
                    {msg.role === "user" ? <UserIcon /> : <BotIcon />}
                  </div>
                  <div className={`p-4 rounded-2xl shadow-sm text-sm leading-relaxed ${
                    msg.role === "user"
                      ? "bg-indigo-600 text-white rounded-tr-none"
                      : "bg-white text-gray-800 border border-gray-100 rounded-tl-none"
                  }`}>
                    <div className={`prose prose-sm max-w-none ${
                      msg.role === "user"
                        ? "prose-invert"
                        : "prose-gray"
                    }`}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                    {msg.role === "assistant" && msg.sources && (
                      <SourcesPanel sources={msg.sources} />
                    )}
                  </div>
                </div>
              </div>
            ))
          )}

          {isLoading && (
            <div className="flex w-full justify-start">
              <div className="flex max-w-[85%] gap-3">
                <div className="shrink-0 w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center">
                  <BotIcon />
                </div>
                <div className="bg-white border border-gray-100 p-4 rounded-2xl rounded-tl-none shadow-sm flex items-center gap-1">
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="bg-gray-100 p-4 sticky bottom-0 border-t border-gray-200">
          <div className="max-w-3xl mx-auto">
            <form onSubmit={handleSubmit} className="relative flex items-center">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value.slice(0, MAX_QUERY_CHARS))}
                placeholder="Message AI Assistant…"
                maxLength={MAX_QUERY_CHARS}
                className="w-full bg-white text-gray-900 border border-gray-300 rounded-full py-3 pl-5 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all shadow-sm text-sm"
                disabled={isLoading}
              />
              <button
                type="submit"
                disabled={isLoading || !input.trim()}
                className="absolute right-2 p-2 bg-blue-600 text-white rounded-full hover:bg-blue-700 disabled:opacity-50 transition-colors shadow-sm disabled:cursor-not-allowed"
              >
                <SendIcon />
              </button>
            </form>
            <div className="flex justify-between items-center mt-1.5 px-1">
              <p className="text-xs text-gray-400">AI can make mistakes. Verify important information.</p>
              {nearLimit && (
                <p className={`text-xs font-medium ${charsLeft <= 0 ? "text-red-500" : "text-amber-500"}`}>
                  {charsLeft} chars left
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Root ─────────────────────────────────────────────────────────────────────
export default function Home() {
  return (
    <div className="grid min-h-screen place-items-center bg-white">
      <Authenticator loginMechanisms={["email"]} signUpAttributes={["email"]}>
        <ChatInterface />
      </Authenticator>
    </div>
  );
}
