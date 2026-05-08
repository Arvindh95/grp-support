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
JWT_TTL_HOURS = int(os.environ.get("JWT_TTL_HOURS", "12"))
IMG_BASE    = os.environ.get("IMG_BASE",    "http://173.212.247.3:8080")
IMG_DIR     = os.environ.get("IMG_DIR",     "/opt/grp-manuals/Doc-Images")

CHAT_UPLOAD_DIR = os.environ.get("CHAT_UPLOAD_DIR", "/tmp/grp-chat")
os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
CHAT_UPLOAD_EXTS = {".pdf", ".md", ".txt", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".csv"}
CHAT_UPLOAD_MAX_BYTES = 25 * 1024 * 1024

RFS_INDICES = [
    "rfs-tickets-jan-2025",
    "rfs-tickets-feb-2025",
    "rfs-tickets-mar-2025",
]
SCRIPTS_INDEX = "grp-scripts"
CODE_INDEX    = "grp-code"
CHATS_INDEX   = "grp-chats"
USERS_INDEX   = "grp-users"

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

=== MANDATORY SEARCH PROTOCOL ===
Before answering, you MUST search. Follow this order:

1. Search grp-manuals for procedure / how-to questions
2. Search ALL THREE ticket indices (jan, feb, mar) for any past similar problems
   - Use multi_match on notes, action_summary, relatedarea
   - If first index has few results, still check the others
3. Search grp-scripts if the question involves a data issue, system error, or fix request
4. Search grp-code if question involves code logic, customization, or scripts
5. If first search returns 0-1 results, RETRY with:
   - Alternative keywords (synonyms, Malay/English equivalents)
   - Specific proper nouns mentioned (e.g. "JomPay", "TNB", bank names)
   - Screen codes if applicable (AP301000, PR201000, GL102000, etc.)
6. Minimum: attempt at least 3 MCP searches per query before synthesizing answer

7. CROSS-LINK TICKETS ↔ MANUALS: RFS tickets do not store screenshots, but the manual does. When a ticket mentions a screen code (e.g. AP303000) or a procedure name (e.g. "vendor registration", "void payment", "credit memo"), you MUST also search grp-manuals for that screen_code or section. Embed the manual's inline screenshots in your troubleshooting answer so the user sees the screen they are working on. Examples:
   - Ticket about a vendor registration bug → search grp-manuals: { "query": { "match": { "section": "Daftar Pembekal" } } } and inline its screenshots.
   - Ticket mentions AP301000 → search grp-manuals: { "query": { "term": { "screen_codes": "AP301000" } } } and inline its screenshots.
   This cross-linking is mandatory whenever a ticket references a screen or procedure that exists in grp-manuals.

