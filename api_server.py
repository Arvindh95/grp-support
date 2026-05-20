"""
GRP Support AI — FastAPI Backend (v3 — Claude-as-Agent + Upload)
Claude is the primary search agent via MCP Elasticsearch tools.
Python provides kNN semantic seed; Claude drives all further retrieval.
New: image upload, document upload (manual/rfs/script/code) endpoints.

Run: uvicorn api_server:app --host 0.0.0.0 --port 8001
"""

import re, json, requests, urllib3, uuid, os, io, csv
from urllib.parse import quote as _url_quote
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess
from collections import defaultdict

urllib3.disable_warnings()

# ── Config ─────────────────────────────────────────────────────────────────────
ES_URL      = os.environ.get("ES_URL",      "https://localhost:9200")
ES_AUTH     = (os.environ.get("ES_USER", "elastic"), os.environ["ES_PASSWORD"])
OLLAMA_URL  = os.environ.get("OLLAMA_URL",  "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
CLAUDE_BIN  = os.environ.get("CLAUDE_BIN",  "/home/claudeuser/.local/bin/claude")  # legacy; unused after SDK migration
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
JWT_SECRET  = os.environ["JWT_SECRET"]
JWT_ALG     = "HS256"
IMG_SIGNING_KEY = os.environ.get("IMG_SIGNING_KEY", "").encode() or None
JWT_TTL_HOURS = int(os.environ.get("JWT_TTL_HOURS", "12"))
IMG_BASE    = os.environ.get("IMG_BASE",    "http://173.212.247.3:8080")
IMG_DIR     = os.environ.get("IMG_DIR",     "/opt/grp-manuals/Doc-Images")
# Public base used to render signed image URLs back to the frontend. nginx
# is expected to proxy this to the FastAPI /images/ route. Falls back to IMG_BASE.
IMG_PUBLIC_BASE = os.environ.get("IMG_PUBLIC_BASE", IMG_BASE).rstrip("/")
# Signed-URL TTL in seconds (default = JWT TTL).
IMG_SIGN_TTL    = int(os.environ.get("IMG_SIGN_TTL", str(JWT_TTL_HOURS * 3600)))

# Cost / budget config — defaults to Sonnet 4.6 list pricing per 1M tokens (USD).
COST_INPUT_PER_M  = float(os.environ.get("COST_INPUT_PER_M",  "3.00"))
COST_OUTPUT_PER_M = float(os.environ.get("COST_OUTPUT_PER_M", "15.00"))
COST_CACHE_PER_M  = float(os.environ.get("COST_CACHE_PER_M",  "0.30"))
# Hard cap on monthly tokens (input+output, billed). 0 = unlimited.
MONTHLY_TOKEN_BUDGET = int(os.environ.get("MONTHLY_TOKEN_BUDGET", "0"))

# External alerting + email
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@grp-support.local").strip()
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://173.212.247.3:8081").rstrip("/")

# Index for service / integration API keys
API_KEYS_INDEX = "grp-api-keys"
RESET_TOKENS_INDEX = "grp-reset-tokens"
# Index for admin-set runtime settings (e.g. the Claude API key)
SETTINGS_INDEX = "grp-settings"

CHAT_UPLOAD_DIR = os.environ.get("CHAT_UPLOAD_DIR", "/tmp/grp-chat")
os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
# Only text-extractable types: the chat reads attachments as UTF-8 text
# (.docx is converted to Markdown on upload). PDFs/images are NOT accepted —
# decoding their bytes as text yields garbage the model cannot use.
CHAT_UPLOAD_EXTS = {".md", ".txt", ".docx", ".csv"}
CHAT_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
DOCUMENT_UPLOAD_MAX_BYTES = int(os.environ.get("DOCUMENT_UPLOAD_MAX_BYTES",
                                                str(30 * 1024 * 1024)))
# Per-doc-type allowed extensions for /upload-document.
DOCUMENT_UPLOAD_EXTS = {
    "manual":    {".docx", ".md"},
    "rfs":       {".xlsx", ".xls", ".csv"},
    "script":    {".txt", ".sql"},
    "code":      {".py", ".cs", ".sql"},
    "acumatica": {".docx", ".md"},
}

RFS_INDICES = [
    "rfs-tickets-jan-2025",
    "rfs-tickets-feb-2025",
    "rfs-tickets-mar-2025",
]
SCRIPTS_INDEX   = "grp-scripts"
CODE_INDEX      = "grp-code"
ACUMATICA_INDEX = "acumatica-help"
CHATS_INDEX     = "grp-chats"
USERS_INDEX   = "grp-users"
AUDIT_INDEX   = "grp-audit"

# Per-user query rate limit (soft cap; in-memory per worker, so effective cap = N_WORKERS x this)
QUERY_RATE_LIMIT_PER_MIN = int(os.environ.get("QUERY_RATE_LIMIT_PER_MIN", "30"))

MONTH_INDEX_MAP = {
    1: "rfs-tickets-jan-2025", 2: "rfs-tickets-feb-2025",
    3: "rfs-tickets-mar-2025", 4: "rfs-tickets-apr-2025",
    5: "rfs-tickets-may-2025", 6: "rfs-tickets-jun-2025",
    7: "rfs-tickets-jul-2025", 8: "rfs-tickets-aug-2025",
    9: "rfs-tickets-sep-2025", 10: "rfs-tickets-oct-2025",
    11: "rfs-tickets-nov-2025", 12: "rfs-tickets-dec-2025",
}

# ── ES Mappings ────────────────────────────────────────────────────────────────
CODE_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "script_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "filename":    {"type": "keyword"},
            "source_file": {"type": "keyword"},
            "purpose":     {"type": "text"},
            "content":     {"type": "text"},
            "tables":      {"type": "keyword"},
            "language":    {"type": "keyword"},
            "embedding":   {"type": "dense_vector", "dims": 1024, "index": True, "similarity": "cosine"}
        }
    }
}

CHATS_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "title":      {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "owner":      {"type": "keyword"},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
            "messages":   {"type": "object", "enabled": False}
        }
    }
}

USERS_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "email":         {"type": "keyword"},
            "password_hash": {"type": "keyword", "index": False},
            "name":          {"type": "text"},
            "role":          {"type": "keyword"},
            "created_at":    {"type": "long"},
        }
    }
}

AUDIT_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "ts":             {"type": "date"},
            "user":           {"type": "keyword"},
            "event":          {"type": "keyword"},
            "question":       {"type": "text"},
            "model":          {"type": "keyword"},
            "latency_ms":     {"type": "long"},
            "tool_calls":     {"type": "integer"},
            "input_tokens":   {"type": "long"},
            "output_tokens":  {"type": "long"},
            "cached_tokens":  {"type": "long"},
            "answer_chars":   {"type": "long"},
            "status":         {"type": "keyword"},
            "error":          {"type": "text"},
        }
    }
}

API_KEYS_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "key_hash":   {"type": "keyword", "index": True},
            "name":       {"type": "text"},
            "owner":      {"type": "keyword"},
            "role":       {"type": "keyword"},
            "created_at": {"type": "long"},
            "last_used":  {"type": "long"},
            "revoked":    {"type": "boolean"},
        }
    }
}

RESET_TOKENS_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "token_hash": {"type": "keyword", "index": True},
            "email":      {"type": "keyword"},
            "expires_at": {"type": "long"},
            "used":       {"type": "boolean"},
        }
    }
}

RFS_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "lodge_id":      {"type": "keyword"},
            "referno":       {"type": "keyword"},
            "timestamp":     {"type": "date", "ignore_malformed": True},
            "relatedarea":   {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "priority":      {"type": "integer"},
            "dateline":      {"type": "date", "ignore_malformed": True},
            "notes":         {"type": "text"},
            "laststatus":    {"type": "integer"},
            "lastassignee":  {"type": "keyword"},
            "actions":       {"type": "nested"},
            "action_summary":{"type": "text"},
            "source_file":   {"type": "keyword"},
            "embedding":     {"type": "dense_vector", "dims": 1024, "index": True, "similarity": "cosine"}
        }
    }
}

# Acumatica help index uses same shape as grp-manuals (heading-chunked sections
# with embedded screenshots and screen codes like AP301000).
ACUMATICA_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "module":          {"type": "keyword"},
            "section":         {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "subsection":      {"type": "text"},
            "content":         {"type": "text", "analyzer": "standard"},
            "screen_codes":    {"type": "keyword"},
            "images":          {"type": "keyword"},
            "image_captions":  {"type": "text"},
            "chunk_index":     {"type": "integer"},
            "total_chunks":    {"type": "integer"},
            "prev_section":    {"type": "keyword"},
            "next_section":    {"type": "keyword"},
            "prev_tail":       {"type": "text"},
            "source_file":     {"type": "keyword"},
            "embedding":       {"type": "dense_vector", "dims": 1024, "index": True, "similarity": "cosine"}
        }
    }
}

# ── Agent System Prompt ─────────────────────────────────────────────────────────
AGENT_SYSTEM_PROMPT = """You are GRP ERP Support AI — a search-first support agent for CENSOF internal support engineers (Century Software, Malaysia).
You are the PRIMARY search agent. You drive all retrieval via Elasticsearch search tools. Do not rely solely on initial context — always search.

=== STEP 0 — CLARIFY IF VAGUE ===
Before searching, judge if the question is too vague to give a useful answer.
A question is TOO VAGUE if it is:
- A module name alone: "accounts payable", "payroll", "fixed asset"
- A screen code alone: "AP301000"
- A single word with no action: "vendor", "payment", "report"
- Ambiguous between procedure vs problem: "payroll issue"

If TOO VAGUE, respond ONLY with a clarification question. Do NOT search. Do NOT output a ```sources block.
Format:
CLARIFY: [one concise question asking what specifically they need]

Examples:
  Input: "accounts payable" → CLARIFY: What do you need help with in Accounts Payable? (e.g. vendor registration, payment processing, AP reports, a specific error)
  Input: "AP301000" → CLARIFY: What do you need help with on screen AP301000? (e.g. how to use it, a specific error, past tickets about it)
  Input: "payroll" → CLARIFY: Are you looking for payroll procedures, a specific error, or past RFS tickets about payroll?

If question is CLEAR ENOUGH (has an action, problem, or specific intent), skip this step and proceed to search.

=== ELASTICSEARCH KNOWLEDGE BASE ===

Index: grp-manuals  (GRP ERP user manual sections, chunked by heading)
  module      : e.g. "Account Payable", "Payroll", "Fixed Asset", "General Ledger"
  section     : heading name of the section
  content     : full text of the section
  screen_codes: e.g. ["AP301000", "PR201000", "GL102000"]
  images      : screenshot filenames (shown to user automatically)
  image_captions: captions for screenshots

Index: rfs-tickets-jan-2025 | rfs-tickets-feb-2025 | rfs-tickets-mar-2025  (past RFS support tickets)
  referno     : ticket reference e.g. "25011313P"
  relatedarea : module or screen area of the problem
  notes       : original problem description
  action_summary : all actions + engineer notes combined (best field for resolution details)
  priority    : 1=low, 5=high
  laststatus  : 10=closed, others=open

Index: grp-scripts  (SQL fix scripts for backend data issues)
  purpose     : what the script fixes
  content     : full SQL
  tables      : affected DB tables
  filename    : script file name

Index: grp-code  (code files — Python, C#, SQL — for reference)
  purpose     : what the code does
  content     : full code
  language    : "python" | "csharp" | "sql"
  filename    : file name

Index: acumatica-help  (Official Acumatica ERP help documentation, chunked by heading)
  Same shape as grp-manuals (module, section, subsection, content, screen_codes, images, image_captions).
  Source: Acumatica's official end-user / implementation / developer guides (AccountsPayable, GeneralLedger, FixedAssets, FrameworkDevelopmentGuide, etc.).
  Use as a FALLBACK to grp-manuals: GRP is the Malaysianised fork of Acumatica, so vanilla Acumatica behaviour applies wherever GRP did not customise. Prefer grp-manuals when both have a hit.

=== MANDATORY SEARCH PROTOCOL ===
Before answering, you MUST search. Follow this order:

1. Search grp-manuals for procedure / how-to questions
2. Search ALL THREE ticket indices (jan, feb, mar) for any past similar problems
   - Use multi_match on notes, action_summary, relatedarea
   - If first index has few results, still check the others
3. Search grp-scripts if the question involves a data issue, system error, or fix request
4. Search grp-code if question involves code logic, customization, or scripts
4b. If grp-manuals returned 0-1 results for a procedure / how-to question, ALSO search acumatica-help with the same terms. Acumatica's vanilla behaviour usually still applies to GRP. Cite manual hits from acumatica-help as source type "acumatica".
5. If first search returns 0-1 results, RETRY with:
   - Alternative keywords (synonyms, Malay/English equivalents)
   - Specific proper nouns mentioned (e.g. "JomPay", "TNB", bank names)
   - Screen codes if applicable (AP301000, PR201000, GL102000, etc.)
6. Minimum: attempt at least 3 MCP searches per query before synthesizing answer

7. CROSS-LINK TICKETS ↔ MANUALS: RFS tickets do not store screenshots, but manual indices do. When a ticket mentions a screen code (e.g. AP303000) or a procedure name (e.g. "vendor registration", "void payment", "credit memo"), you MUST also search the manual indices (grp-manuals first, then acumatica-help if grp-manuals is thin) for that screen_code or section. Embed the manual's inline screenshots in your troubleshooting answer so the user sees the screen they are working on. Examples:
   - Ticket about a vendor registration bug → search grp-manuals: { "query": { "match": { "section": "Daftar Pembekal" } } } and inline its screenshots.
   - Ticket mentions AP301000 → search grp-manuals: { "query": { "term": { "screen_codes": "AP301000" } } } — if grp-manuals returns nothing, retry the same query on acumatica-help.
   This cross-linking is mandatory whenever a ticket references a screen or procedure that exists in either manual index.

8. SIBLING-FETCH FOR PARTIAL SECTIONS: Manual sections in BOTH grp-manuals AND acumatica-help are split into smaller chunks. If a hit from EITHER index has `subsection` containing "(part N/M)" — meaning it is one piece of a larger procedure — you MUST fetch ALL siblings from the SAME index as the hit before answering. Run the following query against whichever index the partial hit came from (grp-manuals OR acumatica-help):
   {
     "query": { "term": { "section.keyword": "<exact section name from hit>" } },
     "sort": [{ "chunk_index": "asc" }],
     "size": 30,
     "_source": ["module","section","subsection","content","images","image_captions","screen_codes"]
   }
   Stitch the parts in order (part 1/M, 2/M, ...) into one continuous procedure. Inline images stay where they appear in each part. Do NOT cross indices when fetching siblings — chunks belong to one index only. Without sibling-fetch, your answer will be incomplete and may skip steps or screenshots.

=== ELASTICSEARCH QUERY FORMAT ===
BM25 (keyword) search:
{
  "query": {
    "multi_match": {
      "query": "your search terms",
      "fields": ["notes^2", "action_summary^2", "relatedarea^3"],
      "type": "best_fields"
    }
  },
  "size": 5,
  "_source": ["referno", "relatedarea", "notes", "action_summary", "priority", "laststatus"]
}

For manuals:
  "fields": ["content^2", "section^3", "module^2"]
  "_source": ["module", "section", "content", "screen_codes", "images", "image_captions"]

For scripts/code:
  "fields": ["purpose^3", "content^2"]
  "_source": ["purpose", "content", "tables", "filename", "language"]

=== ANSWER FORMAT ===

## How to do it
[Step-by-step procedure. Always name steps: "Step 1 (Pay Sheet Generation)", "Step 2 (...)"]

## Common Issues & Solutions
For each relevant past ticket found:
- **Problem:** [describe from ticket notes]
- **Solution:** [exact steps from action_summary — be specific, not vague]
- **Reference:** ticket [referno]
- **Escalate to development team if:** [condition requiring backend fix]

## Fix Script  ← only if SQL script found and relevant
[Full SQL. Explain what parameters to change (CompanyID, PayRunNbr, etc). WARN: always test with ROLLBACK before COMMIT]

=== RULES ===
- Screenshots: when retrieved manual content includes inline image markdown like `![](http://173.212.247.3:8080/...)`, copy that EXACT image markdown into your answer at the step it illustrates. The frontend renders these inline. Do NOT write [Screenshot N] placeholders. Do NOT collect images at the end of the answer — place each one immediately after the step it shows.
- Step names always: "Step 1 (Step Name)" format — never bare "Step 1"
- No personal details: no phone numbers, personal emails, individual staff names
- Audience = CENSOF support engineers, not end users
- "escalate to development team" (not "contact support")
- Never say "I don't have information" without first searching MCP

=== REQUIRED OUTPUT FORMAT ===
At the END of your response, output this block so the system can display images and sources:

```sources
{"manuals":[{"module":"MODULE_NAME","section":"SECTION_NAME"}],"acumatica":[{"module":"MODULE_NAME","section":"SECTION_NAME"}],"tickets":[{"referno":"REFNO","index":"rfs-tickets-jan-2025"}],"scripts":[{"purpose":"PURPOSE"}]}
```

Only include sources you actually used. Empty arrays [] if none. This block is hidden from the user.
"""

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="GRP Support AI API", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "https://173.212.247.3.nip.io",
    ).split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,  # required so the browser sends the HttpOnly auth cookie
)


