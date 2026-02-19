export interface DocFile {
  name: string;
  size: number;
  lastModified: string;
  indexStatus: "processing" | "indexed" | "error" | "unknown";
  chunkCount?: number;
  lastIndexed?: string;
  indexError?: string;
}

export interface Source {
  filename: string;
  chunk_index: number;
  score: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

export interface Toast {
  id: number;
  type: "error" | "info";
  message: string;
}
