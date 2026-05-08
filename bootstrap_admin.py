"""
One-shot setup for the auth system.

  - Creates the initial admin user (idempotent: skips if already exists).
  - Backfills `owner` on existing chats so the admin sees them after auth lands.

Usage on VPS:
  cd /opt/grp-chat
  ADMIN_EMAIL=arvindh@censof.com ADMIN_PASSWORD='ChangeMe123' \
      python3 bootstrap_admin.py

Reads ES_USER / ES_PASSWORD / ES_URL from environment (same as api_server).
"""
import os, sys, time, requests, urllib3, bcrypt

urllib3.disable_warnings()

ES_URL  = os.environ.get("ES_URL", "https://localhost:9200")
ES_AUTH = (os.environ.get("ES_USER", "elastic"), os.environ["ES_PASSWORD"])

USERS_INDEX = "grp-users"
CHATS_INDEX = "grp-chats"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def ensure_admin(email: str, password: str, name: str = "Admin") -> None:
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_search",
        auth=ES_AUTH, verify=False,
        json={"query": {"term": {"email": email}}, "size": 1},
        timeout=10,
    )
    if r.status_code == 200 and r.json().get("hits", {}).get("hits"):
        print(f"[skip] user already exists: {email}")
        return
    doc = {
        "email": email,
        "password_hash": hash_password(password),
        "name": name,
        "role": "admin",
        "created_at": int(time.time() * 1000),
    }
    r = requests.post(
        f"{ES_URL}/{USERS_INDEX}/_doc?refresh=wait_for",
        auth=ES_AUTH, verify=False, json=doc, timeout=10,
    )
    if r.status_code not in (200, 201):
        sys.exit(f"[fail] create admin: {r.status_code} {r.text[:200]}")
    print(f"[ok] created admin: {email}")


def backfill_chat_owner(email: str) -> None:
    r = requests.post(
        f"{ES_URL}/{CHATS_INDEX}/_update_by_query?refresh=wait_for&conflicts=proceed",
        auth=ES_AUTH, verify=False,
        json={
            "script": {"source": "ctx._source.owner = params.o", "params": {"o": email}},
            "query": {"bool": {"must_not": [{"exists": {"field": "owner"}}]}},
        },
        timeout=60,
    )
    if r.status_code != 200:
        sys.exit(f"[fail] backfill: {r.status_code} {r.text[:200]}")
    body = r.json()
    print(f"[ok] backfilled owner={email} on {body.get('updated', 0)} chat(s)")


if __name__ == "__main__":
    email    = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    name     = os.environ.get("ADMIN_NAME", "Admin")
    if not email or not password:
        sys.exit("set ADMIN_EMAIL and ADMIN_PASSWORD env vars")
    ensure_admin(email, password, name)
    backfill_chat_owner(email)
    print("done")