@app.on_event("startup")
def startup():
    """Ensure auxiliary indices exist on startup."""
    for idx, mapping in [(CODE_INDEX, CODE_MAPPING), (CHATS_INDEX, CHATS_MAPPING),
                         (USERS_INDEX, USERS_MAPPING), (AUDIT_INDEX, AUDIT_MAPPING),
                         (API_KEYS_INDEX, API_KEYS_MAPPING),
                         (RESET_TOKENS_INDEX, RESET_TOKENS_MAPPING),
                         (ACUMATICA_INDEX, ACUMATICA_MAPPING)]:
        r = requests.get(f"{ES_URL}/{idx}", auth=ES_AUTH, verify=False)
        if r.status_code == 404:
            requests.put(f"{ES_URL}/{idx}", json=mapping,
                         auth=ES_AUTH, verify=False)
            log_kv("info", "index-created", index=idx)


# ── Auth ───────────────────────────────────────────────────────────────────────
import bcrypt
import jwt as _jwt
import datetime as _dt
import time as _time
import hmac as _hmac
import hashlib as _hashlib
import secrets as _secrets
from fastapi import Depends, status, Request, Response
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _sha256_hex(s: str) -> str:
    return _hashlib.sha256(s.encode()).hexdigest()


def _user_from_api_key(raw_key: str) -> dict | None:
    """Look up an active API key by sha256 hash; return user dict or None."""
    h = _sha256_hex(raw_key)
    r = requests.post(
        f"{ES_URL}/{API_KEYS_INDEX}/_search",
        auth=ES_AUTH, verify=False,
        json={"query": {"bool": {"filter": [
            {"term": {"key_hash": h}},
            {"term": {"revoked": False}},
        ]}}, "size": 1},
        timeout=5,
    )
    if r.status_code != 200:
        return None
    hits = r.json().get("hits", {}).get("hits", [])
    if not hits:
        return None
    src = hits[0]["_source"]
    owner_email = src.get("owner", "")
    # Re-validate owner is still an active user, and read role live (not the
    # stale snapshot on the key doc).
    owner = get_user_by_email(owner_email) if owner_email else None
    if not owner:
        return None
    # Best-effort last_used update; do not block.
    def _touch():
        try:
            requests.post(
                f"{ES_URL}/{API_KEYS_INDEX}/_update/{hits[0]['_id']}",
                auth=ES_AUTH, verify=False,
                json={"doc": {"last_used": int(_time.time() * 1000)}}, timeout=3,
            )
        except Exception:
            pass
    _threading.Thread(target=_touch, daemon=True).start()
    return {"email": owner_email, "role": owner.get("role", "user"),
            "auth": "apikey", "key_name": src.get("name", "")}


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=13)).decode()


def _check_password_strength(pw: str) -> None:
    """Reject weak passwords. >=12 chars, >=3 of [lower/upper/digit/symbol]."""
    if len(pw) < 12:
        raise HTTPException(400, "Password must be at least 12 characters")
    classes = sum([
        any(c.islower() for c in pw),
        any(c.isupper() for c in pw),
        any(c.isdigit() for c in pw),
        any(not c.isalnum() for c in pw),
    ])
    if classes < 3:
        raise HTTPException(400, "Password must include 3 of: lowercase, uppercase, digit, symbol")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def make_token(email: str, role: str, token_version: int = 0) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "typ":  "auth",          # distinguish from upload tokens (typ="upload")
        "sub":  email,
        "role": role,
        "tv":   int(token_version),
        "iat":  int(now.timestamp()),
        "exp":  int((now + _dt.timedelta(hours=JWT_TTL_HOURS)).timestamp()),
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


# ── Opaque upload tokens (HMAC-signed) ────────────────────────────────────────
# /upload-chat-file returns one of these instead of a server filesystem path,
# so a leaked path can't be exfiltrated by another authenticated user.
def _make_upload_token(path: str, owner: str, ttl: int = 86400) -> str:
    payload = {
        "typ": "upload",         # distinguish from auth JWTs (typ="auth")
        "p":   path,
        "o":   owner,
        "exp": int(_time.time()) + ttl,
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _resolve_upload_token(token: str, owner: str) -> str:
    try:
        payload = _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except _jwt.PyJWTError:
        raise HTTPException(403, "Invalid or expired upload token")
    if payload.get("typ") != "upload":
        raise HTTPException(403, "Wrong token type")
    if payload.get("o") != owner:
        raise HTTPException(403, "Upload token owner mismatch")
    p = payload.get("p", "")
    abs_p   = os.path.abspath(p)
    abs_dir = os.path.abspath(CHAT_UPLOAD_DIR)
    if not abs_p.startswith(abs_dir + os.sep):
        raise HTTPException(403, "Upload path outside chat upload dir")
    return p


def _bump_token_version(email: str) -> None:
    """Invalidate all existing JWTs for this user (called on pw change/reset)."""
    requests.post(
        f"{ES_URL}/{USERS_INDEX}/_update_by_query?refresh=true",
        auth=ES_AUTH, verify=False,
        json={
            "script": {
                "source": "ctx._source.token_version = (ctx._source.token_version == null ? 1 : ctx._source.token_version + 1)",
            },
            "query": {"term": {"email": email}},
        },
        timeout=10,
    )


def get_user_by_email(email: str) -> dict | None:
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_search",
        auth=ES_AUTH, verify=False,
        json={"query": {"term": {"email": email}}, "size": 1},
        timeout=5,
    )
    if r.status_code != 200:
        return None
    hits = r.json().get("hits", {}).get("hits", [])
    return hits[0]["_source"] if hits else None


SESSION_COOKIE = "grp_jwt"


def current_user(request: Request, token: str | None = Depends(oauth2_scheme)) -> dict:
    # Accept either: Bearer JWT (legacy / API clients), ApiKey header,
    # or HttpOnly session cookie (browser).
    auth_hdr = request.headers.get("authorization", "")
    if auth_hdr.lower().startswith("apikey "):
        raw = auth_hdr.split(" ", 1)[1].strip()
        u = _user_from_api_key(raw)
        if u:
            return u
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    if not token:
        token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    try:
        payload = _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except _jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    # Reject upload tokens (or anything else) presented here as a bearer auth
    # token. Auth JWTs explicitly carry typ="auth"; legacy tokens (no typ)
    # remain accepted so existing sessions don't break.
    typ = payload.get("typ", "auth")
    if typ != "auth":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    if "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")

    # Re-validate against active user state — catches deleted/demoted users
    # and stale tokens after password change/reset.
    email = payload["sub"]
    src = get_user_by_email(email)
    if not src:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    if int(src.get("token_version", 0)) != int(payload.get("tv", 0)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revoked")
    # Trust live role from the user doc, not the snapshot in the token.
    return {"email": email, "role": src.get("role", "user"), "auth": "jwt"}


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


# Login lockout — Redis-backed so it works across uvicorn workers.
# Fails open if Redis is down (logs warning) — we'd rather let valid users in
# than DoS them on an infra blip.
import redis as _redis_mod
LOGIN_FAIL_THRESHOLD = int(os.environ.get("LOGIN_FAIL_THRESHOLD", "5"))
LOGIN_FAIL_WINDOW    = int(os.environ.get("LOGIN_FAIL_WINDOW", "600"))
LOGIN_LOCK_DURATION  = int(os.environ.get("LOGIN_LOCK_DURATION", "900"))
REDIS_URL            = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
try:
    _redis = _redis_mod.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
    _redis.ping()
except Exception as _e:
    _redis = None


def _check_login_lockout(email: str) -> None:
    if _redis is None:
        return
    try:
        ttl = _redis.ttl(f"grp:login:locked:{email}")
    except Exception:
        return
    if ttl and ttl > 0:
        raise HTTPException(
            429, f"Account temporarily locked. Retry in {ttl}s.",
            headers={"Retry-After": str(ttl)},
        )


def _record_login_failure(email: str) -> None:
    if _redis is None:
        return
    try:
        key = f"grp:login:fails:{email}"
        pipe = _redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, LOGIN_FAIL_WINDOW)
        count, _ = pipe.execute()
        if int(count) >= LOGIN_FAIL_THRESHOLD:
            _redis.set(f"grp:login:locked:{email}", "1", ex=LOGIN_LOCK_DURATION)
            _redis.delete(key)
            try:
                log_kv("warning", "login-lockout", email=email, dur_s=LOGIN_LOCK_DURATION)
            except Exception:
                pass
    except Exception:
        pass


def _record_login_success(email: str) -> None:
    if _redis is None:
        return
    try:
        _redis.delete(f"grp:login:fails:{email}", f"grp:login:locked:{email}")
    except Exception:
        pass


# Password-reset throttle — caps reset emails per address and per client IP
# so the endpoint cannot be used to flood an inbox or to mint tokens en masse.
RESET_REQ_MAX_PER_EMAIL = int(os.environ.get("RESET_REQ_MAX_PER_EMAIL", "3"))
RESET_REQ_MAX_PER_IP    = int(os.environ.get("RESET_REQ_MAX_PER_IP", "15"))
RESET_REQ_WINDOW        = int(os.environ.get("RESET_REQ_WINDOW", "3600"))


def _reset_request_throttled(email: str, client_ip: str) -> bool:
    """True if password-reset requests for this email, or from this IP, exceed
    the hourly cap. Fails open (returns False) if Redis is unavailable."""
    if _redis is None:
        return False
    try:
        throttled = False
        for scope, ident, cap in (
            ("email", (email or "").strip().lower(), RESET_REQ_MAX_PER_EMAIL),
            ("ip", client_ip or "", RESET_REQ_MAX_PER_IP),
        ):
            if not ident:
                continue
            key = f"grp:reset:req:{scope}:{ident}"
            pipe = _redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, RESET_REQ_WINDOW)
            count, _ = pipe.execute()
            if int(count) > cap:
                throttled = True
        return throttled
    except Exception:
        return False



class LoginReq(BaseModel):
    email:    str
    password: str


class RegisterReq(BaseModel):
    email:    str
    name:     str = ""
    role:     str = "user"
    # `password` accepted for back-compat but ignored — admin-created accounts
    # always get a one-time setup link instead of a cleartext password email.
    password: str | None = None


@app.post("/auth/login")
def auth_login(req: LoginReq, response: Response) -> dict:
    _check_login_lockout(req.email)
    user = get_user_by_email(req.email)
    if not user or not verify_password(req.password, user.get("password_hash", "")):
        _record_login_failure(req.email)
        raise HTTPException(401, "Invalid credentials")
    _record_login_success(req.email)
    role = user.get("role", "user")
    tv = int(user.get("token_version", 0))
    token = make_token(req.email, role, tv)
    # HttpOnly session cookie — JWT is no longer reachable from JavaScript,
    # so XSS that lands in the SPA can't exfiltrate it. Secure means it only
    # travels over HTTPS. SameSite=Lax is fine: API and SPA share origin.
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=JWT_TTL_HOURS * 3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return {
        # access_token kept in the body for API/CLI clients; browser SPA
        # ignores it and reads the cookie instead.
        "access_token": token,
        "token_type":   "bearer",
        "email":        req.email,
        "role":         role,
        "name":         user.get("name", ""),
    }


@app.post("/auth/logout")
def auth_logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict", secure=True)
    return {"ok": True}


@app.get("/auth/me")
def auth_me(user: dict = Depends(current_user)) -> dict:
    return user


