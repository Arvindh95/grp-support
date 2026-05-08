"""
GRP Support AI Chat — Streamlit Frontend
Tabs: Chat | Image Manager | Upload Documents
Run: streamlit run grp_chat.py
"""

import streamlit as st
import streamlit.components.v1 as components
import requests, urllib3, re, json
import pandas as pd

urllib3.disable_warnings()

API_URL = "http://127.0.0.1:8000"

ALL_INDICES = {
    "grp-manuals":          "GRP System Manuals",
    "grp-scripts":          "GRP SQL Fix Scripts",
    "grp-code":             "GRP Code Files",
    "rfs-tickets-jan-2025": "RFS Tickets — Jan 2025",
    "rfs-tickets-feb-2025": "RFS Tickets — Feb 2025",
    "rfs-tickets-mar-2025": "RFS Tickets — Mar 2025",
}

SAMPLE_QUESTIONS = [
    "How to register a vendor in Account Payable?",
    "Steps to process payroll pay run",
    "How to do bank reconciliation?",
    "Common issues with AP301000 screen",
    "Find similar tickets about payroll problems",
    "How to close financial period in Fixed Asset?",
    "High priority tickets in January 2025",
    "How to process a purchase order?",
]

st.set_page_config(page_title="GRP Support AI", page_icon="🤖", layout="wide")


# ── Helpers ────────────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'[\ue000-\uf8ff\u25a0-\u25ff\u2190-\u21ff\ufffd\x00-\x08\x0b-\x1f]', ' > ', text)
    text = re.sub(r'(\s*>\s*)+', ' > ', text).strip(' >')
    return text


