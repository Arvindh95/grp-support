#!/usr/bin/env python3
"""
RFS Ticket Loader with Embeddings
Groups rows by ticket (lodge_id) -> 1 doc per ticket -> embed -> push ES
Run on server: python3 load_rfs_embed.py
"""

import xlrd, json, time, sys, re, requests
from pathlib import Path
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────────────────
ES_URL      = "https://localhost:9200"
ES_AUTH     = ("elastic", "W1iUd3PBH2qvhEcTc9mR")
ES_VERIFY   = False
OLLAMA_URL  = "http://localhost:11434"
EMBED_MODEL = "bge-m3"

DATA_DIR = Path("/opt/rfs-data")
FILES = [
    ("backupJan2025.xls", "rfs-tickets-jan-2025"),
    ("backupFeb2025.xls", "rfs-tickets-feb-2025"),
    ("backupMar2025.xls", "rfs-tickets-mar-2025"),
]

ACTION_TYPES = {
    1: "Lodge", 3: "Assign", 5: "Note",
    10: "Close", 13: "Attach", 15: "Escalate", 20: "Edit",
}

# ── ES Mapping ─────────────────────────────────────────────────────────────────
MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "lodge_id":      {"type": "keyword"},
            "referno":       {"type": "keyword"},
            "branch_id":     {"type": "keyword"},
            "timestamp":     {"type": "date", "ignore_malformed": True},
            "serviceid":     {"type": "keyword"},
            "clientid":      {"type": "keyword"},
            "projectid":     {"type": "keyword"},
            "probtypeid":    {"type": "keyword"},
            "probareaid":    {"type": "keyword"},
            "relatedarea":   {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "indicatorid":   {"type": "keyword"},
            "priority":      {"type": "integer"},
            "dateline":      {"type": "date", "ignore_malformed": True},
            "notes":         {"type": "text"},
            "userid":        {"type": "keyword"},
            "contactname":   {"type": "text"},
            "contactno":     {"type": "keyword"},
            "contactemail":  {"type": "keyword"},
            "kakireport":    {"type": "keyword"},
            "laststatus":    {"type": "integer"},
            "laststatusdate":{"type": "date", "ignore_malformed": True},
            "lastassignee":  {"type": "keyword"},
            "actions":       {"type": "nested"},
            "action_summary":{"type": "text"},   # all action notes merged for search
            "embedding": {
                "type": "dense_vector",
                "dims": 1024,
                "index": True,
                "similarity": "cosine"
            }
        }
    }
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def clean_val(val):
    if val is None or val == "":
        return None
    if isinstance(val, float) and val == int(val):
        return int(val)
    return val


def build_embed_text(ticket: dict) -> str:
    """
    Build semantic embedding text from ticket.
    Combines: relatedarea + problem notes + key action notes.
    Capped at 280 chars (model limit).
    """
    parts = []
    if ticket.get("relatedarea"):
        parts.append(str(ticket["relatedarea"]))
    if ticket.get("notes"):
        parts.append(str(ticket["notes"])[:150])
    # Include first meaningful action note (lodge or first note)
    for action in ticket.get("actions", []):
        note = action.get("note")
        if note and len(str(note)) > 10:
            parts.append(str(note)[:80])
            break
    text = ". ".join(parts)
    # Strip non-printable, collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:4000]


def get_embedding(text: str) -> list:
    if not text or len(text) < 5:
        return None
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60
    )
    r.raise_for_status()
    return r.json()["embedding"]