@app.post("/auth/register", dependencies=[Depends(require_admin)])
def auth_register(req: RegisterReq) -> dict:
    # Without SMTP we can't deliver the setup link, and without the link the
    # account has no usable password. Refuse rather than silently creating an
    # unreachable user.
    if not SMTP_HOST:
        raise HTTPException(
            503,
            "SMTP not configured — cannot send account setup link. "
            "Set SMTP_HOST in /etc/grp-api.env and restart grp-api.",
        )
    if get_user_by_email(req.email):
        raise HTTPException(409, "User already exists")
    role = req.role if req.role in ("user", "admin") else "user"
    # Provision with a strong random placeholder; user must use the setup link
    # below to choose their own password. Avoids cleartext-password email.
    placeholder_pw = _secrets.token_urlsafe(32)
    doc = {
        "email":         req.email,
        "password_hash": hash_password(placeholder_pw),
        "name":          req.name,
        "role":          role,
        "created_at":    int(_time.time() * 1000),
    }
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_doc?refresh=wait_for",
        auth=ES_AUTH, verify=False, json=doc, timeout=10,
    )
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"Register failed: {r.text[:200]}")
    user_doc_id = r.json().get("_id")

    # Mint a one-time setup token (reuses reset-token machinery) — 24h TTL so
    # the user has a reasonable window to click the link.
    raw = _secrets.token_urlsafe(32)
    tok_r = requests.post(
        f"{ES_URL}/{RESET_TOKENS_INDEX}/_doc?refresh=true",
        auth=ES_AUTH, verify=False,
        json={
            "token_hash": _sha256_hex(raw),
            "email":      req.email,
            "expires_at": int(_time.time() + 24 * 3600),
            "used":       False,
        }, timeout=10,
    )
    setup_token_id = tok_r.json().get("_id") if tok_r.status_code in (200, 201) else None

    # Send setup email synchronously. If SMTP fails, roll back the user doc
    # and the setup token so an admin can retry without a 409 collision and
    # so we never leave an unreachable account behind.
    try:
        send_email_sync(
            req.email,
            "Set up your GRP Support AI account",
            f"Hi {req.name or req.email},\n\n"
            f"An admin created an account for you on GRP Support AI.\n\n"
            f"Set your password (link valid 24 hours):\n"
            f"{FRONTEND_URL}/reset-password/?token={raw}\n\n"
            f"After setting a password, sign in at {FRONTEND_URL}\n",
        )
    except Exception as e:
        if user_doc_id:
            requests.delete(f"{ES_URL}/{USERS_INDEX}/_doc/{user_doc_id}?refresh=true",
                            auth=ES_AUTH, verify=False, timeout=10)
        if setup_token_id:
            requests.delete(f"{ES_URL}/{RESET_TOKENS_INDEX}/_doc/{setup_token_id}?refresh=true",
                            auth=ES_AUTH, verify=False, timeout=10)
        raise HTTPException(
            502,
            f"Setup email failed; user not created. Check SMTP config. ({e})",
        )

    return {"ok": True, "email": req.email, "role": role,
            "setup_link_sent": True}


# ── Password reset (token-based, for users who forgot password) ───────────────
class ResetRequestReq(BaseModel):
    email: str


class ResetConfirmReq(BaseModel):
    token: str
    new_password: str


@app.post("/auth/reset-request")
def auth_reset_request(req: ResetRequestReq, request: Request) -> dict:
    """Issue a password-reset token for the given email if it exists. Always returns ok
    to avoid email enumeration."""
    client_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                 or (request.client.host if request.client else ""))
    # Throttle silently — the response stays uniform so enumeration is still
    # impossible, but no token is minted and no email is sent when over cap.
    if _reset_request_throttled(req.email, client_ip):
        try:
            log_kv("warning", "reset-request-throttled",
                   email=req.email, ip=client_ip)
        except Exception:
            pass
        return {"ok": True}
    user = get_user_by_email(req.email)
    if user:
        raw = _secrets.token_urlsafe(32)
        h = _sha256_hex(raw)
        doc = {
            "token_hash": h,
            "email":      req.email,
            "expires_at": int(_time.time() + 3600),  # 1h TTL
            "used":       False,
        }
        requests.post(
            f"{ES_URL}/{RESET_TOKENS_INDEX}/_doc?refresh=true",
            auth=ES_AUTH, verify=False, json=doc, timeout=10,
        )
        _threading.Thread(target=send_email, args=(
            req.email,
            "Reset your GRP Support AI password",
            f"Hi,\n\n"
            f"A password reset was requested for your account.\n\n"
            f"Reset link (valid 1 hour): {FRONTEND_URL}/reset-password/?token={raw}\n\n"
            f"If you did not request this, ignore this message.\n",
        ), daemon=True).start()
    return {"ok": True}


@app.post("/auth/reset-confirm")
def auth_reset_confirm(req: ResetConfirmReq) -> dict:
    _check_password_strength(req.new_password)
    h = _sha256_hex(req.token)
    r = requests.post(
        f"{ES_URL}/{RESET_TOKENS_INDEX}/_search",
        auth=ES_AUTH, verify=False,
        json={"query": {"bool": {"filter": [
            {"term": {"token_hash": h}}, {"term": {"used": False}},
        ]}}, "size": 1}, timeout=10,
    )
    hits = r.json().get("hits", {}).get("hits", []) if r.status_code == 200 else []
    if not hits:
        raise HTTPException(400, "Invalid or expired reset token")
    src = hits[0]["_source"]
    if int(src.get("expires_at", 0)) < int(_time.time()):
        raise HTTPException(400, "Invalid or expired reset token")
    email = src["email"]
    if not get_user_by_email(email):
        raise HTTPException(400, "Account no longer exists")
    requests.post(
        f"{ES_URL}/{USERS_INDEX}/_update_by_query?refresh=true",
        auth=ES_AUTH, verify=False,
        json={
            "script": {
                "source": "ctx._source.password_hash = params.h",
                "params": {"h": hash_password(req.new_password)},
            },
            "query": {"term": {"email": email}},
        }, timeout=10,
    )
    _bump_token_version(email)
    requests.post(
        f"{ES_URL}/{RESET_TOKENS_INDEX}/_update/{hits[0]['_id']}?refresh=true",
        auth=ES_AUTH, verify=False, json={"doc": {"used": True}}, timeout=10,
    )
    return {"ok": True, "email": email}


# ── API keys (service-to-service auth alongside JWT) ──────────────────────────
class ApiKeyCreateReq(BaseModel):
    name:  str
    owner: str  # email; must be an existing user (key inherits user's role)


@app.post("/api-keys", dependencies=[Depends(require_admin)])
def api_key_create(req: ApiKeyCreateReq) -> dict:
    """Mint a new API key. Returned in the clear ONCE — store immediately."""
    user = get_user_by_email(req.owner)
    if not user:
        raise HTTPException(404, f"Owner user not found: {req.owner}")
    raw = "grp_" + _secrets.token_urlsafe(32)
    doc = {
        "key_hash":   _sha256_hex(raw),
        "name":       req.name,
        "owner":      req.owner,
        "role":       user.get("role", "user"),
        "created_at": int(_time.time() * 1000),
        "last_used":  None,
        "revoked":    False,
    }
    r = requests.post(
        f"{ES_URL}/{API_KEYS_INDEX}/_doc?refresh=wait_for",
        auth=ES_AUTH, verify=False, json=doc, timeout=10,
    )
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"Create failed: {r.text[:200]}")
    return {"id": r.json().get("_id"), "key": raw, "name": req.name, "owner": req.owner}


@app.get("/api-keys", dependencies=[Depends(require_admin)])
def api_key_list() -> list[dict]:
    r = requests.post(
        f"{ES_URL}/{API_KEYS_INDEX}/_search",
        auth=ES_AUTH, verify=False,
        json={"size": 200, "_source": ["name", "owner", "role", "created_at",
                                        "last_used", "revoked"],
              "sort": [{"created_at": {"order": "desc"}}]}, timeout=10,
    )
    hits = r.json().get("hits", {}).get("hits", []) if r.status_code == 200 else []
    return [{"id": h["_id"], **h["_source"]} for h in hits]


@app.delete("/api-keys/{key_id}", dependencies=[Depends(require_admin)])
def api_key_revoke(key_id: str) -> dict:
    r = requests.post(
        f"{ES_URL}/{API_KEYS_INDEX}/_update/{key_id}?refresh=true",
        auth=ES_AUTH, verify=False, json={"doc": {"revoked": True}}, timeout=10,
    )
    if r.status_code == 404:
        raise HTTPException(404, "Key not found")
    return {"ok": True, "id": key_id}


# ── Claude API key (admin-set, live; used by the chatbot and the RAG-API) ─────
class AnthropicKeyReq(BaseModel):
    key: str


def _mask_anthropic_key(k: str) -> str:
    return f"{k[:10]}…{k[-4:]}" if k and len(k) > 16 else "set"


@app.get("/settings/anthropic-key", dependencies=[Depends(require_admin)])
def anthropic_key_status() -> dict:
    """Report whether a Claude key is configured. Never returns the key."""
    meta: dict = {}
    try:
        r = requests.get(f"{ES_URL}/{SETTINGS_INDEX}/_doc/anthropic",
                         auth=ES_AUTH, verify=False, timeout=5)
        if r.status_code == 200:
            meta = r.json().get("_source", {}) or {}
    except Exception:
        pass
    has_stored = bool(meta.get("key"))
    return {
        "configured": has_stored,
        "source": "ui" if has_stored else "environment",
        "hint": _mask_anthropic_key(meta["key"]) if has_stored else None,
        "updated_at": meta.get("updated_at"),
        "updated_by": meta.get("updated_by"),
    }


@app.put("/settings/anthropic-key")
def anthropic_key_set(req: AnthropicKeyReq,
                      user: dict = Depends(require_admin)) -> dict:
    """Store a Claude API key. Validated against Anthropic before saving, so a
    broken key can never be persisted. Takes effect within ~60s, no restart."""
    key = (req.key or "").strip()
    if not key.startswith("sk-ant-"):
        raise HTTPException(400, "A Claude API key starts with 'sk-ant-'.")

    # Validate with a real (minimal) call before persisting.
    try:
        anthropic.Anthropic(api_key=key).messages.create(
            model="claude-haiku-4-5", max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except anthropic.APIStatusError as e:
        if getattr(e, "status_code", None) in (401, 403):
            raise HTTPException(400, "Anthropic rejected this key (unauthorized).")
        raise HTTPException(400, f"Could not validate the key — try again ({e}).")
    except Exception as e:
        raise HTTPException(400, f"Could not validate the key — try again ({e}).")

    doc = {"key": key, "updated_at": int(_time.time() * 1000),
           "updated_by": user.get("email", "?")}
    r = requests.post(
        f"{ES_URL}/{SETTINGS_INDEX}/_doc/anthropic?refresh=true",
        auth=ES_AUTH, verify=False, json=doc, timeout=10,
    )
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"Save failed: {r.text[:200]}")
    _anthropic_key_cache["at"] = 0.0   # bust cache so this worker reloads now
    return {"ok": True, "hint": _mask_anthropic_key(key)}


class ChangePasswordReq(BaseModel):
    old_password: str
    new_password: str


@app.post("/auth/change-password")
def auth_change_password(req: ChangePasswordReq, user: dict = Depends(current_user)) -> dict:
    src = get_user_by_email(user["email"])
    if not src or not verify_password(req.old_password, src.get("password_hash", "")):
        raise HTTPException(401, "Invalid credentials")
    _check_password_strength(req.new_password)
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_update_by_query?refresh=true",
        auth=ES_AUTH, verify=False,
        json={
            "script": {
                "source": "ctx._source.password_hash = params.h",
                "params": {"h": hash_password(req.new_password)},
            },
            "query": {"term": {"email": user["email"]}},
        },
        timeout=10,
    )
    if r.status_code != 200:
        raise HTTPException(500, f"Update failed: {r.text[:200]}")
    _bump_token_version(user["email"])
    return {"ok": True}


@app.get("/auth/users", dependencies=[Depends(require_admin)])
def auth_users() -> list[dict]:
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_search",
        auth=ES_AUTH, verify=False,
        json={"size": 200, "_source": ["email", "name", "role", "created_at"],
              "sort": [{"created_at": {"order": "desc"}}]},
        timeout=10,
    )
    hits = r.json().get("hits", {}).get("hits", []) if r.status_code == 200 else []
    return [h["_source"] for h in hits]


class ResetPasswordReq(BaseModel):
    new_password: str


@app.delete("/auth/users/{email}")
def auth_delete_user(email: str, admin: dict = Depends(require_admin)) -> dict:
    if email == admin["email"]:
        raise HTTPException(400, "Cannot delete yourself")
    if not get_user_by_email(email):
        raise HTTPException(404, "User not found")
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_delete_by_query?refresh=true",
        auth=ES_AUTH, verify=False,
        json={"query": {"term": {"email": email}}},
        timeout=10,
    )
    if r.status_code != 200:
        raise HTTPException(500, f"Delete failed: {r.text[:200]}")
    return {"ok": True, "deleted": r.json().get("deleted", 0)}


@app.post("/auth/users/{email}/reset-password", dependencies=[Depends(require_admin)])
def auth_reset_password(email: str, req: ResetPasswordReq) -> dict:
    if not get_user_by_email(email):
        raise HTTPException(404, "User not found")
    _check_password_strength(req.new_password)
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_update_by_query?refresh=true",
        auth=ES_AUTH, verify=False,
        json={
            "script": {
                "source": "ctx._source.password_hash = params.h",
                "params": {"h": hash_password(req.new_password)},
            },
            "query": {"term": {"email": email}},
        },
        timeout=10,
    )
    if r.status_code != 200:
        raise HTTPException(500, f"Reset failed: {r.text[:200]}")
    _bump_token_version(email)
    return {"ok": True, "email": email}


# ── Structured logging ────────────────────────────────────────────────────────
import logging as _logging
import sys as _sys
import threading as _threading
from collections import deque as _deque


