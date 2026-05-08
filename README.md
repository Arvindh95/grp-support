# grp-support

GRP Support AI — Streamlit chat over GRP manuals, RFS tickets, scripts, code.

## Layout

| File | Role | Runs as |
|---|---|---|
| `grp_chat.py` | Streamlit frontend | `grp-chat.service` → `:8501` (nginx `:8081` w/ basic auth) |
| `api_server.py` | FastAPI backend (Claude-as-agent + ES/Ollama) | `grp-api.service` → `:8000` |
| `load_manuals.py` | One-off loader: chunk + embed manuals into `grp-manuals` index | manual run |
| `load_rfs_embed.py` | One-off loader: RFS tickets `.xls` → monthly indices | manual run |

## VPS deploy

Working tree on VPS:
- `/opt/grp-chat/grp_chat.py` (frontend)
- `/home/claudeuser/api_server.py` (backend)

Backend depends on Elasticsearch (`:9200`), Ollama (`:11434`, `bge-m3`), Claude CLI, image host (`:8080` → `/opt/grp-manuals/Doc-Images`). `ES_PASSWORD` from env.

## Deps

```
pip install -r requirements.txt
```
