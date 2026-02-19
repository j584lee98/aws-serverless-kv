"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { fetchAuthSession } from "aws-amplify/auth";
import { ChatMessage, Toast } from "../types";
import { MAX_QUERY_CHARS, DAILY_MSG_LIMIT } from "../lib/constants";
import { BotIcon, UserIcon, SendIcon } from "./icons";
import { ToastContainer } from "./ToastContainer";
import { KnowledgeVault } from "./KnowledgeVault";
import { SourcesPanel } from "./SourcesPanel";

export function ChatInterface({ onSignOut }: { onSignOut: () => void }) {
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

  const apiUrl    = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
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
        if (res.status === 429 || res.status === 401 || res.status === 400) {
          setMessages((prev) => [...prev, { role: "assistant", content: `⚠️ ${errMsg}` }]);
          return;
        }
        throw new Error(errMsg);
      }

      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.reply, sources: data.sources },
      ]);
    } catch (err) {
      console.error(err);
      addToast(err instanceof Error ? err.message : String(err));
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Sorry, I couldn't connect to the backend right now. Please try again." },
      ]);
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
            <div>
              <h1 className="text-lg font-bold text-gray-800">Knowledge Vault</h1>
              <p className="text-xs text-gray-400">{DAILY_MSG_LIMIT} messages / day</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={onSignOut}
              className="text-sm text-gray-600 hover:text-gray-900 px-3 py-1.5 border border-gray-300 rounded-lg hover:bg-gray-200 transition-colors"
            >
              Sign out
            </button>
          </div>
        </header>

        {/* Messages */}
        <div
          className={`flex-1 p-4 sm:p-6 space-y-5 ${
            messages.length === 0 ? "overflow-hidden" : "overflow-y-auto"
          }`}
        >
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center opacity-50 gap-3">
              <div className="w-20 h-20 bg-gray-200 rounded-full flex items-center justify-center">
                <BotIcon />
              </div>
              <p className="text-lg font-medium text-gray-500">How can I help you today?</p>
              <p className="text-sm text-gray-400">
                Upload documents in the sidebar for context-aware answers.
              </p>
            </div>
          ) : (
            messages.map((msg, i) => (
              <div
                key={i}
                className={`flex w-full ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`flex max-w-[85%] sm:max-w-[75%] gap-3 ${
                    msg.role === "user" ? "flex-row-reverse" : "flex-row"
                  }`}
                >
                  <div
                    className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
                      msg.role === "user" ? "bg-indigo-600" : "bg-blue-600"
                    }`}
                  >
                    {msg.role === "user" ? <UserIcon /> : <BotIcon />}
                  </div>
                  <div
                    className={`p-4 rounded-2xl shadow-sm text-sm leading-relaxed ${
                      msg.role === "user"
                        ? "bg-indigo-600 text-white rounded-tr-none"
                        : "bg-white text-gray-800 border border-gray-100 rounded-tl-none"
                    }`}
                  >
                    <div
                      className={`prose prose-sm max-w-none ${
                        msg.role === "user" ? "prose-invert" : "prose-gray"
                      }`}
                    >
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