class _JsonFormatter(_logging.Formatter):
    def format(self, record: _logging.LogRecord) -> str:
        payload = {
            "ts":     _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in getattr(record, "extra_fields", {}).items():
            payload[k] = v
        return json.dumps(payload, default=str)


_root_logger = _logging.getLogger()
if not any(isinstance(h, _logging.StreamHandler) and isinstance(h.formatter, _JsonFormatter)
           for h in _root_logger.handlers):
    _h = _logging.StreamHandler(_sys.stdout)
    _h.setFormatter(_JsonFormatter())
    _root_logger.addHandler(_h)
    _root_logger.setLevel(_logging.INFO)

log = _logging.getLogger("grp-api")


def log_kv(level: str, msg: str, **kv) -> None:
    rec = log.makeRecord(log.name, getattr(_logging, level.upper()), "", 0, msg, (), None)
    rec.extra_fields = kv
    log.handle(rec)


# ── Audit log ─────────────────────────────────────────────────────────────────
def write_audit(user_email: str, event: str, **fields) -> None:
    """Fire-and-forget audit write on a daemon thread; failures only log."""
    doc = {
        "ts":    _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "user":  user_email,
        "event": event,
        **{k: v for k, v in fields.items() if v is not None},
    }
    def _send():
        try:
            requests.post(
                f"{ES_URL}/{AUDIT_INDEX}/_doc",
                auth=ES_AUTH, verify=False, json=doc, timeout=5,
            )
        except Exception as e:
            log_kv("warning", "audit-write-failed", err=str(e))
    _threading.Thread(target=_send, daemon=True).start()


@app.get("/audit", dependencies=[Depends(require_admin)])
def audit_recent(user: str | None = None, size: int = 100) -> list[dict]:
    body: dict = {
        "size": min(max(size, 1), 500),
        "sort": [{"ts": {"order": "desc"}}],
    }
    if user:
        body["query"] = {"term": {"user": user}}
    r = requests.post(f"{ES_URL}/{AUDIT_INDEX}/_search",
                      auth=ES_AUTH, verify=False, json=body, timeout=10)
    if r.status_code != 200:
        return []
    return [h["_source"] for h in r.json().get("hits", {}).get("hits", [])]


# ── Per-user rate limit (in-memory per worker; cap = N_WORKERS * limit) ──────
_rate_lock = _threading.Lock()
_rate_log: dict[str, "_deque[float]"] = {}


def check_rate_limit(user_email: str) -> None:
    """Raise 429 if user exceeded QUERY_RATE_LIMIT_PER_MIN queries in the last 60s."""
    now = _time.time()
    cutoff = now - 60.0
    with _rate_lock:
        dq = _rate_log.setdefault(user_email, _deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= QUERY_RATE_LIMIT_PER_MIN:
            retry_in = max(1, int(60 - (now - dq[0])))
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({QUERY_RATE_LIMIT_PER_MIN}/min). Retry in {retry_in}s.",
                headers={"Retry-After": str(retry_in)},
            )
        dq.append(now)


# ── Image signing (HMAC-signed URLs) ──────────────────────────────────────────
from fastapi.responses import FileResponse


def _sign_image(path: str, ttl: int | None = None) -> str:
    """Return a full signed image URL for a manual-image relative path."""
    ttl = IMG_SIGN_TTL if ttl is None else ttl
    exp = int(_time.time()) + max(60, ttl)
    msg = f"{path}|{exp}".encode()
    sig = _hmac.new(IMG_SIGNING_KEY or JWT_SECRET.encode(), msg, _hashlib.sha256).hexdigest()[:32]
    encoded = _url_quote(path, safe="/")
    return f"{IMG_PUBLIC_BASE}/{encoded}?sig={sig}&exp={exp}"


def _verify_image_sig(path: str, sig: str, exp: int) -> bool:
    if exp < int(_time.time()):
        return False
    msg = f"{path}|{exp}".encode()
    expected = _hmac.new(IMG_SIGNING_KEY or JWT_SECRET.encode(), msg, _hashlib.sha256).hexdigest()[:32]
    return _hmac.compare_digest(sig, expected)


# Match any URL pointing at the configured legacy IMG_BASE so we can rewrite it
# to a signed equivalent at response time. URLs already inside answer text were
# baked in at ingest time — this is the migration path without re-embedding.
_IMG_URL_RE = re.compile(
    rf'(?P<base>{re.escape(IMG_BASE)}|{re.escape(IMG_PUBLIC_BASE)})/(?P<path>[^\s\)\"\']+)',
    re.IGNORECASE,
)


def _sign_text_images(text: str) -> str:
    """Rewrite every image URL in the text to a signed URL."""
    if not text:
        return text
    from urllib.parse import unquote

    def _rep(m: re.Match) -> str:
        raw_path = m.group("path").split("?", 1)[0]
        path = unquote(raw_path)
        return _sign_image(path)

    return _IMG_URL_RE.sub(_rep, text)


def _sign_image_dicts(items: list[dict]) -> list[dict]:
    """Rewrite the `url` field of each image dict to a signed URL."""
    out = []
    for it in items:
        url = it.get("url", "")
        m = _IMG_URL_RE.match(url)
        if m:
            from urllib.parse import unquote
            it = {**it, "url": _sign_image(unquote(m.group("path").split("?", 1)[0]))}
        out.append(it)
    return out


@app.get("/images/{path:path}")
def get_image(path: str, sig: str = "", exp: int = 0):
    """Serve a manual image only if the HMAC signature is valid and unexpired."""
    if not _verify_image_sig(path, sig, exp):
        raise HTTPException(status_code=403, detail="Invalid or expired image signature")
    img_dir_abs = os.path.abspath(IMG_DIR)
    full = os.path.abspath(os.path.join(IMG_DIR, path))
    if not (full == img_dir_abs or full.startswith(img_dir_abs + os.sep)):
        raise HTTPException(status_code=400, detail="Bad path")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(full)


# ── External alerting (Slack-compatible webhook) ──────────────────────────────
def notify_slack(text: str, **fields) -> None:
    """Fire-and-forget post to SLACK_WEBHOOK_URL. No-op if not configured."""
    if not SLACK_WEBHOOK_URL:
        return
    payload = {"text": text}
    if fields:
        payload["text"] = text + "\n" + "\n".join(f"• *{k}*: {v}" for k, v in fields.items())

    def _send():
        try:
            requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as e:
            log_kv("warning", "slack-notify-failed", err=str(e))
    _threading.Thread(target=_send, daemon=True).start()


# ── Cost / token budget ───────────────────────────────────────────────────────
def estimate_cost_usd(input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    """USD cost estimate from token counts at configured per-million rates."""
    billed_input = max(0, (input_tokens or 0) - (cached_tokens or 0))
    return round(
        (billed_input  * COST_INPUT_PER_M  / 1_000_000) +
        ((output_tokens or 0) * COST_OUTPUT_PER_M / 1_000_000) +
        ((cached_tokens or 0) * COST_CACHE_PER_M  / 1_000_000),
        4,
    )


def _month_range_iso() -> tuple[str, str]:
    now = _dt.datetime.now(_dt.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=32)
    end = end.replace(day=1)
    return start.isoformat(), end.isoformat()


def _audit_token_sum(user: str | None = None) -> dict:
    """Aggregate input/output/cached tokens for the current calendar month."""
    start, end = _month_range_iso()
    must = [{"range": {"ts": {"gte": start, "lt": end}}}, {"term": {"event": "query"}}]
    if user:
        must.append({"term": {"user": user}})
    body = {
        "size": 0,
        "query": {"bool": {"filter": must}},
        "aggs": {
            "input":  {"sum": {"field": "input_tokens"}},
            "output": {"sum": {"field": "output_tokens"}},
            "cached": {"sum": {"field": "cached_tokens"}},
            "calls":  {"value_count": {"field": "user"}},
        },
    }
    r = requests.post(f"{ES_URL}/{AUDIT_INDEX}/_search",
                      auth=ES_AUTH, verify=False, json=body, timeout=10)
    if r.status_code != 200:
        return {"input": 0, "output": 0, "cached": 0, "calls": 0}
    aggs = r.json().get("aggregations", {})
    return {
        "input":  int(aggs.get("input",  {}).get("value") or 0),
        "output": int(aggs.get("output", {}).get("value") or 0),
        "cached": int(aggs.get("cached", {}).get("value") or 0),
        "calls":  int(aggs.get("calls",  {}).get("value") or 0),
    }


def check_token_budget() -> None:
    """Raise 429 if MONTHLY_TOKEN_BUDGET is configured and already exhausted."""
    if MONTHLY_TOKEN_BUDGET <= 0:
        return
    s = _audit_token_sum()
    spent = s["input"] + s["output"]
    if spent >= MONTHLY_TOKEN_BUDGET:
        notify_slack(
            f":octagonal_sign: GRP-Support monthly token budget exhausted",
            spent=spent, budget=MONTHLY_TOKEN_BUDGET,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Monthly token budget exhausted ({spent:,}/{MONTHLY_TOKEN_BUDGET:,}). "
                   f"Try again next month or contact your admin.",
        )


@app.get("/audit/usage", dependencies=[Depends(require_admin)])
def audit_usage(user: str | None = None) -> dict:
    """Per-user month-to-date token usage and estimated cost."""
    start, end = _month_range_iso()
    must = [{"range": {"ts": {"gte": start, "lt": end}}}, {"term": {"event": "query"}}]
    if user:
        must.append({"term": {"user": user}})
    body = {
        "size": 0,
        "query": {"bool": {"filter": must}},
        "aggs": {
            "by_user": {
                "terms": {"field": "user", "size": 200},
                "aggs": {
                    "input":  {"sum": {"field": "input_tokens"}},
                    "output": {"sum": {"field": "output_tokens"}},
                    "cached": {"sum": {"field": "cached_tokens"}},
                },
            },
            "input":  {"sum": {"field": "input_tokens"}},
            "output": {"sum": {"field": "output_tokens"}},
            "cached": {"sum": {"field": "cached_tokens"}},
        },
    }
    r = requests.post(f"{ES_URL}/{AUDIT_INDEX}/_search",
                      auth=ES_AUTH, verify=False, json=body, timeout=15)
    if r.status_code != 200:
        raise HTTPException(500, f"ES query failed: {r.text[:200]}")
    aggs = r.json().get("aggregations", {})
    rows = []
    for b in aggs.get("by_user", {}).get("buckets", []):
        i = int(b.get("input",  {}).get("value") or 0)
        o = int(b.get("output", {}).get("value") or 0)
        c = int(b.get("cached", {}).get("value") or 0)
        rows.append({
            "user":   b["key"],
            "calls":  b["doc_count"],
            "input_tokens":  i,
            "output_tokens": o,
            "cached_tokens": c,
            "cost_usd":      estimate_cost_usd(i, o, c),
        })
    rows.sort(key=lambda x: x["cost_usd"], reverse=True)
    tot_i = int(aggs.get("input",  {}).get("value") or 0)
    tot_o = int(aggs.get("output", {}).get("value") or 0)
    tot_c = int(aggs.get("cached", {}).get("value") or 0)
    return {
        "month_start": start,
        "month_end":   end,
        "users":       rows,
        "total": {
            "input_tokens":  tot_i,
            "output_tokens": tot_o,
            "cached_tokens": tot_c,
            "cost_usd":      estimate_cost_usd(tot_i, tot_o, tot_c),
            "budget":        MONTHLY_TOKEN_BUDGET,
            "budget_pct":    round(((tot_i + tot_o) / MONTHLY_TOKEN_BUDGET) * 100, 1)
                              if MONTHLY_TOKEN_BUDGET > 0 else None,
        },
    }


# ── Index retention (audit / chats) ───────────────────────────────────────────
@app.post("/admin/retention/run", dependencies=[Depends(require_admin)])
def retention_run(audit_days: int = 90, chats_days: int = 365) -> dict:
    """Delete old audit / chat docs. Idempotent. Schedule via cron in production."""
    cutoff_audit = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=max(1, audit_days))).isoformat()
    cutoff_chats_ms = int((_time.time() - max(1, chats_days) * 86400) * 1000)
    out = {}
    r = requests.post(
        f"{ES_URL}/{AUDIT_INDEX}/_delete_by_query?conflicts=proceed",
        auth=ES_AUTH, verify=False, timeout=60,
        json={"query": {"range": {"ts": {"lt": cutoff_audit}}}},
    )
    out["audit"] = {"status": r.status_code, "deleted": r.json().get("deleted", 0) if r.status_code == 200 else 0}
    r = requests.post(
        f"{ES_URL}/{CHATS_INDEX}/_delete_by_query?conflicts=proceed",
        auth=ES_AUTH, verify=False, timeout=60,
        json={"query": {"range": {"updated_at": {"lt": cutoff_chats_ms}}}},
    )
    out["chats"] = {"status": r.status_code, "deleted": r.json().get("deleted", 0) if r.status_code == 200 else 0}
    log_kv("info", "retention-run", **out)
    return out


# ── SMTP / email ──────────────────────────────────────────────────────────────
def _build_email_message(to: str, subject: str, body: str):
    import smtplib  # noqa: F401 (re-exported for sync caller below)
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _smtp_send(msg) -> None:
    """Synchronous SMTP send. Raises on failure — caller decides what to do."""
    import smtplib
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
        if SMTP_USE_TLS:
            s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


def send_email_sync(to: str, subject: str, body: str) -> None:
    """Send email and raise on failure. Use for security-critical mail like
    account setup links — caller can roll back on send failure."""
    if not SMTP_HOST:
        raise RuntimeError("SMTP not configured")
    msg = _build_email_message(to, subject, body)
    _smtp_send(msg)
    log_kv("info", "email-sent-sync", to=to, subject=subject)


def send_email(to: str, subject: str, body: str) -> bool:
    """Fire-and-forget: send a plain-text email. No-op + warning if SMTP_HOST
    is empty. Failures only log; caller never learns. Use for non-critical
    mail (password-reset hints, alerts) — for security-critical mail prefer
    send_email_sync()."""
    if not SMTP_HOST:
        log_kv("warning", "smtp-not-configured", to=to, subject=subject)
        return False
    msg = _build_email_message(to, subject, body)

    def _send():
        try:
            _smtp_send(msg)
            log_kv("info", "email-sent", to=to, subject=subject)
        except Exception as e:
            log_kv("warning", "email-send-failed", to=to, err=str(e))
    _threading.Thread(target=_send, daemon=True).start()
    return True


# ── Models ─────────────────────────────────────────────────────────────────────
class ConversationTurn(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    question: str
    top_k: int = 5
    include_images: bool = True
    history: list[ConversationTurn] = []
    attached_files: list[str] = []


class Source(BaseModel):
    type: str
    index: str
    module: str | None = None
    section: str | None = None
    referno: str | None = None
    score: float = 0.0


class QueryResponse(BaseModel):
    answer: str
    images: list[dict]
    sources: list[Source]
    context_used: int
    expanded_query: str | None = None


# ── Shared Helpers ─────────────────────────────────────────────────────────────

def _strip_escape(raw: str) -> str:
    raw = re.sub(r'\x1b\[[0-9;?><]*[a-zA-Z]', '', raw)
    raw = re.sub(r'\x1b\].*?(\x07|\x1b\\)', '', raw)
    raw = re.sub(r'\x1b[=>]', '', raw)
    raw = re.sub(r'\[[\?><]?[0-9;]*[a-zA-Z]', '', raw)
    return raw.strip()


def get_embedding(text: str) -> list[float]:
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:4000]},
        timeout=30
    )
    r.raise_for_status()
    return r.json()["embedding"]


# ── Loader Helpers (from load_manuals.py / load_scripts.py / load_rfs_embed.py) ─

def _extract_module_name(filename: str) -> str:
    m = re.search(r'Manual Pengguna\s+(.+?)\s+v\d', filename)
    if m:
        return m.group(1).strip()
    return re.sub(r'\.(md|txt|docx)$', '', filename)


def _extract_screen_codes(text: str) -> list[str]:
    return list(set(re.findall(r'\b[A-Z]{2,3}\d{6}\b', text)))