def api_get(path: str, params: dict = None) -> dict | list | None:
    try:
        r = requests.get(f"{API_URL}{path}", params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def render_sources(sources):
    manuals = [s for s in sources if s["type"] == "manual"]
    tickets = [s for s in sources if s["type"] == "ticket"]
    scripts = [s for s in sources if s["type"] == "script"]

    if manuals:
        st.markdown("**📖 Manual Sections**")
        for src in manuals:
            section = clean_text(src.get("section", ""))
            st.markdown(
                f"&nbsp;&nbsp;• **{src['module']}** › {section}",
                unsafe_allow_html=True
            )
    if scripts:
        st.markdown("**🛠 Fix Scripts**")
        for src in scripts:
            st.markdown(f"&nbsp;&nbsp;• {src.get('section','?')}", unsafe_allow_html=True)
    if tickets:
        st.markdown("**🎫 Similar Tickets**")
        for src in tickets:
            label = src.get("referno") or "?"
            idx_short = src["index"].replace("rfs-tickets-", "")
            st.markdown(
                f"&nbsp;&nbsp;• **{label}** "
                f"<span style='color:grey;font-size:0.8em'>{idx_short}</span>",
                unsafe_allow_html=True
            )


def render_images(images):
    if not images:
        return
    grouped = {}
    for img in images:
        key = f"{clean_text(img['module'])} › {clean_text(img['section'])}"
        grouped.setdefault(key, []).append(img)

    st.markdown("---")
    st.caption("📸 Related Screenshots")
    for section_label, imgs in grouped.items():
        st.markdown(f"<small><b>{section_label}</b></small>", unsafe_allow_html=True)
        for j, img in enumerate(imgs, 1):
            caption = img.get("caption", "")
            try:
                st.image(img["url"], use_container_width=True)
                st.caption(f"**{j}.** {caption}" if caption else f"**{j}.**")
            except Exception:
                st.caption(f"[{j}. unavailable]")


def render_assistant_msg(msg, show_sources, show_images):
    if msg.get("expanded_query") == "clarification_needed":
        st.info(f"❓ {msg['content']}")
        return
    st.markdown(msg["content"])
    if msg.get("context_used"):
        st.caption(f"{msg['context_used']} chunks retrieved")
    if show_sources and msg.get("sources"):
        with st.expander(f"📎 Sources ({len(msg['sources'])})"):
            render_sources(msg["sources"])
    # Screenshots render inline inside msg["content"] via markdown image refs.
    # Cluster fallback below shows only images Claude did NOT already embed inline.
    if show_images and msg.get("images"):
        inlined = set(re.findall(r'!\[[^\]]*\]\(([^)]+)\)', msg.get("content", "")))
        leftover = [im for im in msg["images"] if im.get("url") not in inlined]
        if leftover:
            render_images(leftover)


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🤖 GRP Support AI")
    st.caption("Powered by Claude + Elasticsearch")
    st.divider()

    page = st.radio(
        "Section",
        ["💬 Chat", "🖼 Image Manager", "📤 Upload Documents"],
        label_visibility="collapsed",
    )
    st.divider()

    if page == "💬 Chat":
        st.subheader("💬 Chats")

        if st.button("+ New chat", use_container_width=True, key="new_chat_btn"):
            st.session_state.current_chat_id    = None
            st.session_state.current_chat_title = None
            st.session_state.messages           = []
            st.session_state.editing_idx        = None
            st.rerun()

        try:
            chats = requests.get(f"{API_URL}/chats", timeout=5).json()
        except Exception:
            chats = []

        # On the very first render (key never set), auto-load most recent chat for convenience.
        # Once the user clicks + New chat, current_chat_id becomes None and we keep an empty slate.
        if "current_chat_id" not in st.session_state:
            if chats:
                cid0 = chats[0]["id"]
                try:
                    full = requests.get(f"{API_URL}/chats/{cid0}", timeout=5).json()
                    st.session_state.current_chat_id    = cid0
                    st.session_state.current_chat_title = full.get("title", "New chat")
                    st.session_state.messages           = full.get("messages", [])
                except Exception:
                    st.session_state.current_chat_id    = None
                    st.session_state.current_chat_title = None
                    st.session_state.messages           = []
            else:
                st.session_state.current_chat_id    = None
                st.session_state.current_chat_title = None
                st.session_state.messages           = []
        if "messages" not in st.session_state:
            st.session_state.messages = []

        if chats:
            list_h = min(300, 56 * len(chats) + 8)
            with st.container(height=list_h, border=False):
                for c in chats:
                    cid = c["id"]
                    title = c.get("title") or "New chat"
                    if len(title) > 38:
                        title = title[:38] + "…"
                    is_active = cid == st.session_state.get("current_chat_id")
                    col_t, col_d = st.columns([5, 1])
                    with col_t:
                        label = ("→ " if is_active else "  ") + title
                        if st.button(label, key=f"chat_load_{cid}", use_container_width=True):
                            if not is_active:
                                try:
                                    full = requests.get(f"{API_URL}/chats/{cid}", timeout=5).json()
                                    st.session_state.current_chat_id    = cid
                                    st.session_state.current_chat_title = full.get("title", "New chat")
                                    st.session_state.messages           = full.get("messages", [])
                                    st.session_state.editing_idx        = None
                                except Exception as _e:
                                    st.error(f"Could not load chat: {_e}")
                                st.rerun()
                    with col_d:
                        if st.button("🗑", key=f"chat_del_{cid}", help="Delete chat"):
                            try:
                                requests.delete(f"{API_URL}/chats/{cid}", timeout=5)
                            except Exception:
                                pass
                            if cid == st.session_state.get("current_chat_id"):
                                st.session_state.pop("current_chat_id", None)
                                st.session_state.pop("current_chat_title", None)
                                st.session_state.messages = []
                            st.rerun()
        st.divider()

    st.subheader("📚 Knowledge Base")
    try:
        r = requests.get(f"{API_URL}/indices", timeout=5)
        counts = r.json()
    except Exception:
        counts = {}

    total_docs = sum(c for c in counts.values() if isinstance(c, int) and c >= 0)
    st.caption(f"{total_docs:,} docs · {len(ALL_INDICES)} indices")

    knowledge_idx = [k for k in ALL_INDICES if not k.startswith("rfs-tickets-")]
    ticket_idx    = [k for k in ALL_INDICES if k.startswith("rfs-tickets-")]

    def _render_idx(idx_list):
        for idx in idx_list:
            count = counts.get(idx, "?")
            st.markdown(f"**{idx}**")
            st.caption(f"{ALL_INDICES[idx]} — {count} docs")

    with st.expander(f"📖 Knowledge ({len(knowledge_idx)})", expanded=False):
        with st.container(height=220, border=False):
            _render_idx(knowledge_idx)

    with st.expander(f"🎫 RFS Tickets ({len(ticket_idx)})", expanded=False):
        with st.container(height=220, border=False):
            _render_idx(ticket_idx)

    st.divider()
    st.subheader("⚙️ Settings")
    show_images  = st.toggle("Show screenshots", value=True)
    show_sources = st.toggle("Show sources", value=True)



# ── Tabs ───────────────────────────────────────────────────────────────────────
# Sections rendered conditionally below based on sidebar selection (page).


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CHAT
# ══════════════════════════════════════════════════════════════════════════════
if page == "💬 Chat":
    st.markdown("""
    <style>
    section[data-testid="stMain"] .block-container { max-width: 820px; padding-top: 2rem; padding-bottom: 6rem; }
    [data-testid="stChatMessage"] { padding: 0.8rem 1rem; border-radius: 12px; margin-bottom: 0.8rem; }
    [data-testid="stChatMessage"] p { line-height: 1.55; }
    .empty-state { text-align: center; padding: 3rem 1rem 1rem; }
    .empty-state h1 { font-size: 3rem; margin: 0; }
    .empty-state h2 { font-weight: 500; margin-top: 0.5rem; }
    .empty-state p  { color: rgba(180,180,180,0.85); font-size: 0.9rem; }
    .stButton > button { white-space: normal; height: auto; padding: 0.6rem 0.8rem; text-align: left; }
    .icon-btn .stButton > button, [data-testid="stChatMessage"] .stButton > button, .stChatMessage .stButton > button { background: transparent !important; border: none !important; box-shadow: none !important; padding: 4px 10px !important; min-height: 0 !important; height: auto !important; font-size: 1.1em !important; color: rgba(180,180,180,0.6) !important; border-radius: 6px !important; }
    .icon-btn .stButton > button:hover, [data-testid="stChatMessage"] .stButton > button:hover, .stChatMessage .stButton > button:hover { color: rgba(255,255,255,0.95) !important; background: rgba(255,255,255,0.06) !important; border: none !important; }
    .icon-btn .stButton > button:focus, [data-testid="stChatMessage"] .stButton > button:focus { box-shadow: none !important; outline: none !important; }
    </style>
    """, unsafe_allow_html=True)

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "editing_idx" not in st.session_state:
        st.session_state.editing_idx = None

    USER_AVATAR = "🧑"
    BOT_AVATAR  = "🤖"

    def _trigger_copy(text):
        st.session_state["copy_pending"] = text

    pending = st.session_state.pop("pending_submit", None)

    last_idx = len(st.session_state.messages) - 1
    last_user_idx = max(
        (i for i, m in enumerate(st.session_state.messages) if m["role"] == "user"),
        default=-1,
    )

    for i, msg in enumerate(st.session_state.messages):
        avatar = USER_AVATAR if msg["role"] == "user" else BOT_AVATAR
        with st.chat_message(msg["role"], avatar=avatar):
            if msg["role"] == "user":
                if st.session_state.editing_idx == i:
                    new_content = st.text_area(
                        "Edit message", value=msg["content"], key="edit_ta_" + str(i),
                        label_visibility="collapsed",
                    )
                    c1, c2, _ = st.columns([1, 1, 4])
                    if c1.button("Save", key="edit_save_" + str(i), type="primary"):
                        st.session_state.messages[i]["content"] = new_content
                        st.session_state.messages = st.session_state.messages[: i + 1]
                        st.session_state.editing_idx = None
                        st.session_state.pending_submit = {
                            "text": new_content,
                            "skip_user_append": True,
                            "attached_paths": [],
                            "attached_names": msg.get("attached_names", []),
                        }
                        st.rerun()
                    if c2.button("Cancel", key="edit_cancel_" + str(i)):
                        st.session_state.editing_idx = None
                        st.rerun()
                else:
                    if i == last_user_idx:
                        umsg_col, uedit_col = st.columns([20, 1])
                        with umsg_col:
                            st.markdown(msg["content"])
                            if msg.get("attached_names"):
                                st.caption("Attached: " + ", ".join(msg["attached_names"]))
                        with uedit_col:
                            if st.button("✎", key="edit_btn_" + str(i), help="Edit"):
                                st.session_state.editing_idx = i
                                st.rerun()
                    else:
                        st.markdown(msg["content"])
                        if msg.get("attached_names"):
                            st.caption("Attached: " + ", ".join(msg["attached_names"]))
            else:
                render_assistant_msg(msg, show_sources, show_images)
                ac1, ac2, _ = st.columns([1, 1, 18])
                with ac1:
                    if st.button("⎘", key="copy_" + str(i), help="Copy"):
                        _trigger_copy(msg["content"])
                        st.rerun()
                with ac2:
                    if i == last_idx:
                        if st.button("↻", key="regen_" + str(i), help="Regenerate"):
                            st.session_state.messages.pop()
                            if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
                                last_user = st.session_state.messages[-1]
                                st.session_state.pending_submit = {
                                    "text": last_user["content"],
                                    "skip_user_append": True,
                                    "attached_paths": [],
                                    "attached_names": last_user.get("attached_names", []),
                                }
                            st.rerun()

    if not st.session_state.messages and not pending:
        st.markdown(
            "<div class='empty-state'>"
            "<h1>🤖</h1>"
            "<h2>How can I help with GRP today?</h2>"
            "<p>Ask about procedures, past RFS tickets, fix scripts - or attach a file.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption("Suggested questions")
        cols = st.columns(2)
        for i, q in enumerate(SAMPLE_QUESTIONS[:6]):
            with cols[i % 2]:
                if st.button(q, key="chip_" + str(i), use_container_width=True):
                    st.session_state["prefill"] = q
                    st.rerun()

    prefill = st.session_state.pop("prefill", "")

    user_input = st.chat_input(
        "Ask about GRP system or past RFS tickets...",
        accept_file="multiple",
        file_type=["pdf", "docx", "md", "txt", "csv", "png", "jpg", "jpeg", "webp"],
    )

    text = None
    raw_files = []
    attached_paths_pre = []
    attached_names_pre = []
    skip_user_append = False

    if pending:
        text = pending["text"]
        attached_paths_pre = pending.get("attached_paths", [])
        attached_names_pre = pending.get("attached_names", [])
        skip_user_append = pending.get("skip_user_append", False)
    elif user_input:
        if isinstance(user_input, str):
            text = user_input
        else:
            text = user_input.text
            raw_files = user_input.files or []
    if not text and prefill:
        text = prefill

    if text:
        if raw_files:
            attached_paths, attached_names = [], []
            for f in raw_files:
                try:
                    r = requests.post(
                        f"{API_URL}/upload-chat-file",
                        files={"file": (f.name, f.getvalue(), f.type)},
                        timeout=60,
                    )
                    if r.status_code == 200:
                        d = r.json()
                        attached_paths.append(d["path"])
                        attached_names.append(d["name"])
                except Exception:
                    pass
        else:
            attached_paths = attached_paths_pre
            attached_names = attached_names_pre

        if not skip_user_append:
            st.session_state.messages.append({
                "role": "user", "content": text,
                "attached_names": attached_names,
            })
            with st.chat_message("user", avatar=USER_AVATAR):
                st.markdown(text)
                if attached_names:
                    st.caption("Attached: " + ", ".join(attached_names))

        with st.chat_message("assistant", avatar=BOT_AVATAR):
            with st.spinner("Searching knowledge base..."):
                history = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state.messages[:-1]
                    if m["role"] in ("user", "assistant")
                ][-6:]

                try:
                    resp = requests.post(
                        f"{API_URL}/query",
                        json={"question": text, "top_k": 5,
                              "include_images": show_images, "history": history,
                              "attached_files": attached_paths},
                        timeout=250,
                    )
                    if resp.status_code == 200:
                        data     = resp.json()
                        answer   = data["answer"]
                        images   = data.get("images", [])
                        sources  = data.get("sources", [])
                        ctx      = data.get("context_used", 0)
                        expanded = data.get("expanded_query")
                    else:
                        answer = f"API error {resp.status_code}: {resp.text[:200]}"
                        images, sources, ctx, expanded = [], [], 0, None
                except requests.exceptions.Timeout:
                    answer = "Request timed out - try a simpler question."
                    images, sources, ctx, expanded = [], [], 0, None
                except Exception as e:
                    answer = f"Connection error: {e}"
                    images, sources, ctx, expanded = [], [], 0, None

            if expanded == "clarification_needed":
                st.info(answer)
            else:
                st.markdown(answer)
                if ctx:
                    st.caption(f"{ctx} chunks retrieved")

        st.session_state.messages.append({
            "role": "assistant", "content": answer,
            "images": images, "sources": sources,
            "context_used": ctx, "expanded_query": expanded,
        })

        cid = st.session_state.get("current_chat_id")
        if not cid:
            try:
                r = requests.post(f"{API_URL}/chats", json={}, timeout=5).json()
                cid = r["id"]
                st.session_state.current_chat_id    = cid
                st.session_state.current_chat_title = r["title"]
            except Exception:
                cid = None

        if cid:
            payload = {"messages": st.session_state.messages}
            if st.session_state.get("current_chat_title", "New chat") == "New chat":
                first_user = next(
                    (m["content"] for m in st.session_state.messages if m["role"] == "user"),
                    None,
                )
                if first_user:
                    new_title = first_user[:50] + ("…" if len(first_user) > 50 else "")
                    payload["title"] = new_title
                    st.session_state.current_chat_title = new_title
            try:
                requests.put(f"{API_URL}/chats/{cid}", json=payload, timeout=10)
            except Exception:
                pass
        st.rerun()

    copy_pending = st.session_state.pop("copy_pending", None)
    if copy_pending is not None:
        _safe = json.dumps(copy_pending)
        components.html(
            "<script>(function(){"
            "  var t=" + _safe + ";"
            "  function fb(){var ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';"
            "    document.body.appendChild(ta);ta.focus();ta.select();try{document.execCommand('copy');}catch(e){}document.body.removeChild(ta);}"
            "  if (navigator.clipboard && window.isSecureContext) {navigator.clipboard.writeText(t).catch(fb);} else {fb();}"
            "})();</script>",
            height=0,
        )
        st.toast("Copied")

    if st.session_state.messages:
        components.html(
            "<script>"
            "const doc = window.parent.document;"
            "const target = doc.querySelector('section[data-testid=\"stMain\"]') || doc.scrollingElement || doc.body;"
            "target.scrollTo({top: target.scrollHeight, behavior: 'smooth'});"
            "</script>",
            height=0,
        )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — IMAGE MANAGER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🖼 Image Manager":
    st.header("Manual Image Manager")
    st.caption("Upload or remove screenshots linked to manual sections")

    col_left, col_right = st.columns([1, 2])

    with col_left:
        # Module selector
        modules = api_get("/modules") or []
        if not modules:
            st.warning("Could not load modules from API")
        else:
            selected_module = st.selectbox("Module", modules, key="img_module")

            # Section selector filtered by module
            sections = api_get("/sections", {"module": selected_module}) or []
            if sections:
                selected_section = st.selectbox("Section", sections, key="img_section")

                st.divider()
                st.subheader("Upload New Image")
                uploaded_img = st.file_uploader(
                    "Choose image", type=["png", "jpg", "jpeg", "gif", "webp"],
                    key="img_upload"
                )
                caption_input = st.text_input("Caption (optional)", key="img_caption")

                if st.button("Upload Image", type="primary", disabled=uploaded_img is None):
                    with st.spinner("Uploading..."):
                        try:
                            resp = requests.post(
                                f"{API_URL}/upload-image",
                                files={"file": (uploaded_img.name, uploaded_img.getvalue(),
                                                uploaded_img.type)},
                                data={"module": selected_module,
                                      "section": selected_section,
                                      "caption": caption_input},
                                timeout=30
                            )
                            if resp.status_code == 200:
                                st.success(f"Uploaded: {resp.json()['filename']}")
                                st.rerun()
                            else:
                                st.error(f"Upload failed: {resp.json().get('detail', resp.text[:100])}")
                        except Exception as e:
                            st.error(f"Error: {e}")
            else:
                st.info("No sections found for this module")

    with col_right:
        if modules and sections:
            st.subheader(f"Images in: {clean_text(selected_section)}")
            section_data = api_get("/section-images",
                                   {"module": selected_module, "section": selected_section})

            if section_data and section_data.get("images"):
                imgs = section_data["images"]
                st.caption(f"{len(imgs)} image(s)")
                for img in imgs:
                    img_col, del_col = st.columns([4, 1])
                    with img_col:
                        try:
                            st.image(img["url"], use_container_width=True)
                            caption = img.get("caption", "")
                            st.caption(caption if caption else "_no caption_")
                        except Exception:
                            st.caption(f"[{img['filename']} — unavailable]")
                    with del_col:
                        if st.button("🗑", key=f"del_{img['filename']}",
                                     help="Delete this image"):
                            try:
                                resp = requests.delete(
                                    f"{API_URL}/delete-image",
                                    params={"module": selected_module,
                                            "section": selected_section,
                                            "filename": img["filename"]},
                                    timeout=15
                                )
                                if resp.status_code == 200:
                                    st.success("Deleted")
                                    st.rerun()
                                else:
                                    st.error(f"Delete failed: {resp.text[:100]}")
                            except Exception as e:
                                st.error(f"Error: {e}")
            else:
                st.info("No images for this section yet. Upload one on the left.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — UPLOAD DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📤 Upload Documents":
    st.header("Upload & Index Documents")
    st.caption("Add new knowledge to the AI — files are parsed, embedded, and indexed into Elasticsearch")

    doc_type = st.selectbox(
        "Document Type",
        ["Manual (.docx / .md)", "Manual Images (.zip)", "RFS Tickets (.xlsx / .csv)", "SQL Script (.txt)", "Code (.py / .cs / .sql)"],
        key="doc_type_select"
    )

    st.divider()

    # ── MANUAL ──────────────────────────────────────────────────────────────
    if doc_type == "Manual (.docx / .md)":
        st.subheader("Upload Manual (.docx or .md)")

        manual_mode = st.radio(
            "Upload mode",
            ["Single (preview & edit sections)", "Bulk (auto-index all, no preview)"],
            key="manual_mode",
            horizontal=True,
        )

        # ── BULK MODE ────────────────────────────────────────────────────────
        if manual_mode.startswith("Bulk"):
            st.caption("Each file is auto-chunked by headings, module name auto-detected from filename. No preview.")
            bulk_files = st.file_uploader(
                "Upload .docx or .md files",
                type=["docx", "md"],
                accept_multiple_files=True,
                key="manual_bulk_files",
            )
            if bulk_files and st.button(f"📦 Index {len(bulk_files)} file(s)", type="primary", key="manual_bulk_go"):
                progress = st.progress(0.0)
                status = st.empty()
                summary = []
                for i, f in enumerate(bulk_files, 1):
                    status.write(f"[{i}/{len(bulk_files)}] {f.name}")
                    meta = {"confirm": True, "module": ""}
                    try:
                        resp = requests.post(
                            f"{API_URL}/upload-document",
                            files={"file": (f.name, f.getvalue(), "text/plain")},
                            data={"doc_type": "manual", "metadata": json.dumps(meta)},
                            timeout=600,
                        )
                        if resp.status_code == 200:
                            r = resp.json()
                            summary.append(f"✅ {f.name}: {r.get('chunks_indexed', 0)} chunks (errors: {r.get('errors', 0)})")
                        else:
                            summary.append(f"❌ {f.name}: {resp.json().get('detail', resp.text[:200])}")
                    except Exception as e:
                        summary.append(f"❌ {f.name}: {e}")
                    progress.progress(i / len(bulk_files))
                status.empty()
                st.success(f"Done. {len(bulk_files)} file(s) processed.")
                for line in summary:
                    st.write(line)

        # ── SINGLE MODE (existing flow) ──────────────────────────────────────
        else:
            st.caption("File will be chunked by headings. Review sections before indexing.")

            module_override = st.text_input(
                "Module Name Override",
                placeholder="e.g. Account Payable — leave blank to auto-detect from filename",
                key="manual_module"
            )
            manual_file = st.file_uploader("Upload .docx or .md file", type=["docx", "md"], key="manual_file")

            if manual_file:
                if st.button("Preview Sections", key="manual_preview"):
                    with st.spinner("Parsing sections..."):
                        meta = {"confirm": False, "module": module_override or ""}
                        try:
                            resp = requests.post(
                                f"{API_URL}/upload-document",
                                files={"file": (manual_file.name, manual_file.getvalue(), "text/plain")},
                                data={"doc_type": "manual", "metadata": json.dumps(meta)},
                                timeout=30
                            )
                            if resp.status_code == 200:
                                result = resp.json()
                                st.session_state["manual_preview_data"] = result
                                st.session_state["manual_file_bytes"] = manual_file.getvalue()
                                st.session_state["manual_filename"]   = manual_file.name
                            else:
                                st.error(f"Error: {resp.json().get('detail', resp.text[:200])}")
                        except Exception as e:
                            st.error(f"Error: {e}")

            if st.session_state.get("manual_preview_data"):
                result = st.session_state["manual_preview_data"]
                st.success(f"Found {result['chunks']} sections in module: **{result['module']}**")

                df = pd.DataFrame(result["preview"])
                edited_df = st.data_editor(
                    df[["original_section", "section", "content_preview", "image_count"]],
                    column_config={
                        "original_section": st.column_config.TextColumn("Original", disabled=True),
                        "section":          st.column_config.TextColumn("Section Name (editable)"),
                        "content_preview":  st.column_config.TextColumn("Preview", disabled=True),
                        "image_count":      st.column_config.NumberColumn("Images", disabled=True),
                    },
                    use_container_width=True,
                    key="manual_editor"
                )

                col1, col2 = st.columns([1, 4])
                with col1:
                    if st.button("✅ Confirm & Index", type="primary", key="manual_confirm"):
                        overrides = {
                            row["original_section"]: row["section"]
                            for _, row in edited_df.iterrows()
                            if row["original_section"] != row["section"]
                        }
                        meta = {
                            "confirm": True,
                            "module": module_override or result["module"],
                            "overrides": overrides
                        }
                        with st.spinner("Embedding and indexing..."):
                            try:
                                resp = requests.post(
                                    f"{API_URL}/upload-document",
                                    files={"file": (
                                        st.session_state["manual_filename"],
                                        st.session_state["manual_file_bytes"],
                                        "text/plain"
                                    )},
                                    data={"doc_type": "manual", "metadata": json.dumps(meta)},
                                    timeout=300
                                )
                                if resp.status_code == 200:
                                    r = resp.json()
                                    st.success(f"Indexed {r['chunks_indexed']} chunks into grp-manuals. Errors: {r['errors']}")
                                    del st.session_state["manual_preview_data"]
                                else:
                                    st.error(f"Error: {resp.json().get('detail', resp.text[:200])}")
                            except Exception as e:
                                st.error(f"Error: {e}")
                with col2:
                    if st.button("✖ Cancel", key="manual_cancel"):
                        del st.session_state["manual_preview_data"]
                        st.rerun()

    # ── MANUAL IMAGES (ZIP) ─────────────────────────────────────────────────
    elif doc_type == "Manual Images (.zip)":
        st.subheader("Bulk Upload Manual Screenshots")
        st.caption(
            "Zip your local Images/ folder (with per-module subfolders inside) and upload here. "
            "Files extract to /opt/grp-manuals/Doc-Images/ preserving folders. Re-upload overwrites existing."
        )
        zip_file = st.file_uploader("Upload images .zip", type=["zip"], key="img_zip_file")
        if zip_file:
            st.info(f"Selected: {zip_file.name} — {zip_file.size / 1024 / 1024:.1f} MB")
            if st.button("📦 Extract & Upload", type="primary", key="img_zip_go"):
                with st.spinner("Uploading and extracting..."):
                    try:
                        resp = requests.post(
                            f"{API_URL}/upload-images-zip",
                            files={"file": (zip_file.name, zip_file.getvalue(), "application/zip")},
                            timeout=600,
                        )
                        if resp.status_code == 200:
                            r = resp.json()
                            st.success(
                                f"Extracted {r['extracted']} image(s) → {r['img_dir']}. "
                                f"Skipped: {r['skipped']}."
                            )
                            if r.get("skipped_examples"):
                                with st.expander("Skipped files"):
                                    for name in r["skipped_examples"]:
                                        st.code(name)
                        else:
                            st.error(f"Error: {resp.json().get('detail', resp.text[:200])}")
                    except Exception as e:
                        st.error(f"Error: {e}")

    # ── RFS TICKETS ──────────────────────────────────────────────────────────
    elif doc_type == "RFS Tickets (.xlsx / .csv)":
        st.subheader("Upload RFS Ticket Export")
        st.caption("Month auto-detected from ticket timestamps. Override if needed.")

        rfs_index_override = st.selectbox(
            "Target Index (auto-detect or override)",
            ["Auto-detect"] + list(ALL_INDICES.keys())[3:],
            key="rfs_index"
        )
        rfs_file = st.file_uploader("Upload file", type=["xlsx", "xls", "csv"], key="rfs_file")

        if rfs_file and st.button("Upload & Index", type="primary", key="rfs_upload"):
            meta = {}
            if rfs_index_override != "Auto-detect":
                meta["index"] = rfs_index_override
            with st.spinner("Parsing, embedding, indexing... (may take a few minutes)"):
                try:
                    resp = requests.post(
                        f"{API_URL}/upload-document",
                        files={"file": (rfs_file.name, rfs_file.getvalue(),
                                        "application/octet-stream")},
                        data={"doc_type": "rfs", "metadata": json.dumps(meta)},
                        timeout=600
                    )
                    if resp.status_code == 200:
                        r = resp.json()
                        idx = r["index"]
                        month = r.get("detected_month")
                        month_str = f" (detected month: {month})" if month else ""
                        st.success(f"Indexed {r['chunks_indexed']} tickets into **{idx}**{month_str}. Errors: {r['errors']}")
                    else:
                        st.error(f"Error: {resp.json().get('detail', resp.text[:200])}")
                except Exception as e:
                    st.error(f"Error: {e}")

    # ── SQL SCRIPT ───────────────────────────────────────────────────────────
    elif doc_type == "SQL Script (.txt)":
        st.subheader("Upload SQL Fix Script")

        script_purpose = st.text_input(
            "Script Purpose",
            placeholder="e.g. Fix locked pay run status for CompanyID",
            key="script_purpose"
        )
        script_file = st.file_uploader("Upload .txt file", type=["txt"], key="script_file")

        if script_file and st.button("Upload & Index", type="primary", key="script_upload"):
            if not script_purpose.strip():
                st.warning("Please enter a purpose description")
            else:
                meta = {"purpose": script_purpose}
                with st.spinner("Embedding and indexing..."):
                    try:
                        resp = requests.post(
                            f"{API_URL}/upload-document",
                            files={"file": (script_file.name, script_file.getvalue(), "text/plain")},
                            data={"doc_type": "script", "metadata": json.dumps(meta)},
                            timeout=60
                        )
                        if resp.status_code == 200:
                            r = resp.json()
                            st.success(f"Indexed into **{r['index']}**")
                        else:
                            st.error(f"Error: {resp.json().get('detail', resp.text[:200])}")
                    except Exception as e:
                        st.error(f"Error: {e}")

    # ── CODE ─────────────────────────────────────────────────────────────────
    elif doc_type == "Code (.py / .cs / .sql)":
        st.subheader("Upload Code File")
        st.caption("Indexed into grp-code index")

        code_purpose = st.text_input(
            "Code Description / Purpose",
            placeholder="e.g. Payroll EFT generation script, AP batch payment processor",
            key="code_purpose"
        )
        code_file = st.file_uploader("Upload code file", type=["py", "cs", "sql"], key="code_file")

        if code_file:
            ext = code_file.name.rsplit(".", 1)[-1].lower()
            lang_names = {"py": "Python", "cs": "C#", "sql": "SQL"}
            st.caption(f"Detected language: **{lang_names.get(ext, ext)}**")

        if code_file and st.button("Upload & Index", type="primary", key="code_upload"):
            if not code_purpose.strip():
                st.warning("Please enter a description")
            else:
                meta = {"purpose": code_purpose}
                with st.spinner("Embedding and indexing..."):
                    try:
                        resp = requests.post(
                            f"{API_URL}/upload-document",
                            files={"file": (code_file.name, code_file.getvalue(), "text/plain")},
                            data={"doc_type": "code", "metadata": json.dumps(meta)},
                            timeout=60
                        )
                        if resp.status_code == 200:
                            r = resp.json()
                            st.success(f"Indexed into **{r['index']}** (language: {ext})")
                        else:
                            st.error(f"Error: {resp.json().get('detail', resp.text[:200])}")
                    except Exception as e:
                        st.error(f"Error: {e}")

    # ── DELETE CONTENT ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("🗑 Delete Content")

    del_tab_file, del_tab_index = st.tabs(["Delete Uploaded File", "Delete Entire Index"])

    # ── Delete by source file ────────────────────────────────────────────────
    with del_tab_file:
        st.caption("Remove a specific uploaded file and all its chunks from an index.")

        del_file_index = st.selectbox(
            "Index", list(ALL_INDICES.keys()), key="del_file_index"
        )

        # Load files in that index
        if st.button("Load Files", key="load_files_btn"):
            files = api_get(f"/index-files/{del_file_index}") or []
            st.session_state["del_file_list"] = files

        file_list = st.session_state.get("del_file_list", [])
        if file_list:
            file_options = [f"{f['source_file']}  ({f['doc_count']} chunks)" for f in file_list]
            selected_file_label = st.selectbox("Select file", file_options, key="del_file_select")
            selected_file = file_list[file_options.index(selected_file_label)]["source_file"]

            confirmed_file = st.checkbox(
                f'Delete all chunks from **{selected_file}**',
                key="del_file_confirm"
            )
            if st.button("Delete File", type="primary",
                         disabled=not confirmed_file, key="del_file_btn"):
                try:
                    resp = requests.delete(
                        f"{API_URL}/delete-file",
                        params={"index_name": del_file_index, "source_file": selected_file},
                        timeout=30
                    )
                    if resp.status_code == 200:
                        r = resp.json()
                        st.success(f"Deleted {r['deleted']} chunks from **{selected_file}**")
                        st.session_state.pop("del_file_list", None)
                        st.rerun()
                    else:
                        st.error(f"Error: {resp.json().get('detail', resp.text[:100])}")
                except Exception as e:
                    st.error(f"Error: {e}")
        elif st.session_state.get("del_file_list") is not None:
            st.info("No uploaded files tracked in this index (may have been loaded before source_file tracking was added).")

    # ── Delete entire index ──────────────────────────────────────────────────
    with del_tab_index:
        st.caption("Permanently removes the entire index and all its data. Cannot be undone.")

        del_index = st.selectbox(
            "Select index", list(ALL_INDICES.keys()), key="del_index_select"
        )
        confirmed_idx = st.checkbox(
            f'I understand this permanently deletes ALL data in **{del_index}**',
            key="del_index_confirm"
        )
        if st.button("Delete Entire Index", type="primary",
                     disabled=not confirmed_idx, key="del_index_btn"):
            try:
                resp = requests.delete(f"{API_URL}/delete-index/{del_index}", timeout=15)
                if resp.status_code == 200:
                    st.success(f"Index **{del_index}** deleted.")
                    st.rerun()
                else:
                    st.error(f"Error: {resp.json().get('detail', resp.text[:100])}")
            except Exception as e:
                st.error(f"Error: {e}")