def load_xls(filepath: Path) -> dict:
    wb = xlrd.open_workbook(str(filepath))
    ws = wb.sheet_by_index(0)

    # Parse headers — handle duplicate 'id'
    raw_headers = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]
    seen = {}
    col_names = []
    for h in raw_headers:
        if h in seen:
            seen[h] += 1
            col_names.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            col_names.append(h)

    tickets = defaultdict(lambda: {
        "referno": None, "branch_id": None, "timestamp": None,
        "serviceid": None, "clientid": None, "projectid": None,
        "probtypeid": None, "probareaid": None, "relatedarea": None,
        "indicatorid": None, "priority": None, "dateline": None,
        "notes": None, "userid": None, "contactname": None,
        "contactno": None, "contactemail": None, "kakireport": None,
        "laststatus": None, "laststatusdate": None, "lastassignee": None,
        "actions": []
    })

    print(f"  Reading {ws.nrows - 1} rows...")
    for row_idx in range(1, ws.nrows):
        row = {col_names[c]: clean_val(ws.cell_value(row_idx, c)) for c in range(ws.ncols)}
        lodge_id = row.get("lodge_id") or row.get("id")
        if not lodge_id:
            continue

        t = tickets[lodge_id]
        action_type = row.get("action_type_id")

        if action_type == 1 or t["referno"] is None:
            for field in ["referno", "branch_id", "timestamp", "serviceid", "clientid",
                          "projectid", "probtypeid", "probareaid", "relatedarea",
                          "indicatorid", "priority", "dateline", "notes", "userid",
                          "contactname", "contactno", "contactemail", "kakireport",
                          "laststatus", "laststatusdate", "lastassignee"]:
                if row.get(field) is not None:
                    t[field] = row[field]

        action = {
            "type":           ACTION_TYPES.get(action_type, f"type_{action_type}"),
            "action_type_id": action_type,
            "time":           row.get("action_time"),
            "assignee_id":    row.get("assignee_id"),
            "assigner_id":    row.get("assigner_id"),
            "note":           row.get("action_note"),
            "allow_view":     row.get("allow_view"),
        }
        action = {k: v for k, v in action.items() if v is not None}
        t["actions"].append(action)

    print(f"  Unique tickets: {len(tickets)}")
    return tickets


def process_file(filepath: Path, index_name: str):
    print(f"\n{'='*60}")
    print(f"Processing: {filepath.name} -> {index_name}")

    tickets = load_xls(filepath)

    # Delete + recreate index
    requests.delete(f"{ES_URL}/{index_name}", auth=ES_AUTH, verify=ES_VERIFY)
    r = requests.put(f"{ES_URL}/{index_name}", json=MAPPING, auth=ES_AUTH, verify=ES_VERIFY)
    if r.status_code not in (200, 201):
        print(f"ERROR creating index: {r.text[:200]}")
        return 0

    total = len(tickets)
    done = 0
    errors = 0
    batch_docs = []
    BATCH_SIZE = 50

    for lodge_id, ticket in tickets.items():
        ticket["lodge_id"] = lodge_id

        # Build action_summary: all action notes merged for full-text search
        notes = [str(a["note"]) for a in ticket["actions"] if a.get("note")]
        ticket["action_summary"] = " | ".join(notes)

        # Embedding
        embed_text = build_embed_text(ticket)
        try:
            embedding = get_embedding(embed_text)
            ticket["embedding"] = embedding
        except Exception as e:
            errors += 1
            ticket["embedding"] = None
            print(f"  [{done+1}/{total}] embed error: {e}")

        batch_docs.append((str(lodge_id), index_name, ticket))

        # Flush batch
        if len(batch_docs) >= BATCH_SIZE:
            flush_batch(batch_docs)
            done += len(batch_docs)
            batch_docs = []
            print(f"  Indexed {done}/{total} tickets... ({errors} embed errors)")

    if batch_docs:
        flush_batch(batch_docs)
        done += len(batch_docs)

    print(f"  Done. {done} tickets indexed, {errors} embed errors.")
    return done


def flush_batch(batch_docs: list):
    lines = []
    for doc_id, index_name, doc in batch_docs:
        lines.append(json.dumps({"index": {"_index": index_name, "_id": doc_id}}))
        lines.append(json.dumps(doc))
    body = "\n".join(lines) + "\n"
    r = requests.post(
        f"{ES_URL}/_bulk",
        data=body.encode(),
        headers={"Content-Type": "application/x-ndjson"},
        auth=ES_AUTH,
        verify=ES_VERIFY
    )
    if r.status_code not in (200, 201):
        print(f"  BULK ERROR: {r.text[:200]}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import urllib3
    urllib3.disable_warnings()

    print("=== RFS Ticket Loader with Embeddings ===\n")

    total_all = 0
    for filename, index_name in FILES:
        filepath = DATA_DIR / filename
        if not filepath.exists():
            print(f"SKIP: {filepath} not found")
            continue
        total_all += process_file(filepath, index_name)

    print(f"\n{'='*60}")
    print(f"ALL DONE. Total tickets: {total_all}")

    # Summary
    for _, index_name in FILES:
        r = requests.get(f"{ES_URL}/{index_name}/_count", auth=ES_AUTH, verify=ES_VERIFY)
        count = r.json().get("count", "?")
        print(f"  {index_name}: {count} docs")


if __name__ == "__main__":
    main()