def _extract_images_and_captions(text: str) -> tuple[list[str], list[str]]:
    images, captions = [], []
    lines = text.split('\n')
    seen = set()
    for i, line in enumerate(lines):
        img_match = re.search(
            r'!\[.*?\]\((?:.*?Images/|https?://[^/)]+/)(.+?)\)',
            line,
        )
        if img_match:
            fname = img_match.group(1)
            if fname not in seen:
                seen.add(fname)
                images.append(fname)
                for j in range(i + 1, min(i + 4, len(lines))):
                    next_line = lines[j].strip()
                    if not next_line:
                        continue
                    cap = re.match(r'^\*(.+)\*$', next_line)
                    if cap:
                        captions.append(cap.group(1))
                    break
    return images, captions


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            meta_text = text[3:end]
            body = text[end + 4:].strip()
            meta = {}
            for line in meta_text.strip().split('\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    meta[k.strip()] = v.strip()
            return meta, body
    return {}, text


def _chunk_by_headings(body: str, module: str, source_file: str) -> list[dict]:
    chunks = []
    heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    matches = list(heading_pattern.finditer(body))

    spans = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        spans.append((m.group(1), m.group(2).strip(), body[m.end():end]))

    if matches:
        pre = body[:matches[0].start()].strip()
        if pre and len(pre) > 100:
            spans.insert(0, ('#', 'Introduction', pre))

    # Length-cap secondary split: oversized heading chunks split by paragraph
    # so each piece fits under the embedding window (4000 chars) for full semantic coverage.
    MAX_CHARS = 1500

    def _abs_img(m):
        # URL-encode path so spaces, parens, etc. don't break markdown image syntax
        encoded = _url_quote(m.group(1), safe="/")
        return f"![]({IMG_BASE}/{encoded})"

    def _split_paragraphs(text: str, limit: int) -> list[str]:
        if len(text) <= limit:
            return [text]
        paras = re.split(r'\n\s*\n', text)
        out, cur = [], ""
        for p in paras:
            p = p.strip()
            if not p:
                continue
            # If a single paragraph is itself larger than limit, hard-split it.
            if len(p) > limit:
                if cur:
                    out.append(cur)
                    cur = ""
                for i in range(0, len(p), limit):
                    out.append(p[i:i + limit])
                continue
            if cur and len(cur) + len(p) + 2 > limit:
                out.append(cur)
                cur = p
            else:
                cur = (cur + "\n\n" + p) if cur else p
        if cur:
            out.append(cur)
        return out

    chunk_idx = 0
    for level, heading, content in spans:
        content = content.strip()
        if not content or len(content) < 30:
            continue

        images, captions = _extract_images_and_captions(content)
        screen_codes = _extract_screen_codes(heading + ' ' + content)

        # Allow balanced parens inside the path (e.g. stem "Manual (AP)") so the
        # URL isn't truncated at the first ).
        clean_content = re.sub(
            r'!\[[^\]]*\]\(.*?Images/((?:[^()]|\([^()]*\))+?)\)',
            _abs_img, content,
        ).strip()

        section = heading if len(level) <= 2 else ''
        subsection = heading if len(level) >= 3 else ''

        parts = _split_paragraphs(clean_content, MAX_CHARS)
        n_parts = len(parts)

        for part_i, part_text in enumerate(parts):
            sub_label = subsection
            if n_parts > 1:
                marker = f"(part {part_i + 1}/{n_parts})"
                sub_label = f"{subsection} {marker}".strip()
            # Re-extract images/captions/codes per part so each chunk's metadata
            # reflects only what's in that piece.
            part_images, part_captions = _extract_images_and_captions(part_text)
            part_codes = _extract_screen_codes(heading + ' ' + part_text)
            embed_text = f"{module}: {heading}. {part_text}"[:4000]

            chunks.append({
                'module': module, 'section': heading, 'subsection': sub_label,
                'content': part_text, 'screen_codes': part_codes,
                'images': part_images, 'image_captions': part_captions,
                'chunk_index': chunk_idx, 'total_chunks': 0,
                'prev_section': '', 'next_section': '', 'prev_tail': '',
                'source_file': source_file, '_embed_text': embed_text,
            })
            chunk_idx += 1

    total = len(chunks)
    for i, chunk in enumerate(chunks):
        chunk['total_chunks'] = total
        if i > 0:
            chunk['prev_section'] = chunks[i - 1]['section']
            chunk['prev_tail'] = chunks[i - 1]['content'][-150:].strip()
        if i < total - 1:
            chunk['next_section'] = chunks[i + 1]['section']

    return chunks


def _extract_tables(sql: str) -> list[str]:
    patterns = [
        r'\bFROM\s+(\w+)', r'\bUPDATE\s+(\w+)',
        r'\bINSERT\s+INTO\s+(\w+)', r'\bDELETE\s+FROM\s+(\w+)', r'\bJOIN\s+(\w+)',
    ]
    tables = set()
    for p in patterns:
        for m in re.finditer(p, sql, re.IGNORECASE):
            t = m.group(1)
            if t.upper() not in ('TRAN', 'SET', 'WHERE', 'AND', 'OR'):
                tables.add(t)
    return sorted(tables)


def _build_rfs_embed_text(ticket: dict) -> str:
    parts = []
    if ticket.get("relatedarea"):
        parts.append(str(ticket["relatedarea"]))
    if ticket.get("notes"):
        parts.append(str(ticket["notes"])[:150])
    for action in ticket.get("actions", []):
        note = action.get("note")
        if note and len(str(note)) > 10:
            parts.append(str(note)[:80])
            break
    text = ". ".join(parts)
    return re.sub(r'\s+', ' ', text).strip()[:4000]


def _find_section_doc(module: str, section: str,
                       index: str = "grp-manuals") -> tuple[str | None, dict | None]:
    """Find ES doc _id and source for a given module+section in `index`."""
    body = {
        "size": 1,
        "_source": ["module", "section", "images", "image_captions"],
        "query": {
            "bool": {
                "filter": [{"term": {"module": module}}],
                "must": [{"match_phrase": {"section": section}}]
            }
        }
    }
    r = requests.post(f"{ES_URL}/{index}/_search",
                      auth=ES_AUTH, verify=False, json=body, timeout=5)
    hits = r.json().get("hits", {}).get("hits", [])
    if hits:
        return hits[0]["_id"], hits[0]["_source"]
    # Fallback: match only on section
    body["query"] = {"match": {"section": section}}
    r = requests.post(f"{ES_URL}/{index}/_search",
                      auth=ES_AUTH, verify=False, json=body, timeout=5)
    hits = r.json().get("hits", {}).get("hits", [])
    if hits:
        return hits[0]["_id"], hits[0]["_source"]
    return None, None


def _bulk_index(docs: list[tuple[str, str, dict]]):
    """Bulk index list of (doc_id, index, doc) tuples."""
    lines = []
    for doc_id, index, doc in docs:
        meta = {"index": {"_index": index}}
        if doc_id:
            meta["index"]["_id"] = doc_id
        lines.append(json.dumps(meta))
        lines.append(json.dumps(doc))
    body = "\n".join(lines) + "\n"
    r = requests.post(f"{ES_URL}/_bulk",
                      data=body.encode(),
                      headers={"Content-Type": "application/x-ndjson"},
                      auth=ES_AUTH, verify=False, timeout=60)
    return r.status_code in (200, 201)


# ── kNN Seed Search ────────────────────────────────────────────────────────────
MANUAL_FIELDS = ["module", "section", "content", "screen_codes",
                  "images", "image_captions", "prev_section"]
TICKET_FIELDS = ["lodge_id", "referno", "notes", "relatedarea",
                 "priority", "laststatus", "action_summary"]
SCRIPT_FIELDS = ["script_name", "filename", "purpose", "content", "tables"]
CODE_FIELDS   = ["filename", "purpose", "content", "language"]


def _knn_search(index: str, embedding: list[float], top_k: int,
                source_fields: list) -> list[dict]:
    try:
        r = requests.post(
            f"{ES_URL}/{index}/_search",
            auth=ES_AUTH, verify=False, timeout=10,
            json={
                "size": top_k,
                "knn": {"field": "embedding", "query_vector": embedding,
                        "k": top_k, "num_candidates": max(top_k * 10, 100)},
                "_source": source_fields
            }
        )
        if r.status_code == 200:
            return r.json().get("hits", {}).get("hits", [])
    except Exception:
        pass
    return []


def get_seed_context(embedding: list[float]) -> tuple[str, int]:
    seed_k = 3
    manual_hits    = _knn_search("grp-manuals",    embedding, seed_k, MANUAL_FIELDS)
    acumatica_hits = _knn_search(ACUMATICA_INDEX,  embedding, seed_k, MANUAL_FIELDS)
    script_hits    = _knn_search(SCRIPTS_INDEX,    embedding, seed_k, SCRIPT_FIELDS)
    code_hits      = _knn_search(CODE_INDEX,       embedding, seed_k, CODE_FIELDS)

    ticket_hits = []
    for idx in RFS_INDICES:
        hits = _knn_search(idx, embedding, seed_k, TICKET_FIELDS)
        for h in hits:
            h["_index_name"] = idx
        ticket_hits.extend(hits)
    ticket_hits.sort(key=lambda x: x.get("_score", 0), reverse=True)
    ticket_hits = ticket_hits[:seed_k]

    parts = []

    if script_hits:
        parts.append("=== GRP SQL FIX SCRIPTS (semantic seed) ===")
        for h in script_hits:
            s = h["_source"]
            parts.append(
                f"SCRIPT: {s.get('purpose','')}\nFILE: {s.get('filename','')}\n"
                f"TABLES: {', '.join(s.get('tables', []))}\nSQL:\n{s.get('content','')}\n"
            )

    if code_hits:
        parts.append("=== GRP CODE FILES (semantic seed) ===")
        for h in code_hits:
            s = h["_source"]
            parts.append(
                f"FILE: {s.get('filename','')}\nLANGUAGE: {s.get('language','')}\n"
                f"PURPOSE: {s.get('purpose','')}\nCODE:\n{s.get('content','')[:500]}\n"
            )

    if manual_hits:
        parts.append("=== GRP MANUAL SECTIONS (semantic seed) ===")
        for h in manual_hits:
            s = h["_source"]
            txt = f"MODULE: {s.get('module','')}\nSECTION: {s.get('section','')}\n"
            if s.get("screen_codes"):
                txt += f"SCREEN CODES: {', '.join(s['screen_codes'])}\n"
            txt += f"CONTENT:\n{s.get('content','')[:500]}\n"
            if s.get("image_captions"):
                txt += f"IMAGE CAPTIONS: {' | '.join(s['image_captions'])}\n"
            if s.get("images"):
                txt += f"HAS SCREENSHOTS: Yes ({len(s['images'])} images)\n"
            parts.append(txt)

    if acumatica_hits:
        parts.append("=== ACUMATICA HELP SECTIONS (semantic seed — fallback to GRP) ===")
        for h in acumatica_hits:
            s = h["_source"]
            txt = f"MODULE: {s.get('module','')}\nSECTION: {s.get('section','')}\n"
            if s.get("screen_codes"):
                txt += f"SCREEN CODES: {', '.join(s['screen_codes'])}\n"
            txt += f"CONTENT:\n{s.get('content','')[:500]}\n"
            if s.get("image_captions"):
                txt += f"IMAGE CAPTIONS: {' | '.join(s['image_captions'])}\n"
            if s.get("images"):
                txt += f"HAS SCREENSHOTS: Yes ({len(s['images'])} images)\n"
            parts.append(txt)

    if ticket_hits:
        parts.append("=== SIMILAR PAST RFS TICKETS (semantic seed) ===")
        for h in ticket_hits:
            s = h["_source"]
            txt = (
                f"TICKET: {s.get('referno', s.get('lodge_id','?'))}\n"
                f"PROBLEM: {str(s.get('notes',''))[:200]}\n"
                f"AREA: {s.get('relatedarea','')}\nSTATUS: {s.get('laststatus','')}\n"
            )
            if s.get("action_summary"):
                txt += f"ACTIONS/NOTES: {str(s['action_summary'])[:400]}\n"
            parts.append(txt)

    seed_count = (len(manual_hits) + len(acumatica_hits) + len(ticket_hits)
                  + len(script_hits) + len(code_hits))
    return "\n---\n".join(parts), seed_count


# ── Claude Agent ───────────────────────────────────────────────────────────────

def format_history(history: list) -> str:
    if not history:
        return ""
    parts = ["=== CONVERSATION HISTORY (most recent last) ==="]
    for turn in history[-6:]:
        role = "Support Engineer" if turn.role == "user" else "Assistant"
        parts.append(f"{role}: {turn.content[:800]}")
    return "\n".join(parts) + "\n\n"


import anthropic

# ── Claude API key — env default, overridable live via the admin UI ──────────
# The effective key is whatever is stored in grp-settings (set in the UI),
# falling back to ANTHROPIC_API_KEY from the environment. The stored value is
# cached ~60s so a UI change takes effect without a restart; the Anthropic
# client is rebuilt whenever the effective key changes.
_ANTHROPIC_KEY_CACHE_TTL = 60
_anthropic_key_cache = {"key": None, "at": 0.0}
_anthropic_client = None
_anthropic_client_key = None


def _stored_anthropic_key() -> str | None:
    """The admin-set Claude key from grp-settings, or None. Cached ~60s."""
    now = _time.time()
    if now - _anthropic_key_cache["at"] < _ANTHROPIC_KEY_CACHE_TTL:
        return _anthropic_key_cache["key"]
    try:
        r = requests.get(f"{ES_URL}/{SETTINGS_INDEX}/_doc/anthropic",
                         auth=ES_AUTH, verify=False, timeout=5)
        key = None
        if r.status_code == 200:
            key = (r.json().get("_source") or {}).get("key") or None
        _anthropic_key_cache["key"] = key
        _anthropic_key_cache["at"] = now
        return key
    except Exception:
        return _anthropic_key_cache["key"]   # last known on an ES blip


def _effective_anthropic_key() -> str:
    return _stored_anthropic_key() or ANTHROPIC_API_KEY


def get_anthropic() -> "anthropic.Anthropic":
    """Anthropic client built with the effective key; rebuilt when it changes."""
    global _anthropic_client, _anthropic_client_key
    key = _effective_anthropic_key()
    if _anthropic_client is None or _anthropic_client_key != key:
        _anthropic_client = anthropic.Anthropic(api_key=key)
        _anthropic_client_key = key
    return _anthropic_client

ES_TOOLS = [
    {
        "name": "es_search",
        "description": (
            "Search an Elasticsearch index. `body` is a standard ES query DSL "
            "(query, size, _source, sort, etc.). Returns the raw ES response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "body":  {"type": "object"},
            },
            "required": ["index", "body"],
        },
    },
    {
        "name": "es_list_indices",
        "description": "List all available Elasticsearch indices with doc counts.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "es_get_mappings",
        "description": "Return the field mappings for an Elasticsearch index.",
        "input_schema": {
            "type": "object",
            "properties": {"index": {"type": "string"}},
            "required": ["index"],
        },
    },
]

_TOOL_RESULT_CAP = 50_000  # per call; oversized ES responses get truncated
_MAX_TOOL_ITERS  = 20      # hard ceiling on agent loop

# Hard server-side allowlist. Prevents prompt-injection (agent tools) and
# admin-route path-injection (delete-index, reindex, search, etc.) from
# touching internal indices (users, api-keys, audit, chats, reset-tokens).
_KNOWLEDGE_INDICES: set[str] = {
    "grp-manuals",
    SCRIPTS_INDEX,
    CODE_INDEX,
    ACUMATICA_INDEX,
    *RFS_INDICES,
}


def _check_index_allowed(idx: str) -> str | None:
    """Agent tool-side check: returns error string instead of raising."""
    if idx not in _KNOWLEDGE_INDICES:
        return (f"Refused: index '{idx}' is not in the knowledge allowlist. "
                f"Allowed indices: {sorted(_KNOWLEDGE_INDICES)}")
    return None