8. SIBLING-FETCH FOR PARTIAL SECTIONS: Manual sections are split into smaller chunks. If a grp-manuals hit has `subsection` containing "(part N/M)" — meaning it is one piece of a larger procedure — you MUST fetch ALL siblings to assemble the full procedure before answering. Run:
   {
     "query": { "term": { "section.keyword": "<exact section name from hit>" } },
     "sort": [{ "chunk_index": "asc" }],
     "size": 30,
     "_source": ["module","section","subsection","content","images","image_captions","screen_codes"]
   }
   Stitch the parts in order (part 1/M, 2/M, ...) into one continuous procedure. Inline images stay where they appear in each part. Without sibling-fetch, your answer will be incomplete and may skip steps or screenshots.

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
{"manuals":[{"module":"MODULE_NAME","section":"SECTION_NAME"}],"tickets":[{"referno":"REFNO","index":"rfs-tickets-jan-2025"}],"scripts":[{"purpose":"PURPOSE"}]}
```

Only include sources you actually used. Empty arrays [] if none. This block is hidden from the user.
"""

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="GRP Support AI API", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8501",
        "http://localhost:8501",
        "http://173.212.247.3:8081",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """Ensure auxiliary indices exist on startup."""
    for idx, mapping in [(CODE_INDEX, CODE_MAPPING), (CHATS_INDEX, CHATS_MAPPING), (USERS_INDEX, USERS_MAPPING)]:
        r = requests.get(f"{ES_URL}/{idx}", auth=ES_AUTH, verify=False)
        if r.status_code == 404:
            requests.put(f"{ES_URL}/{idx}", json=mapping,
                         auth=ES_AUTH, verify=False)
            print(f"Created index: {idx}")


# ── Auth ───────────────────────────────────────────────────────────────────────
import bcrypt
import jwt as _jwt
import datetime as _dt
import time as _time
from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def make_token(email: str, role: str) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub":  email,
        "role": role,
        "iat":  int(now.timestamp()),
        "exp":  int((now + _dt.timedelta(hours=JWT_TTL_HOURS)).timestamp()),
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


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


def current_user(token: str | None = Depends(oauth2_scheme)) -> dict:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    try:
        payload = _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except _jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return {"email": payload["sub"], "role": payload.get("role", "user")}


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


class LoginReq(BaseModel):
    email:    str
    password: str


class RegisterReq(BaseModel):
    email:    str
    password: str
    name:     str = ""
    role:     str = "user"


@app.post("/auth/login")
def auth_login(req: LoginReq) -> dict:
    user = get_user_by_email(req.email)
    if not user or not verify_password(req.password, user.get("password_hash", "")):
        raise HTTPException(401, "Invalid credentials")
    role = user.get("role", "user")
    return {
        "access_token": make_token(req.email, role),
        "token_type":   "bearer",
        "email":        req.email,
        "role":         role,
        "name":         user.get("name", ""),
    }


@app.get("/auth/me")
def auth_me(user: dict = Depends(current_user)) -> dict:
    return user


@app.post("/auth/register", dependencies=[Depends(require_admin)])
def auth_register(req: RegisterReq) -> dict:
    if get_user_by_email(req.email):
        raise HTTPException(409, "User already exists")
    role = req.role if req.role in ("user", "admin") else "user"
    doc = {
        "email":         req.email,
        "password_hash": hash_password(req.password),
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
    return {"ok": True, "email": req.email, "role": role}


class ChangePasswordReq(BaseModel):
    old_password: str
    new_password: str


@app.post("/auth/change-password")
def auth_change_password(req: ChangePasswordReq, user: dict = Depends(current_user)) -> dict:
    src = get_user_by_email(user["email"])
    if not src or not verify_password(req.old_password, src.get("password_hash", "")):
        raise HTTPException(401, "Invalid credentials")
    if len(req.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
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


def _slugify(text: str, max_len: int = 30) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '_', text.lower()).strip('_')
    return s[:max_len]


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
        img_match = re.search(r'!\[.*?\]\(.*?/(.*?)\)', line)
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


def _find_section_doc(module: str, section: str) -> tuple[str | None, dict | None]:
    """Find ES doc _id and source for a given module+section."""
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
    r = requests.post(f"{ES_URL}/grp-manuals/_search",
                      auth=ES_AUTH, verify=False, json=body, timeout=5)
    hits = r.json().get("hits", {}).get("hits", [])
    if hits:
        return hits[0]["_id"], hits[0]["_source"]
    # Fallback: match only on section
    body["query"] = {"match_phrase": {"section": section}}
    del body["query"]
    body["query"] = {"match": {"section": section}}
    r = requests.post(f"{ES_URL}/grp-manuals/_search",
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
    manual_hits = _knn_search("grp-manuals", embedding, seed_k, MANUAL_FIELDS)
    script_hits = _knn_search(SCRIPTS_INDEX, embedding, seed_k, SCRIPT_FIELDS)
    code_hits   = _knn_search(CODE_INDEX, embedding, seed_k, CODE_FIELDS)

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

    seed_count = len(manual_hits) + len(ticket_hits) + len(script_hits) + len(code_hits)
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

_anthropic = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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


def _tool_es_search(input_: dict) -> str:
    r = requests.post(
        f"{ES_URL}/{input_['index']}/_search",
        auth=ES_AUTH, verify=False, json=input_["body"], timeout=30,
    )
    return r.text


def _tool_es_list_indices(_: dict) -> str:
    r = requests.get(f"{ES_URL}/_cat/indices?format=json",
                     auth=ES_AUTH, verify=False, timeout=10)
    return r.text


def _tool_es_get_mappings(input_: dict) -> str:
    r = requests.get(f"{ES_URL}/{input_['index']}/_mapping",
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


def call_claude_agent(question: str, seed_context: str,
                      history: list = None,
                      attached_files: list = None) -> str:
    history_text = format_history(history or [])
    safe_files = [p for p in (attached_files or [])
                  if isinstance(p, str) and p.startswith(CHAT_UPLOAD_DIR + "/") and os.path.exists(p)]
    files_block = ""
    if safe_files:
        chunks = []
        for p in safe_files:
            chunks.append(f"--- FILE: {os.path.basename(p)} ---\n"
                          + _read_attached_file_text(p))
        files_block = (
            "USER ATTACHED FILES — content embedded below; cite alongside knowledge-base sources:\n\n"
            + "\n\n".join(chunks) + "\n\n"
        )

    user_text = (
        f"{history_text}"
        f"{files_block}"
        f"INITIAL CONTEXT (kNN semantic seed — treat as incomplete starting point):\n"
        f"{seed_context}\n\n"
        f"USER QUESTION: {question}\n\n"
        f"Now search Elasticsearch via the available tools, then answer."
    )

    messages: list = [{"role": "user", "content": user_text}]

    try:
        for _step in range(_MAX_TOOL_ITERS):
            resp = _anthropic.messages.create(
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
            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        out = _run_tool(block.name, block.input or {})
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": block.id,
                            "content":     out,
                        })
                messages.append({"role": "user", "content": tool_results})
                continue
            # end_turn / stop_sequence / max_tokens — assemble final text
            text = "".join(b.text for b in resp.content if b.type == "text")
            return text or "No response from Claude."
        return "Tool loop exceeded max iterations — partial answer unavailable."
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


def fetch_images_for_sections(manuals: list[dict]) -> list[dict]:
    images = []
    seen = set()
    for ref in manuals:
        module = ref.get("module", "")
        section = ref.get("section", "")
        if not section:
            continue
        try:
            doc_id, src = _find_section_doc(module, section)
            if src:
                img_list = src.get("images", [])
                cap_list = src.get("image_captions", [])
                for i, fname in enumerate(img_list[:6]):
                    if fname in seen:
                        continue
                    seen.add(fname)
                    img_path = fname.replace("Doc-Images/", "", 1)
                    images.append({
                        "url":     f"{IMG_BASE}/{img_path}",
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
    for idx in ["grp-manuals", SCRIPTS_INDEX, CODE_INDEX] + RFS_INDICES:
        try:
            r = requests.get(f"{ES_URL}/{idx}/_count", auth=ES_AUTH,
                             verify=False, timeout=5)
            result[idx] = r.json().get("count", 0)
        except Exception:
            result[idx] = -1
    return result


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(current_user)])
def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Empty question")

    try:
        embedding = get_embedding(req.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    seed_context, seed_count = get_seed_context(embedding)
    raw_answer = call_claude_agent(req.question, seed_context, req.history, req.attached_files)

    clarify_match = re.match(r'^CLARIFY:\s*(.+)', raw_answer.strip(), re.DOTALL)
    if clarify_match:
        return QueryResponse(
            answer=clarify_match.group(1).strip(),
            images=[], sources=[], context_used=0,
            expanded_query="clarification_needed"
        )

    answer, sources_dict = parse_sources_block(raw_answer)
    images  = fetch_images_for_sections(sources_dict.get("manuals", [])) if req.include_images else []
    sources = build_sources_from_dict(sources_dict)

    return QueryResponse(
        answer=answer, images=images, sources=sources,
        context_used=seed_count, expanded_query=None
    )


# ── Routes: Module / Section lookup ────────────────────────────────────────────

@app.get("/modules", dependencies=[Depends(current_user)])
def get_modules() -> list[str]:
    r = requests.post(
        f"{ES_URL}/grp-manuals/_search",
        auth=ES_AUTH, verify=False, timeout=10,
        json={"size": 0, "aggs": {"mods": {"terms": {"field": "module", "size": 100}}}}
    )
    buckets = r.json().get("aggregations", {}).get("mods", {}).get("buckets", [])
    return sorted(b["key"] for b in buckets)


@app.get("/sections", dependencies=[Depends(current_user)])
def get_sections(module: str = Query(...)) -> list[str]:
    r = requests.post(
        f"{ES_URL}/grp-manuals/_search",
        auth=ES_AUTH, verify=False, timeout=10,
        json={
            "size": 0,
            "query": {"term": {"module": module}},
            "aggs": {"secs": {"terms": {"field": "section.keyword", "size": 300}}}
        }
    )
    buckets = r.json().get("aggregations", {}).get("secs", {}).get("buckets", [])
    return sorted(b["key"] for b in buckets)


@app.get("/section-images", dependencies=[Depends(current_user)])
def get_section_images(module: str = Query(...), section: str = Query(...)) -> dict:
    doc_id, src = _find_section_doc(module, section)
    if not src:
        return {"doc_id": None, "images": []}
    img_list = src.get("images", [])
    cap_list = src.get("image_captions", [])
    images = []
    for i, fname in enumerate(img_list):
        img_path = fname.replace("Doc-Images/", "", 1)
        images.append({
            "filename": fname,
            "url": f"{IMG_BASE}/{img_path}",
            "caption": cap_list[i] if i < len(cap_list) else ""
        })
    return {"doc_id": doc_id, "images": images}


# ── Routes: Chat file upload (one-shot, Claude reads at query time) ────────────

@app.post("/upload-chat-file", dependencies=[Depends(current_user)])
async def upload_chat_file(file: UploadFile = File(...)) -> dict:
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in CHAT_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported type: {ext}")

    data = await file.read()
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

    return {"path": saved, "name": file.filename, "size": len(data)}


# ── Routes: Image upload / delete ──────────────────────────────────────────────

@app.post("/upload-image", dependencies=[Depends(require_admin)])
async def upload_image(
    file:    UploadFile = File(...),
    module:  str = Form(...),
    section: str = Form(...),
    caption: str = Form(""),
) -> dict:
    # Validate image type
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        raise HTTPException(400, "Only PNG/JPG/JPEG/GIF/WEBP allowed")

    # Build filename
    mod_slug = _slugify(module, 20)
    sec_slug = _slugify(section, 30)
    uid = uuid.uuid4().hex[:8]
    filename = f"{mod_slug}_{sec_slug}_{uid}{ext}"
    dest_path = os.path.join(IMG_DIR, filename)

    # Save to disk
    contents = await file.read()
    with open(dest_path, "wb") as f:
        f.write(contents)

    # Find ES doc and patch arrays via Painless script
    doc_id, _ = _find_section_doc(module, section)
    if not doc_id:
        os.remove(dest_path)
        raise HTTPException(404, f"Section '{section}' not found in module '{module}'")

    script = {
        "script": {
            "source": (
                "if (ctx._source.images == null) { ctx._source.images = []; }"
                "ctx._source.images.add(params.fname);"
                "if (ctx._source.image_captions == null) { ctx._source.image_captions = []; }"
                "ctx._source.image_captions.add(params.cap);"
            ),
            "params": {"fname": filename, "cap": caption}
        }
    }
    r = requests.post(
        f"{ES_URL}/grp-manuals/_update/{doc_id}",
        auth=ES_AUTH, verify=False, json=script, timeout=10
    )
    if r.status_code not in (200, 201):
        raise HTTPException(500, f"ES update failed: {r.text[:200]}")

    img_url = f"{IMG_BASE}/{filename}"
    return {"status": "ok", "filename": filename, "url": img_url}


@app.delete("/delete-image", dependencies=[Depends(require_admin)])
def delete_image(module: str, section: str, filename: str) -> dict:
    doc_id, _ = _find_section_doc(module, section)
    if not doc_id:
        raise HTTPException(404, f"Section '{section}' not found")

    script = {
        "script": {
            "source": (
                "int i = ctx._source.images.indexOf(params.fname);"
                "if (i >= 0) {"
                "  ctx._source.images.remove(i);"
                "  if (ctx._source.image_captions != null && ctx._source.image_captions.size() > i) {"
                "    ctx._source.image_captions.remove(i);"
                "  }"
                "}"
            ),
            "params": {"fname": filename}
        }
    }
    requests.post(f"{ES_URL}/grp-manuals/_update/{doc_id}",
                  auth=ES_AUTH, verify=False, json=script, timeout=10)

    # Delete file from disk
    dest_path = os.path.join(IMG_DIR, filename.replace("Doc-Images/", ""))
    try:
        os.remove(dest_path)
    except FileNotFoundError:
        pass

    return {"status": "ok", "filename": filename}


# ── Routes: Index management ────────────────────────────────────────────────────

@app.delete("/delete-index/{index_name}", dependencies=[Depends(require_admin)])
def delete_index(index_name: str) -> dict:
    """Delete an ES index entirely. Irreversible."""
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
    all_indices = ["grp-manuals", SCRIPTS_INDEX, CODE_INDEX] + RFS_INDICES
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
        if index_name == "grp-manuals":
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
    all_text_fields = {
        "grp-manuals":    ["content^2", "section^3", "module^2", "image_captions"],
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


@app.post("/upload-image-bulk", dependencies=[Depends(require_admin)])
async def upload_image_bulk(
    files:   list[UploadFile] = File(...),
    module:  str = Form(...),
    section: str = Form(...),
    captions: str = Form("[]"),   # JSON array of captions, positionally matched
) -> dict:
    """Upload multiple images to a section at once."""
    try:
        cap_list = json.loads(captions)
    except Exception:
        cap_list = []

    doc_id, _ = _find_section_doc(module, section)
    if not doc_id:
        raise HTTPException(404, f"Section '{section}' not found in module '{module}'")

    uploaded = []
    errors = []
    for i, file in enumerate(files):
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            errors.append(f"{file.filename}: unsupported type")
            continue
        mod_slug = _slugify(module, 20)
        sec_slug = _slugify(section, 30)
        uid = uuid.uuid4().hex[:8]
        filename = f"{mod_slug}_{sec_slug}_{uid}{ext}"
        dest_path = os.path.join(IMG_DIR, filename)
        caption = cap_list[i] if i < len(cap_list) else ""
        try:
            contents = await file.read()
            with open(dest_path, "wb") as f:
                f.write(contents)
            script = {
                "script": {
                    "source": (
                        "if (ctx._source.images == null) { ctx._source.images = []; }"
                        "ctx._source.images.add(params.fname);"
                        "if (ctx._source.image_captions == null) { ctx._source.image_captions = []; }"
                        "ctx._source.image_captions.add(params.cap);"
                    ),
                    "params": {"fname": filename, "cap": caption}
                }
            }
            requests.post(f"{ES_URL}/grp-manuals/_update/{doc_id}",
                          auth=ES_AUTH, verify=False, json=script, timeout=10)
            uploaded.append({"filename": filename, "url": f"{IMG_BASE}/{filename}", "caption": caption})
        except Exception as e:
            errors.append(f"{file.filename}: {e}")

    return {"uploaded": uploaded, "errors": errors, "total": len(uploaded)}


@app.post("/upload-images-zip", dependencies=[Depends(require_admin)])
async def upload_images_zip(file: UploadFile = File(...)) -> dict:
    """Bulk upload images by zip. Extracts into IMG_DIR preserving folder structure.
    Existing files are overwritten (re-upload safe). Path traversal blocked."""
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Only .zip files accepted")

    import zipfile
    data = await file.read()
    allowed_ext = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")
    extracted, skipped = 0, 0
    skipped_names = []
    img_dir_abs = os.path.abspath(IMG_DIR)

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Not a valid zip file")

    with zf:
        for member in zf.namelist():
            if member.endswith("/"):
                continue
            safe = member.replace("\\", "/").lstrip("/")
            ext = os.path.splitext(safe)[1].lower()
            if ext not in allowed_ext:
                skipped += 1
                skipped_names.append(safe)
                continue
            target = os.path.abspath(os.path.join(IMG_DIR, safe))
            if not target.startswith(img_dir_abs + os.sep) and target != img_dir_abs:
                skipped += 1
                skipped_names.append(f"{safe} (path traversal blocked)")
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted += 1

    return {
        "extracted": extracted,
        "skipped": skipped,
        "skipped_examples": skipped_names[:10],
        "img_dir": IMG_DIR,
    }


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
    doc_type: str = Form(...),   # manual | rfs | script | code
    metadata: str = Form("{}"),
) -> dict:
    try:
        meta = json.loads(metadata)
    except Exception:
        meta = {}

    content_bytes = await file.read()

    if doc_type == "manual":
        return _handle_manual(content_bytes, file.filename, meta)
    elif doc_type == "rfs":
        return _handle_rfs(content_bytes, file.filename, meta)
    elif doc_type == "script":
        return _handle_script(content_bytes, file.filename, meta)
    elif doc_type == "code":
        return _handle_code(content_bytes, file.filename, meta)
    else:
        raise HTTPException(400, f"Unknown doc_type: {doc_type}")


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
    stem = re.sub(r'\.docx$', '', filename, flags=re.IGNORECASE)
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
        raise HTTPException(500, f"Create failed: {r.text[:200]}")
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
        raise HTTPException(500, f"Update failed: {r.text[:200]}")
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
        raise HTTPException(500, f"Delete failed: {r.text[:200]}")
    return {"ok": True}

