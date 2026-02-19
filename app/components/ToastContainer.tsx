"use client";

import { Toast } from "../types";

export function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-start gap-3 max-w-sm px-4 py-3 rounded-xl shadow-lg pointer-events-auto text-sm
            ${t.type === "error" ? "bg-red-600 text-white" : "bg-gray-800 text-white"}`}
        >
          <span className="flex-1">{t.message}</span>
          <button onClick={() => onDismiss(t.id)} className="text-white/70 hover:text-white shrink-0">
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