def _require_knowledge_index(idx: str) -> None:
    """HTTP route-side check: raises 400 outside the allowlist."""
    if idx not in _KNOWLEDGE_INDICES:
        raise HTTPException(
            400,
            f"Index '{idx}' is not a knowledge index. "
            f"Allowed: {sorted(_KNOWLEDGE_INDICES)}",
        )


def _tool_es_search(input_: dict) -> str:
    idx = input_.get("index", "")
    err = _check_index_allowed(idx)
    if err:
        return err
    r = requests.post(
        f"{ES_URL}/{idx}/_search",
        auth=ES_AUTH, verify=False, json=input_["body"], timeout=30,
    )
    return r.text


def _tool_es_list_indices(_: dict) -> str:
    r = requests.get(f"{ES_URL}/_cat/indices?format=json",
                     auth=ES_AUTH, verify=False, timeout=10)
    if r.status_code != 200:
        return r.text
    try:
        all_idx = r.json()
    except Exception:
        return r.text
    filtered = [x for x in all_idx if x.get("index") in _KNOWLEDGE_INDICES]
    return json.dumps(filtered)


def _tool_es_get_mappings(input_: dict) -> str:
    idx = input_.get("index", "")
    err = _check_index_allowed(idx)
    if err:
        return err
    r = requests.get(f"{ES_URL}/{idx}/_mapping",
                     auth=ES_AUTH, verify=False, timeout=10)
    return r.text


_TOOL_DISPATCH = {
    "es_search":        _tool_es_search,
    "es_list_indices":  _tool_es_list_indices,
    "es_get_mappings":  _tool_es_get_mappings,
}


def _run_tool(name: str, input_: dict) -> str:
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        out = fn(input_ or {})
    except Exception as e:
        return f"Tool error: {e}"
    if len(out) > _TOOL_RESULT_CAP:
        out = out[:_TOOL_RESULT_CAP] + "\n...[truncated]"
    return out


def _read_attached_file_text(path: str, max_bytes: int = 200_000) -> str:
    try:
        with open(path, "rb") as f:
            content = f.read(max_bytes)
        return content.decode("utf-8", errors="replace")
    except Exception as e:
        return f"[failed to read {path}: {e}]"


def _build_user_text(question: str, seed_context: str,
                     history: list = None, attached_files: list = None) -> str:
    history_text = format_history(history or [])
    safe_files = [p for p in (attached_files or [])
                  if isinstance(p, str) and p.startswith(CHAT_UPLOAD_DIR + "/") and os.path.exists(p)]
    files_block = ""
    if safe_files:
        chunks = [f"--- FILE: {os.path.basename(p)} ---\n" + _read_attached_file_text(p)
                  for p in safe_files]
        files_block = (
            "USER ATTACHED FILES — content embedded below; cite alongside knowledge-base sources:\n\n"
            + "\n\n".join(chunks) + "\n\n"
        )
    return (
        f"{history_text}"
        f"{files_block}"
        f"INITIAL CONTEXT (kNN semantic seed — treat as incomplete starting point):\n"
        f"{seed_context}\n\n"
        f"USER QUESTION: {question}\n\n"
        f"Now search Elasticsearch via the available tools, then answer."
    )


def _block_to_dict(b) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return {"type": b.type}


def call_claude_agent(question: str, seed_context: str,
                      history: list = None,
                      attached_files: list = None) -> dict:
    """Run the agent loop. Returns {text, tool_calls, input_tokens, output_tokens, cached_tokens}."""
    user_text = _build_user_text(question, seed_context, history, attached_files)
    messages: list = [{"role": "user", "content": user_text}]
    tool_calls = 0
    tot_in = tot_out = tot_cached = 0

    try:
        for _step in range(_MAX_TOOL_ITERS):
            resp = get_anthropic().messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=8000,
                system=[{
                    "type": "text",
                    "text": AGENT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=ES_TOOLS,
                messages=messages,
            )
            usage = getattr(resp, "usage", None)
            if usage:
                tot_in     += getattr(usage, "input_tokens", 0) or 0
                tot_out    += getattr(usage, "output_tokens", 0) or 0
                tot_cached += getattr(usage, "cache_read_input_tokens", 0) or 0
            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant",
                                 "content": [_block_to_dict(b) for b in resp.content]})
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        tool_calls += 1
                        out = _run_tool(block.name, block.input or {})
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": block.id,
                            "content":     out,
                        })
                messages.append({"role": "user", "content": tool_results})
                continue
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {
                "text":           text or "No response from Claude.",
                "tool_calls":     tool_calls,
                "input_tokens":   tot_in,
                "output_tokens":  tot_out,
                "cached_tokens":  tot_cached,
            }
        return {
            "text":           "Tool loop exceeded max iterations — partial answer unavailable.",
            "tool_calls":     tool_calls,
            "input_tokens":   tot_in,
            "output_tokens":  tot_out,
            "cached_tokens":  tot_cached,
        }
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e.message}")
    except anthropic.APITimeoutError:
        raise HTTPException(status_code=504, detail="Anthropic API timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Claude error: {e}")


SOURCES_RE = re.compile(r'```sources\s*(\{.*?\})\s*```', re.DOTALL)


def parse_sources_block(raw: str) -> tuple[str, dict]:
    m = SOURCES_RE.search(raw)
    if not m:
        return raw, {}
    try:
        sources_dict = json.loads(m.group(1))
    except json.JSONDecodeError:
        sources_dict = {}
    return SOURCES_RE.sub('', raw).strip(), sources_dict


def fetch_images_for_sections(manuals: list[dict],
                               index: str = "grp-manuals") -> list[dict]:
    images = []
    seen = set()
    for ref in manuals:
        module = ref.get("module", "")
        section = ref.get("section", "")
        if not section:
            continue
        try:
            doc_id, src = _find_section_doc(module, section, index=index)
            if src:
                img_list = src.get("images", [])
                cap_list = src.get("image_captions", [])
                for i, fname in enumerate(img_list[:6]):
                    if fname in seen:
                        continue
                    seen.add(fname)
                    img_path = fname.replace("Doc-Images/", "", 1)
                    images.append({
                        "url":     _sign_image(img_path),
                        "module":  src.get("module", module),
                        "section": src.get("section", section),
                        "caption": cap_list[i] if i < len(cap_list) else "",
                    })
        except Exception:
            pass
    return images


def build_sources_from_dict(sources_dict: dict) -> list[Source]:
    sources = []
    for m in sources_dict.get("manuals", []):
        sources.append(Source(type="manual", index="grp-manuals",
                              module=m.get("module"), section=m.get("section")))
    for a in sources_dict.get("acumatica", []):
        sources.append(Source(type="acumatica", index=ACUMATICA_INDEX,
                              module=a.get("module"), section=a.get("section")))
    for t in sources_dict.get("tickets", []):
        sources.append(Source(type="ticket",
                              index=t.get("index", "rfs-tickets"),
                              referno=t.get("referno")))
    for s in sources_dict.get("scripts", []):
        sources.append(Source(type="script", index=SCRIPTS_INDEX,
                              section=s.get("purpose")))
    return sources


# ── Routes: Core ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "3.1.0-agent"}


@app.get("/indices", dependencies=[Depends(current_user)])
def list_indices():
    result = {}
    for idx in ["grp-manuals", ACUMATICA_INDEX, SCRIPTS_INDEX, CODE_INDEX] + RFS_INDICES:
        try:
            r = requests.get(f"{ES_URL}/{idx}/_count", auth=ES_AUTH,
                             verify=False, timeout=5)
            result[idx] = r.json().get("count", 0)
        except Exception:
            result[idx] = -1
    return result


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, user: dict = Depends(current_user)):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Empty question")
    check_rate_limit(user["email"])
    check_token_budget()

    # attached_files arrive as opaque HMAC tokens; resolve to filesystem paths
    # while enforcing the token's owner matches the requesting user.
    if req.attached_files:
        req.attached_files = [_resolve_upload_token(t, user["email"])
                              for t in req.attached_files]

    t0 = _time.time()
    try:
        embedding = get_embedding(req.question)
    except Exception as e:
        write_audit(user["email"], "query",
                    question=req.question[:1000], status="error",
                    error=f"embedding: {e}", latency_ms=int((_time.time()-t0)*1000))
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    seed_context, seed_count = get_seed_context(embedding)
    try:
        result = call_claude_agent(req.question, seed_context, req.history, req.attached_files)
    except HTTPException as he:
        write_audit(user["email"], "query",
                    question=req.question[:1000], model=ANTHROPIC_MODEL,
                    status="error", error=he.detail,
                    latency_ms=int((_time.time()-t0)*1000))
        raise

    raw_answer = result["text"]
    clarify_match = re.match(r'^CLARIFY:\s*(.+)', raw_answer.strip(), re.DOTALL)
    if clarify_match:
        write_audit(user["email"], "query",
                    question=req.question[:1000], model=ANTHROPIC_MODEL,
                    status="clarify",
                    tool_calls=result["tool_calls"],
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                    cached_tokens=result["cached_tokens"],
                    answer_chars=len(raw_answer),
                    latency_ms=int((_time.time()-t0)*1000))
        return QueryResponse(
            answer=clarify_match.group(1).strip(),
            images=[], sources=[], context_used=0,
            expanded_query="clarification_needed"
        )

    answer, sources_dict = parse_sources_block(raw_answer)
    answer = _sign_text_images(answer)
    if req.include_images:
        images = fetch_images_for_sections(sources_dict.get("manuals", []))
        images += fetch_images_for_sections(sources_dict.get("acumatica", []),
                                            index=ACUMATICA_INDEX)
    else:
        images = []
    sources = build_sources_from_dict(sources_dict)

    write_audit(user["email"], "query",
                question=req.question[:1000], model=ANTHROPIC_MODEL,
                status="ok",
                tool_calls=result["tool_calls"],
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                cached_tokens=result["cached_tokens"],
                answer_chars=len(answer),
                latency_ms=int((_time.time()-t0)*1000))

    return QueryResponse(
        answer=answer, images=images, sources=sources,
        context_used=seed_count, expanded_query=None
    )


# ── Streaming /query ──────────────────────────────────────────────────────────
from fastapi.responses import StreamingResponse


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _stream_claude_agent(user_email: str, req: QueryRequest):
    """Generator yielding SSE strings. Streams text deltas as Claude generates."""
    t0 = _time.time()
    try:
        embedding = get_embedding(req.question)
    except Exception as e:
        yield _sse("error", {"detail": f"Embedding failed: {e}"})
        write_audit(user_email, "query.stream",
                    question=req.question[:1000], status="error",
                    error=f"embedding: {e}",
                    latency_ms=int((_time.time()-t0)*1000))
        return

    seed_context, _seed_count = get_seed_context(embedding)
    user_text = _build_user_text(req.question, seed_context, req.history, req.attached_files)
    messages: list = [{"role": "user", "content": user_text}]
    tool_calls = 0
    tot_in = tot_out = tot_cached = 0
    final_text_parts: list[str] = []

    try:
        for _step in range(_MAX_TOOL_ITERS):
            with get_anthropic().messages.stream(
                model=ANTHROPIC_MODEL,
                max_tokens=8000,
                system=[{
                    "type": "text",
                    "text": AGENT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=ES_TOOLS,
                messages=messages,
            ) as stream:
                for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        final_text_parts.append(event.delta.text)
                        yield _sse("delta", {"text": event.delta.text})
                final = stream.get_final_message()

            usage = getattr(final, "usage", None)
            if usage:
                tot_in     += getattr(usage, "input_tokens", 0) or 0
                tot_out    += getattr(usage, "output_tokens", 0) or 0
                tot_cached += getattr(usage, "cache_read_input_tokens", 0) or 0

            if final.stop_reason == "tool_use":
                messages.append({"role": "assistant",
                                 "content": [_block_to_dict(b) for b in final.content]})
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        tool_calls += 1
                        yield _sse("tool", {"name": block.name, "input": block.input})
                        out = _run_tool(block.name, block.input or {})
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": block.id,
                            "content":     out,
                        })
                messages.append({"role": "user", "content": tool_results})
                # Reset text parts at each iteration; only the final one matters for sources
                final_text_parts = []
                continue

            # end_turn — emit done with parsed sources from accumulated text
            full = "".join(final_text_parts)
            answer, sources_dict = parse_sources_block(full)
            answer = _sign_text_images(answer)
            if req.include_images:
                images = fetch_images_for_sections(sources_dict.get("manuals", []))
                images += fetch_images_for_sections(sources_dict.get("acumatica", []),
                                                    index=ACUMATICA_INDEX)
            else:
                images = []
            sources = build_sources_from_dict(sources_dict)
            yield _sse("done", {
                "answer":       answer,
                "answer_chars": len(answer),
                "sources":      [s.dict() if hasattr(s, "dict") else s for s in sources],
                "images":       images,
                "tool_calls":   tool_calls,
            })
            write_audit(user_email, "query.stream",
                        question=req.question[:1000], model=ANTHROPIC_MODEL,
                        status="ok", tool_calls=tool_calls,
                        input_tokens=tot_in, output_tokens=tot_out,
                        cached_tokens=tot_cached, answer_chars=len(answer),
                        latency_ms=int((_time.time()-t0)*1000))
            return

        yield _sse("error", {"detail": "tool loop exceeded"})
        write_audit(user_email, "query.stream",
                    question=req.question[:1000], model=ANTHROPIC_MODEL,
                    status="error", error="tool-loop-exceeded",
                    tool_calls=tool_calls,
                    latency_ms=int((_time.time()-t0)*1000))
    except anthropic.APIStatusError as e:
        yield _sse("error", {"detail": e.message})
        write_audit(user_email, "query.stream",
                    question=req.question[:1000], model=ANTHROPIC_MODEL,
                    status="error", error=str(e.message),
                    latency_ms=int((_time.time()-t0)*1000))
    except Exception as e:
        yield _sse("error", {"detail": str(e)})
        write_audit(user_email, "query.stream",
                    question=req.question[:1000], model=ANTHROPIC_MODEL,
                    status="error", error=str(e),
                    latency_ms=int((_time.time()-t0)*1000))


@app.post("/query/stream")
def query_stream(req: QueryRequest, user: dict = Depends(current_user)):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Empty question")
    check_rate_limit(user["email"])
    check_token_budget()
    if req.attached_files:
        req.attached_files = [_resolve_upload_token(t, user["email"])
                              for t in req.attached_files]
    return StreamingResponse(_stream_claude_agent(user["email"], req),
                             media_type="text/event-stream")


# ── Routes: Chat file upload (one-shot, Claude reads at query time) ────────────

