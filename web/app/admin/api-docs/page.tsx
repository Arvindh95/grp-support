"use client";

export default function ApiDocsPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">API Reference</h1>
        <p className="text-sm text-muted-foreground">
          Interactive OpenAPI documentation for the RAG-API, generated from the
          live service (FastAPI Swagger UI).
        </p>
      </div>

      <div
        className="border rounded-md overflow-hidden bg-white"
        style={{ height: "75vh" }}
      >
        <iframe
          src="/rag/docs"
          className="w-full h-full"
          title="RAG-API Swagger UI"
        />
      </div>

      <p className="text-xs text-muted-foreground">
        Open directly:{" "}
        <a
          className="underline"
          href="/rag/docs"
          target="_blank"
          rel="noreferrer"
        >
          /rag/docs
        </a>{" "}
        · raw spec:{" "}
        <a
          className="underline"
          href="/rag/openapi.json"
          target="_blank"
          rel="noreferrer"
        >
          /rag/openapi.json
        </a>
      </p>
    </div>
  );
}
