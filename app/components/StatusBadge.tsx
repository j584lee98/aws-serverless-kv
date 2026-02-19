"use client";

import { DocFile } from "../types";

export function StatusBadge({
  status,
  error,
}: {
  status: DocFile["indexStatus"];
  error?: string;
}) {
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
    return (
      <span
        className="text-xs text-red-500 font-medium"
        title={error || "Processing failed"}
      >
        ✗ Error
      </span>
    );
  return <span className="text-xs text-gray-400">Pending</span>;
}