@app.post("/upload-chat-file")
async def upload_chat_file(file: UploadFile = File(...),
                            user: dict = Depends(current_user)) -> dict:
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in CHAT_UPLOAD_EXTS:
        raise HTTPException(
            status_code=400,
            detail=(f"Unsupported type: {ext or 'unknown'}. "
                    f"Chat attachments must be text-readable: "
                    f"{', '.join(sorted(CHAT_UPLOAD_EXTS))}."),
        )

    # Cap memory: read at most MAX+1 bytes so an oversized upload fails fast
    # instead of buffering the whole body first.
    data = await file.read(CHAT_UPLOAD_MAX_BYTES + 1)
    if len(data) > CHAT_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB)")

    sub = os.path.join(CHAT_UPLOAD_DIR, uuid.uuid4().hex)
    os.makedirs(sub, exist_ok=True)
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', os.path.basename(file.filename))
    saved = os.path.join(sub, safe_name)
    with open(saved, "wb") as f:
        f.write(data)

    if ext == ".docx":
        md_path = saved + ".md"
        try:
            subprocess.run(["pandoc", saved, "-o", md_path], check=True, timeout=30)
            saved = md_path
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"DOCX conversion failed: {e}")

    # Return an opaque, HMAC-signed token (NOT the server filesystem path) so
    # a leaked id from one user cannot be replayed by another. /query and
    # /query/stream resolve the token back to a path while enforcing owner.
    token = _make_upload_token(saved, user["email"])
    return {"id": token, "name": file.filename, "size": len(data)}


# ── Routes: Index management ────────────────────────────────────────────────────

@app.delete("/delete-index/{index_name}", dependencies=[Depends(require_admin)])
def delete_index(index_name: str) -> dict:
    """Delete an ES index entirely. Irreversible."""
    _require_knowledge_index(index_name)
    r = requests.delete(
        f"{ES_URL}/{index_name}",
        auth=ES_AUTH, verify=False, timeout=15
    )
    if r.status_code == 200:
        return {"status": "ok", "index": index_name}
    elif r.status_code == 404:
        raise HTTPException(404, f"Index '{index_name}' not found")
    else:
        raise HTTPException(500, f"Delete failed: {r.text[:200]}")


def _file_field_for_index(index_name: str) -> str:
    """Each index uses a different field to track the source file."""
    if index_name == SCRIPTS_INDEX:
        return "filename"           # keyword field
    if index_name in RFS_INDICES:
        return "source_file.keyword"  # dynamically mapped as text — need .keyword sub-field
    return "source_file"            # grp-manuals, grp-code — mapped as keyword


@app.get("/knowledge-base", dependencies=[Depends(current_user)])
def knowledge_base_summary() -> dict:
    """All indices with doc counts + file list in one call."""
    all_indices = ["grp-manuals", ACUMATICA_INDEX, SCRIPTS_INDEX, CODE_INDEX] + RFS_INDICES
    result = {}
    for idx in all_indices:
        count_r = requests.get(f"{ES_URL}/{idx}/_count",
                               auth=ES_AUTH, verify=False, timeout=5)
        doc_count = count_r.json().get("count", 0) if count_r.status_code == 200 else 0

        field = _file_field_for_index(idx)
        files_r = requests.post(
            f"{ES_URL}/{idx}/_search",
            auth=ES_AUTH, verify=False, timeout=10,
            json={"size": 0, "aggs": {"files": {"terms": {"field": field, "size": 500}}}}
        )
        files = []
        if files_r.status_code == 200:
            buckets = files_r.json().get("aggregations", {}).get("files", {}).get("buckets", [])
            files = [{"name": b["key"], "chunks": b["doc_count"]} for b in buckets]

        result[idx] = {"doc_count": doc_count, "files": files}
    return result


@app.get("/index-files/{index_name}", dependencies=[Depends(current_user)])
def list_index_files(index_name: str) -> list[dict]:
    """List unique files indexed in an index with doc counts."""
    _require_knowledge_index(index_name)
    field = _file_field_for_index(index_name)
    r = requests.post(
        f"{ES_URL}/{index_name}/_search",
        auth=ES_AUTH, verify=False, timeout=10,
        json={"size": 0, "aggs": {"files": {"terms": {"field": field, "size": 500}}}}
    )
    if r.status_code == 404:
        return []
    buckets = r.json().get("aggregations", {}).get("files", {}).get("buckets", [])
    return [{"source_file": b["key"], "doc_count": b["doc_count"]} for b in buckets]


@app.delete("/delete-file", dependencies=[Depends(require_admin)])
def delete_file_from_index(index_name: str, source_file: str) -> dict:
    """Delete all docs with matching file field from an index."""
    _require_knowledge_index(index_name)
    field = _file_field_for_index(index_name)
    # Use term on keyword field (strip .keyword suffix not needed — term works on keyword sub-fields)
    r = requests.post(
        f"{ES_URL}/{index_name}/_delete_by_query",
        auth=ES_AUTH, verify=False, timeout=30,
        json={"query": {"term": {field: source_file}}}
    )
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"Delete failed: {r.text[:200]}")
    deleted = r.json().get("deleted", 0)
    return {"status": "ok", "deleted": deleted, "source_file": source_file}


@app.get("/index-files/{index_name}/chunks", dependencies=[Depends(current_user)])
def get_file_chunks(index_name: str, source_file: str = Query(...)) -> list[dict]:
    """List individual chunks for a specific source_file in an index."""
    _require_knowledge_index(index_name)
    field = _file_field_for_index(index_name)
    r = requests.post(
        f"{ES_URL}/{index_name}/_search",
        auth=ES_AUTH, verify=False, timeout=10,
        json={
            "size": 200,
            "_source": ["module", "section", "content", "purpose",
                        "filename", "chunk_index", "relatedarea", "referno"],
            "query": {"term": {field: source_file}}
        }
    )
    if r.status_code != 200:
        raise HTTPException(500, r.text[:200])
    hits = r.json().get("hits", {}).get("hits", [])
    return [{"id": h["_id"], **h["_source"]} for h in hits]


@app.post("/reindex/{index_name}", dependencies=[Depends(require_admin)])
def reindex(index_name: str) -> dict:
    """Re-embed all docs in an index using current embed model. Runs synchronously."""
    _require_knowledge_index(index_name)
    r = requests.post(
        f"{ES_URL}/{index_name}/_search",
        auth=ES_AUTH, verify=False, timeout=15,
        json={"size": 1000, "_source": ["module", "section", "content",
                                         "purpose", "filename", "source_file"]}
    )
    if r.status_code != 200:
        raise HTTPException(500, f"Could not fetch docs: {r.text[:200]}")
    hits = r.json().get("hits", {}).get("hits", [])
    if not hits:
        return {"updated": 0, "errors": 0}

    updated, errors = 0, 0
    for hit in hits:
        s = hit["_source"]
        # Build embed text based on index type
        if index_name in ("grp-manuals", ACUMATICA_INDEX):
            text = f"{s.get('module','')} {s.get('section','')} {s.get('content','')}"[:4000]
        elif index_name in [SCRIPTS_INDEX, CODE_INDEX]:
            text = f"{s.get('purpose','')} {s.get('content','')}"[:4000]
        else:
            text = f"{s.get('relatedarea','')} {s.get('content','')}"[:4000]
        try:
            embedding = get_embedding(text)
            requests.post(
                f"{ES_URL}/{index_name}/_update/{hit['_id']}",
                auth=ES_AUTH, verify=False, timeout=30,
                json={"doc": {"embedding": embedding}}
            )
            updated += 1
        except Exception:
            errors += 1
    return {"updated": updated, "errors": errors, "index": index_name}


@app.get("/search", dependencies=[Depends(current_user)])
def search(q: str = Query(...), index: str = Query(...), size: int = 10) -> list[dict]:
    """Direct BM25 keyword search — no Claude, instant results."""
    _require_knowledge_index(index)
    all_text_fields = {
        "grp-manuals":    ["content^2", "section^3", "module^2", "image_captions"],
        ACUMATICA_INDEX:  ["content^2", "section^3", "module^2", "image_captions"],
        SCRIPTS_INDEX:    ["purpose^3", "content^2", "tables^2"],
        CODE_INDEX:       ["purpose^3", "content^2"],
    }
    # RFS indices
    for idx in RFS_INDICES:
        all_text_fields[idx] = ["notes^2", "action_summary^2", "relatedarea^3"]

    fields = all_text_fields.get(index, ["_all"])
    r = requests.post(
        f"{ES_URL}/{index}/_search",
        auth=ES_AUTH, verify=False, timeout=10,
        json={
            "size": size,
            "query": {"multi_match": {"query": q, "fields": fields, "type": "best_fields"}},
            "_source": ["module", "section", "content", "purpose", "referno",
                        "relatedarea", "notes", "action_summary", "filename"]
        }
    )
    if r.status_code != 200:
        raise HTTPException(500, r.text[:200])
    hits = r.json().get("hits", {}).get("hits", [])
    return [{"id": h["_id"], "score": round(h["_score"], 4), **h["_source"]} for h in hits]


@app.patch("/section", dependencies=[Depends(require_admin)])
def rename_section(
    module:      str = Query(...),
    old_section: str = Query(...),
    new_section: str = Query(...),
) -> dict:
    """Rename a section across all chunks in grp-manuals."""
    # Update section field on all matching docs
    r = requests.post(
        f"{ES_URL}/grp-manuals/_update_by_query",
        auth=ES_AUTH, verify=False, timeout=30,
        json={
            "script": {
                "source": "ctx._source.section = params.new_sec",
                "params": {"new_sec": new_section}
            },
            "query": {
                "bool": {
                    "filter": [{"term": {"module": module}}],
                    "must":   [{"term": {"section.keyword": old_section}}]
                }
            }
        }
    )
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"Update failed: {r.text[:200]}")
    updated = r.json().get("updated", 0)
    if updated == 0:
        raise HTTPException(404, f"No docs found for module='{module}' section='{old_section}'")

    # Also update prev_section / next_section references in adjacent chunks
    requests.post(
        f"{ES_URL}/grp-manuals/_update_by_query",
        auth=ES_AUTH, verify=False, timeout=30,
        json={
            "script": {
                "source": "ctx._source.next_section = params.new_sec",
                "params": {"new_sec": new_section}
            },
            "query": {"term": {"next_section": old_section}}
        }
    )
    requests.post(
        f"{ES_URL}/grp-manuals/_update_by_query",
        auth=ES_AUTH, verify=False, timeout=30,
        json={
            "script": {
                "source": "ctx._source.prev_section = params.new_sec",
                "params": {"new_sec": new_section}
            },
            "query": {"term": {"prev_section": old_section}}
        }
    )
    return {"updated": updated, "old_section": old_section, "new_section": new_section}


# ── Routes: Document upload ─────────────────────────────────────────────────────

@app.post("/upload-document", dependencies=[Depends(require_admin)])
async def upload_document(
    file:     UploadFile = File(...),
    doc_type: str = Form(...),   # manual | rfs | script | code | acumatica
    metadata: str = Form("{}"),
) -> dict:
    try:
        meta = json.loads(metadata)
    except Exception:
        meta = {}

    if doc_type not in DOCUMENT_UPLOAD_EXTS:
        raise HTTPException(400, f"Unknown doc_type: {doc_type}")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in DOCUMENT_UPLOAD_EXTS[doc_type]:
        raise HTTPException(
            400,
            f"doc_type '{doc_type}' requires one of "
            f"{sorted(DOCUMENT_UPLOAD_EXTS[doc_type])}, got '{ext}'",
        )

    # Cap memory: read at most MAX+1 bytes so oversized uploads fail fast
    # instead of buffering the whole body.
    content_bytes = await file.read(DOCUMENT_UPLOAD_MAX_BYTES + 1)
    if len(content_bytes) > DOCUMENT_UPLOAD_MAX_BYTES:
        raise HTTPException(
            413,
            f"File too large (max {DOCUMENT_UPLOAD_MAX_BYTES // (1024*1024)} MB)",
        )

    if doc_type == "manual":
        return _handle_manual(content_bytes, file.filename, meta)
    elif doc_type == "rfs":
        return _handle_rfs(content_bytes, file.filename, meta)
    elif doc_type == "script":
        return _handle_script(content_bytes, file.filename, meta)
    elif doc_type == "code":
        return _handle_code(content_bytes, file.filename, meta)
    elif doc_type == "acumatica":
        return _handle_acumatica(content_bytes, file.filename, meta)


# Match Images/<stem>/[media/]file regardless of any prefix (tmp dir, MD/, etc.)
_DOCX_IMG_HTML_RE = re.compile(
    r'<img\s+src="[^"]*?Images/([^"/]+)/(?:media/)?([^"]+)"[^>]*?/?>',
    re.IGNORECASE,
)
_DOCX_IMG_MD_RE = re.compile(
    r'!\[[^\]]*\]\([^)]*?Images/([^/)]+)/(?:media/)?([^)]+)\)'
)


