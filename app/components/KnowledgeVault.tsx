"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { fetchAuthSession } from "aws-amplify/auth";
import { DocFile, Toast } from "../types";
import { MAX_FILES, MAX_FILE_MB, ALLOWED_EXTENSIONS, ALLOWED_ACCEPT } from "../lib/constants";
import { FileIcon, TrashIcon, UploadIcon } from "./icons";
import { StatusBadge } from "./StatusBadge";

export function KnowledgeVault({
  onToast,
}: {
  onToast: (msg: string, type?: Toast["type"]) => void;
}) {
  const [files, setFiles]             = useState<DocFile[]>([]);
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
      const res = await fetch(
        `${baseUrl}/documents?filename=${encodeURIComponent(filename)}`,
        { method: "DELETE", headers: { Authorization: token } }
      );
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
        <p className="text-xs text-gray-400 mt-0.5">
          {files.length}/{MAX_FILES} files · max {MAX_FILE_MB} MB each
        </p>
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
          <div
            key={file.name}
            className="group relative flex items-start justify-between p-3 rounded-xl bg-white border border-gray-200 hover:border-blue-200 hover:shadow-sm transition-all"
          >
            <div className="flex flex-col min-w-0 pr-2 gap-0.5">
              <div className="flex items-center gap-1.5">
                <span className="text-blue-500"><FileIcon /></span>
                <span className="text-gray-700 text-sm font-medium truncate" title={file.name}>
                  {file.name}
                </span>
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
