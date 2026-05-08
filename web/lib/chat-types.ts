export type ChatSummary = {
  id: string;
  title: string;
  owner?: string;
  created_at?: number;
  updated_at?: number;
};

export type Source = {
  type: "manual" | "ticket" | "script";
  index: string;
  module?: string | null;
  section?: string | null;
  referno?: string | null;
  score?: number;
};

export type ImageItem = {
  url: string;
  module?: string;
  section?: string;
  caption?: string;
  filename?: string;
};

export type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  images?: ImageItem[];
  context_used?: number;
  expanded_query?: string | null;
  pending?: boolean;
  tool_calls?: { name: string; input: unknown }[];
  error?: string;
};

export type ChatDoc = {
  id: string;
  title: string;
  owner?: string;
  created_at?: number;
  updated_at?: number;
  messages: Message[];
};

export type QueryRequest = {
  question: string;
  top_k?: number;
  include_images?: boolean;
  history?: { role: string; content: string }[];
  attached_files?: string[];
};