def _docx_to_md(content_bytes: bytes, filename: str) -> str:
    """Convert uploaded .docx to clean Markdown via pandoc.
    Side effect: writes extracted images flat into IMG_DIR/<stem>/.
    Returns MD text with image refs as ![](Images/<stem>/file.png).
    """
    import tempfile, shutil
    # Derive a filesystem- and URL-safe stem. The uploaded filename is
    # attacker-controlled, so basename() it (drops any ../ or absolute path)
    # and strip to a safe character set before it is used to build
    # IMG_DIR/<stem> — otherwise extracted media could escape IMG_DIR.
    stem = re.sub(r'\.docx$', '', os.path.basename(filename or ""),
                  flags=re.IGNORECASE)
    stem = re.sub(r'[^A-Za-z0-9._-]', '_', stem).strip('._') or "doc"
    with tempfile.TemporaryDirectory() as tmp:
        docx_path = os.path.join(tmp, "in.docx")
        with open(docx_path, "wb") as f:
            f.write(content_bytes)
        md_path = os.path.join(tmp, "out.md")
        media_dir = os.path.join(tmp, "Images", stem)
        os.makedirs(media_dir, exist_ok=True)

        result = subprocess.run(
            ["pandoc", docx_path, "--from=docx", "--to=gfm",
             "--wrap=none", f"--extract-media={media_dir}",
             "-o", md_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"pandoc failed: {result.stderr[:300]}")

        media_sub = os.path.join(media_dir, "media")
        if os.path.isdir(media_sub):
            for fn in os.listdir(media_sub):
                src = os.path.join(media_sub, fn)
                dst = os.path.join(media_dir, fn)
                if os.path.exists(dst):
                    os.unlink(dst)
                shutil.move(src, dst)
            try:
                os.rmdir(media_sub)
            except OSError:
                pass

        dest_dir = os.path.join(IMG_DIR, stem)
        os.makedirs(dest_dir, exist_ok=True)
        for fn in os.listdir(media_dir):
            src = os.path.join(media_dir, fn)
            if os.path.isfile(src):
                shutil.move(src, os.path.join(dest_dir, fn))

        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()

    repl = lambda m: f"![](Images/{m.group(1)}/{m.group(2)})"
    md_text = _DOCX_IMG_HTML_RE.sub(repl, md_text)
    md_text = _DOCX_IMG_MD_RE.sub(repl, md_text)
    return md_text


def _handle_manual(content_bytes: bytes, filename: str, meta: dict) -> dict:
    if filename.lower().endswith(".docx"):
        text = _docx_to_md(content_bytes, filename)
        filename = re.sub(r'\.docx$', '.md', filename, flags=re.IGNORECASE)
    else:
        text = content_bytes.decode("utf-8", errors="replace")
    _, body = _parse_frontmatter(text)
    module = meta.get("module") or _extract_module_name(filename)
    chunks = _chunk_by_headings(body, module, filename)

    if not chunks:
        raise HTTPException(400, "No sections found in file")

    # Preview mode: return section list, do not index
    if not meta.get("confirm", False):
        preview = [
            {
                "original_section": c["section"],
                "section": c["section"],
                "content_preview": c["content"][:120],
                "image_count": len(c["images"])
            }
            for c in chunks
        ]
        return {"preview": preview, "module": module, "chunks": len(chunks)}

    # Apply section overrides from user
    overrides = meta.get("overrides", {})  # {original_section: new_section}
    module_override = meta.get("module") or module
    for c in chunks:
        if c["section"] in overrides:
            c["section"] = overrides[c["section"]]
        c["module"] = module_override

    # Delete existing chunks for this source_file
    requests.post(
        f"{ES_URL}/grp-manuals/_delete_by_query",
        auth=ES_AUTH, verify=False, timeout=30,
        json={"query": {"term": {"source_file": filename}}}
    )

    # Embed and index
    indexed, errors = 0, 0
    for chunk in chunks:
        try:
            embedding = get_embedding(chunk["_embed_text"])
            doc = {k: v for k, v in chunk.items() if not k.startswith("_")}
            doc["embedding"] = embedding
            r = requests.post(f"{ES_URL}/grp-manuals/_doc",
                              auth=ES_AUTH, verify=False, json=doc, timeout=30)
            if r.status_code in (200, 201):
                indexed += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    return {"chunks_indexed": indexed, "errors": errors, "index": "grp-manuals"}


def _handle_acumatica(content_bytes: bytes, filename: str, meta: dict) -> dict:
    """Index official Acumatica help docs into acumatica-help.
    Same chunking pipeline as _handle_manual; only the target index differs."""
    if filename.lower().endswith(".docx"):
        text = _docx_to_md(content_bytes, filename)
        filename = re.sub(r'\.docx$', '.md', filename, flags=re.IGNORECASE)
    else:
        text = content_bytes.decode("utf-8", errors="replace")
    _, body = _parse_frontmatter(text)
    module = meta.get("module") or _extract_module_name(filename)
    chunks = _chunk_by_headings(body, module, filename)

    if not chunks:
        raise HTTPException(400, "No sections found in file")

    if not meta.get("confirm", False):
        preview = [
            {
                "original_section": c["section"],
                "section": c["section"],
                "content_preview": c["content"][:120],
                "image_count": len(c["images"])
            }
            for c in chunks
        ]
        return {"preview": preview, "module": module, "chunks": len(chunks)}

    overrides = meta.get("overrides", {})
    module_override = meta.get("module") or module
    for c in chunks:
        if c["section"] in overrides:
            c["section"] = overrides[c["section"]]
        c["module"] = module_override

    requests.post(
        f"{ES_URL}/{ACUMATICA_INDEX}/_delete_by_query",
        auth=ES_AUTH, verify=False, timeout=30,
        json={"query": {"term": {"source_file": filename}}}
    )

    indexed, errors = 0, 0
    for chunk in chunks:
        try:
            embedding = get_embedding(chunk["_embed_text"])
            doc = {k: v for k, v in chunk.items() if not k.startswith("_")}
            doc["embedding"] = embedding
            r = requests.post(f"{ES_URL}/{ACUMATICA_INDEX}/_doc",
                              auth=ES_AUTH, verify=False, json=doc, timeout=30)
            if r.status_code in (200, 201):
                indexed += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    return {"chunks_indexed": indexed, "errors": errors, "index": ACUMATICA_INDEX}


def _handle_rfs(content_bytes: bytes, filename: str, meta: dict) -> dict:
    ext = os.path.splitext(filename)[1].lower()

    ACTION_TYPES = {1: "Lodge", 3: "Assign", 5: "Note",
                    10: "Close", 13: "Attach", 15: "Escalate", 20: "Edit"}

    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                raise HTTPException(400, "Empty file")
            headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
            data_rows = [dict(zip(headers, row)) for row in rows[1:]]
        except ImportError:
            raise HTTPException(500, "openpyxl not installed on server. Use CSV format.")
    elif ext == ".csv":
        text = content_bytes.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        data_rows = list(reader)
        headers = reader.fieldnames or []
    else:
        raise HTTPException(400, "RFS tickets: use .xlsx, .xls, or .csv")

    # Group by lodge_id
    tickets = defaultdict(lambda: {
        "referno": None, "relatedarea": None, "priority": None,
        "dateline": None, "notes": None, "laststatus": None,
        "lastassignee": None, "actions": [], "timestamp": None,
        "source_file": filename
    })

    for row in data_rows:
        lodge_id = row.get("lodge_id") or row.get("id")
        if not lodge_id:
            continue
        t = tickets[str(lodge_id)]
        action_type = row.get("action_type_id")
        try:
            action_type = int(action_type) if action_type is not None else None
        except (ValueError, TypeError):
            action_type = None

        if action_type == 1 or t["referno"] is None:
            for field in ["referno", "relatedarea", "priority", "dateline",
                          "notes", "laststatus", "lastassignee", "timestamp"]:
                if row.get(field) is not None:
                    t[field] = row[field]

        action = {"type": ACTION_TYPES.get(action_type, f"type_{action_type}"),
                  "note": row.get("action_note")}
        action = {k: v for k, v in action.items() if v is not None}
        t["actions"].append(action)

    # Auto-detect month from timestamps
    from collections import Counter
    month_counts = Counter()
    for t in tickets.values():
        ts = t.get("timestamp") or t.get("dateline")
        if ts and ts != "None":
            try:
                ts_str = str(ts)
                m = re.search(r'(\d{4})-(\d{2})', ts_str)
                if m:
                    month_counts[int(m.group(2))] += 1
            except Exception:
                pass

    target_index = meta.get("index")
    if not target_index:
        if month_counts:
            dominant_month = month_counts.most_common(1)[0][0]
            target_index = MONTH_INDEX_MAP.get(dominant_month, "rfs-tickets-jan-2025")
        else:
            target_index = "rfs-tickets-jan-2025"
    # Admin-supplied target must be one of the configured RFS month indices.
    if target_index not in RFS_INDICES:
        raise HTTPException(
            400,
            f"RFS target index '{target_index}' is not a configured RFS index. "
            f"Allowed: {sorted(RFS_INDICES)}",
        )

    # Ensure index exists
    r = requests.get(f"{ES_URL}/{target_index}", auth=ES_AUTH, verify=False)
    if r.status_code == 404:
        requests.put(f"{ES_URL}/{target_index}", json=RFS_MAPPING,
                     auth=ES_AUTH, verify=False)
    else:
        # Add source_file field if missing (existing indices)
        requests.put(
            f"{ES_URL}/{target_index}/_mapping",
            auth=ES_AUTH, verify=False,
            json={"properties": {"source_file": {"type": "keyword"}}},
            timeout=5
        )

    # Delete existing docs from this source file
    requests.post(
        f"{ES_URL}/{target_index}/_delete_by_query",
        auth=ES_AUTH, verify=False, timeout=30,
        json={"query": {"term": {"source_file": filename}}}
    )

    # Embed and bulk index
    indexed, errors = 0, 0
    batch = []
    BATCH = 50

    for lodge_id, ticket in tickets.items():
        ticket["lodge_id"] = lodge_id
        notes = [str(a["note"]) for a in ticket["actions"] if a.get("note")]
        ticket["action_summary"] = " | ".join(notes)

        embed_text = _build_rfs_embed_text(ticket)
        try:
            ticket["embedding"] = get_embedding(embed_text)
        except Exception:
            errors += 1
            ticket["embedding"] = None

        batch.append((str(lodge_id), target_index, ticket))
        if len(batch) >= BATCH:
            _bulk_index(batch)
            indexed += len(batch)
            batch = []

    if batch:
        _bulk_index(batch)
        indexed += len(batch)

    return {"chunks_indexed": indexed, "errors": errors, "index": target_index,
            "detected_month": dominant_month if month_counts else None}


def _handle_script(content_bytes: bytes, filename: str, meta: dict) -> dict:
    content = content_bytes.decode("utf-8", errors="replace").strip()
    if not content:
        raise HTTPException(400, "Empty file")

    purpose = meta.get("purpose") or re.sub(r'\.(txt)$', '', filename)
    tables = _extract_tables(content)
    embed_text = f"{purpose}: {content}"

    # Delete existing
    requests.post(
        f"{ES_URL}/{SCRIPTS_INDEX}/_delete_by_query",
        auth=ES_AUTH, verify=False, timeout=10,
        json={"query": {"term": {"filename": filename}}}
    )

    embedding = get_embedding(embed_text)
    doc = {
        "script_name": purpose, "filename": filename, "purpose": purpose,
        "content": content, "tables": tables, "is_template": False,
        "embedding": embedding
    }
    r = requests.post(f"{ES_URL}/{SCRIPTS_INDEX}/_doc",
                      auth=ES_AUTH, verify=False, json=doc, timeout=30)
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"ES index failed: {r.text[:200]}")

    return {"chunks_indexed": 1, "errors": 0, "index": SCRIPTS_INDEX}


def _handle_code(content_bytes: bytes, filename: str, meta: dict) -> dict:
    content = content_bytes.decode("utf-8", errors="replace").strip()
    if not content:
        raise HTTPException(400, "Empty file")

    ext = os.path.splitext(filename)[1].lower()
    lang_map = {".py": "python", ".cs": "csharp", ".sql": "sql"}
    language = lang_map.get(ext, "unknown")
    purpose = meta.get("purpose") or re.sub(r'\.[^.]+$', '', filename)
    tables = _extract_tables(content)
    embed_text = f"{purpose}: {content}"

    # Delete existing
    requests.post(
        f"{ES_URL}/{CODE_INDEX}/_delete_by_query",
        auth=ES_AUTH, verify=False, timeout=10,
        json={"query": {"term": {"source_file": filename}}}
    )

    embedding = get_embedding(embed_text)
    doc = {
        "script_name": purpose, "filename": filename, "source_file": filename,
        "purpose": purpose, "content": content, "tables": tables,
        "language": language, "embedding": embedding
    }
    r = requests.post(f"{ES_URL}/{CODE_INDEX}/_doc",
                      auth=ES_AUTH, verify=False, json=doc, timeout=30)
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"ES index failed: {r.text[:200]}")

    return {"chunks_indexed": 1, "errors": 0, "index": CODE_INDEX}


# ── Routes: Chat history ────────────────────────────────────────────────────────

class ChatCreate(BaseModel):
    title: str | None = None

class ChatUpdate(BaseModel):
    title: str | None = None
    messages: list[dict] | None = None


def _owner_filter(user: dict) -> dict | None:
    """Return ES term filter for the user's owner field, or None for admins (sees all)."""
    if user.get("role") == "admin":
        return None
    return {"term": {"owner": user["email"]}}


def _fetch_chat_doc(cid: str) -> dict | None:
    r = requests.get(f"{ES_URL}/{CHATS_INDEX}/_doc/{cid}",
                     auth=ES_AUTH, verify=False, timeout=10)
    if r.status_code != 200:
        return None
    return r.json().get("_source", {})


def _assert_chat_access(cid: str, user: dict) -> dict:
    src = _fetch_chat_doc(cid)
    if src is None:
        raise HTTPException(404, "Chat not found")
    if user.get("role") != "admin" and src.get("owner") != user["email"]:
        # Hide existence: 404, not 403
        raise HTTPException(404, "Chat not found")
    return src


@app.get("/chats")
def chats_list(user: dict = Depends(current_user)) -> list[dict]:
    query: dict = {"match_all": {}}
    of = _owner_filter(user)
    if of is not None:
        query = {"bool": {"filter": [of]}}
    body = {
        "query": query,
        "size": 200,
        "sort": [{"updated_at": {"order": "desc"}}],
        "_source": ["title", "owner", "created_at", "updated_at"],
    }
    r = requests.post(f"{ES_URL}/{CHATS_INDEX}/_search",
                      auth=ES_AUTH, verify=False, json=body, timeout=10)
    if r.status_code != 200:
        return []
    hits = r.json().get("hits", {}).get("hits", [])
    return [{"id": h["_id"], **h["_source"]} for h in hits]


@app.post("/chats")
def chats_create(req: ChatCreate, user: dict = Depends(current_user)) -> dict:
    cid = uuid.uuid4().hex[:12]
    now = int(_time.time() * 1000)
    doc = {
        "title":      req.title or "New chat",
        "owner":      user["email"],
        "created_at": now,
        "updated_at": now,
        "messages":   [],
    }
    r = requests.put(
        f"{ES_URL}/{CHATS_INDEX}/_doc/{cid}?refresh=wait_for",
        auth=ES_AUTH, verify=False, json=doc, timeout=10
    )
    if r.status_code not in (200, 201):
        raise HTTPException(500, "Failed to create chat")
    return {"id": cid, **doc}


@app.get("/chats/{cid}")
def chats_get(cid: str, user: dict = Depends(current_user)) -> dict:
    src = _assert_chat_access(cid, user)
    return {"id": cid, **src}


@app.put("/chats/{cid}")
def chats_update(cid: str, req: ChatUpdate, user: dict = Depends(current_user)) -> dict:
    _assert_chat_access(cid, user)
    now = int(_time.time() * 1000)
    doc = {"updated_at": now}
    if req.title is not None:
        doc["title"] = req.title
    if req.messages is not None:
        doc["messages"] = req.messages
    r = requests.post(
        f"{ES_URL}/{CHATS_INDEX}/_update/{cid}?refresh=wait_for",
        auth=ES_AUTH, verify=False, json={"doc": doc}, timeout=10
    )
    if r.status_code == 404:
        raise HTTPException(404, "Chat not found")
    if r.status_code not in (200, 201):
        raise HTTPException(500, "Failed to update chat")
    return {"id": cid, "updated_at": now}


@app.delete("/chats/{cid}")
def chats_delete(cid: str, user: dict = Depends(current_user)) -> dict:
    _assert_chat_access(cid, user)
    r = requests.delete(
        f"{ES_URL}/{CHATS_INDEX}/_doc/{cid}?refresh=wait_for",
        auth=ES_AUTH, verify=False, timeout=10
    )
    if r.status_code == 404:
        raise HTTPException(404, "Chat not found")
    if r.status_code not in (200, 201):
        raise HTTPException(500, "Failed to delete chat")
    return {"ok": True}

